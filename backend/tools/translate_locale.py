"""Fill missing keys in a frontend locale bundle using the LLM.

Reads frontend/messages/en.json as canonical, walks the tree, sends
missing leaves to the configured LLM in batches, writes the completed
bundle back. Idempotent — re-running only touches keys that are still
missing (use ``--force`` to retranslate everything).

What it preserves
-----------------
- Exact nested structure (object shape is cloned from en.json).
- Placeholder tokens: {count}, {name}, {due}, {priority}, etc. — the
  LLM is instructed to keep these verbatim and post-validation rejects
  outputs that lost one.
- Brand names that are intentionally identical across locales: any key
  whose English value matches one of the BRAND_TOKENS list (VidSOPEngine,
  SOPs, SOP AI, etc.) is copied verbatim, not translated.

Provider
--------
Uses the standard ``app.services.llm`` router, which picks Groq /
Together / OpenRouter based on which API key is configured. The model
defaults to ``settings.sop_translation_model`` (Llama-3.3-70B-Instruct
on Together) — the same model the runtime SOP translator uses.

Usage
-----
    cd backend
    # set whichever provider key your stack uses (TOGETHER_API_KEY,
    # GROQ_API_KEY, or OPENROUTER_API_KEY) — the router picks
    # automatically from the model name.
    python -m tools.translate_locale mr             # fill only missing keys
    python -m tools.translate_locale mr --dry-run   # print stats; don't write
    python -m tools.translate_locale mr --force     # retranslate all keys
    python -m tools.translate_locale mr --batch 30  # tune throughput
    python -m tools.translate_locale mr --model meta-llama/Llama-3.3-70B-Instruct-Turbo

Adding a brand-new language: add a partial messages/<code>.json on the
frontend (can be just {}) and run this tool. The tool reads the
language label from app.core.languages so the LLM sees "Tamil" /
"Marathi" / "Bengali" etc. — no script edits needed.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.core.languages import (  # noqa: E402
    SUPPORTED_LANGUAGES,
    language_label,
    normalize_language,
    DEFAULT_LANGUAGE,
)
from app.services.llm import get_provider  # noqa: E402

logger = logging.getLogger(__name__)

# Strings we never translate — they're brand names, acronyms, code-like
# identifiers, or already-internationalised. Compared case-sensitively
# against the English value of a key. Anything else gets translated.
BRAND_TOKENS = {
    "VidSOPEngine",
    "SOP AI",
    "SOPs",
    "SOP",
    "AI",
    "API",
    "URL",
    "QR",
    "PDF",
    "OCR",
    "JSON",
    "ID",
    "OK",
    "v1.0",
    "EN",
}

# Match {placeholder} or {count1} tokens. The LLM tries to translate
# these in some languages; we re-validate post-hoc.
_PLACEHOLDER_RE = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")

# Frontend messages directory, relative to the backend root.
FRONTEND_MESSAGES_DIR = ROOT.parent / "frontend" / "messages"


def collect_paths(obj: Any, prefix: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], str]]:
    """Flatten a nested dict into (path_tuple, value) pairs for string leaves."""
    out: list[tuple[tuple[str, ...], str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.startswith("_"):
                # Convention: leading underscore = developer note / metadata.
                continue
            out.extend(collect_paths(v, prefix + (k,)))
    elif isinstance(obj, str):
        out.append((prefix, obj))
    return out


def get_at(obj: Any, path: tuple[str, ...]) -> Any:
    cur = obj
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def set_at(obj: dict, path: tuple[str, ...], value: str) -> None:
    cur = obj
    for p in path[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[path[-1]] = value


def placeholders(text: str) -> set[str]:
    return set(_PLACEHOLDER_RE.findall(text))


def _translate_batch(
    pairs: list[tuple[tuple[str, ...], str]],
    target_label: str,
    model: Optional[str],
) -> dict[str, str]:
    """One LLM call → dict mapping path-as-string → translated value.

    Skips any output that lost or invented placeholders and any output
    that's identical to the input (likely a refusal or echo).
    """
    numbered = []
    for i, (path, val) in enumerate(pairs):
        numbered.append({"id": i, "key": ".".join(path), "en": val})

    prompt = (
        f"You translate UI strings from English to {target_label}.\n"
        "Return ONLY a JSON object mapping the input id (as string) to the "
        "translated string, like {\"0\": \"...\", \"1\": \"...\"} — no commentary, "
        "no markdown fences.\n\n"
        "Rules:\n"
        "- Keep these EXACTLY as-is: any token like {name}, {count}, {due}, "
        "{priority}, etc. — they are variable placeholders.\n"
        "- Keep brand names exactly: VidSOPEngine, SOP, SOP AI, AI, API, QR, PDF.\n"
        "- Keep length similar to the source.\n"
        f"- Output natural, conversational {target_label}, not literal "
        "word-for-word translation.\n\n"
        "Strings to translate:\n"
        + json.dumps(numbered, ensure_ascii=False, indent=2)
    )

    provider = get_provider("router")
    resp = provider.chat(
        prompt,
        model=model or settings.sop_translation_model,
        timeout=90,
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    raw = (resp.text or "").strip()
    if raw.startswith("```"):
        # Some providers wrap JSON in fences even with response_format set.
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Bad JSON from LLM: {exc}\n{raw[:500]}")

    out: dict[str, str] = {}
    for i, (path, en_value) in enumerate(pairs):
        raw_val = parsed.get(str(i)) or parsed.get(i)
        if not isinstance(raw_val, str):
            logger.warning("[locale] missing id=%s key=%s — keeping English", i, ".".join(path))
            out[".".join(path)] = en_value
            continue

        # Validation 1: placeholders preserved exactly
        expected = placeholders(en_value)
        got = placeholders(raw_val)
        if expected != got:
            logger.warning(
                "[locale] placeholder mismatch on %s (expected %s, got %s) — keeping English",
                ".".join(path), sorted(expected), sorted(got),
            )
            out[".".join(path)] = en_value
            continue

        # Validation 2: refusal / echo
        stripped = raw_val.strip()
        if not stripped:
            out[".".join(path)] = en_value
            continue

        out[".".join(path)] = stripped
    return out


def run(
    target_code: str,
    *,
    force: bool = False,
    dry_run: bool = False,
    batch_size: int = 30,
    model: Optional[str] = None,
) -> dict:
    target_code = normalize_language(target_code)
    if target_code == DEFAULT_LANGUAGE:
        raise SystemExit("Target language must not be the default (English).")
    if target_code not in SUPPORTED_LANGUAGES:
        raise SystemExit(
            f"Unknown language code '{target_code}'. "
            f"Add it to app/core/languages.py first. Known: {sorted(SUPPORTED_LANGUAGES)}"
        )
    target_label = language_label(target_code)

    en_path = FRONTEND_MESSAGES_DIR / "en.json"
    target_path = FRONTEND_MESSAGES_DIR / f"{target_code}.json"
    if not en_path.exists():
        raise SystemExit(f"Missing canonical bundle: {en_path}")

    en_bundle = json.loads(en_path.read_text(encoding="utf-8"))
    if target_path.exists():
        target_bundle = json.loads(target_path.read_text(encoding="utf-8"))
    else:
        target_bundle = {}

    en_pairs = collect_paths(en_bundle)
    total = len(en_pairs)

    # What needs translation?
    work: list[tuple[tuple[str, ...], str]] = []
    skipped_brand = 0
    skipped_existing = 0
    for path, en_value in en_pairs:
        # Brand tokens & code-like identifiers: copy verbatim, no LLM call.
        if en_value in BRAND_TOKENS:
            existing = get_at(target_bundle, path)
            if existing != en_value:
                set_at(target_bundle, path, en_value)
            skipped_brand += 1
            continue
        # Already present (and not forced): skip
        existing = get_at(target_bundle, path)
        if not force and isinstance(existing, str) and existing.strip():
            skipped_existing += 1
            continue
        work.append((path, en_value))

    print(f"[{target_code}] canonical={total}  brand-copy={skipped_brand}  already-translated={skipped_existing}  to-translate={len(work)}")

    if dry_run:
        return {"to_translate": len(work), "skipped_brand": skipped_brand, "skipped_existing": skipped_existing}

    if not work:
        print(f"[{target_code}] nothing to do.")
        return {"to_translate": 0, "skipped_brand": skipped_brand, "skipped_existing": skipped_existing}

    # Batch & call
    translated_count = 0
    for i in range(0, len(work), batch_size):
        chunk = work[i:i + batch_size]
        print(f"[{target_code}] batch {i // batch_size + 1}/{(len(work) + batch_size - 1) // batch_size} ({len(chunk)} keys)")
        try:
            results = _translate_batch(chunk, target_label, model)
        except Exception as exc:
            logger.error("[locale] batch failed: %s", exc)
            continue
        for path, en_value in chunk:
            translated = results.get(".".join(path))
            if isinstance(translated, str) and translated != en_value:
                set_at(target_bundle, path, translated)
                translated_count += 1
            elif not isinstance(get_at(target_bundle, path), str):
                # Fallback: keep English so the JSON is structurally complete.
                set_at(target_bundle, path, en_value)

    # Preserve developer-note keys (starting with _) that already exist
    # in the target bundle — they're our own comments.

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps(target_bundle, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[{target_code}] wrote {target_path}  translated={translated_count}")
    return {
        "to_translate": len(work),
        "translated": translated_count,
        "skipped_brand": skipped_brand,
        "skipped_existing": skipped_existing,
        "path": str(target_path),
    }


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("language", help="Target language code (e.g. mr, hi, ta, bn)")
    parser.add_argument("--force", action="store_true",
                        help="Re-translate keys even when they already exist in the target bundle.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change; don't touch the file.")
    parser.add_argument("--batch", type=int, default=30,
                        help="Keys per LLM call (default 30).")
    parser.add_argument("--model", default=None,
                        help="Override translation model (default: settings.sop_translation_model).")
    args = parser.parse_args()
    run(
        args.language,
        force=args.force,
        dry_run=args.dry_run,
        batch_size=args.batch,
        model=args.model,
    )


if __name__ == "__main__":
    main()
