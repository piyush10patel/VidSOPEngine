"""Translate a markdown document to a target language, preserving structure.

Unlike ``translate_locale`` (which handles flat JSON locale bundles),
this tool understands markdown. It splits the input on structural
boundaries, sends only the prose blocks to the LLM, and reassembles
the document so the translated version is byte-shape-identical to the
source apart from natural-language content.

What it preserves verbatim
--------------------------
- Fenced code blocks (``` ... ```) — translation would break them.
- Tables: each cell is translated individually so the pipe alignment
  stays intact. Brand tokens inside cells are left alone.
- Heading levels (#, ##, ...) and inline anchor IDs.
- Inline ``code`` spans, links ``[text](url)``, image refs, and
  emphasis markers stay in place — the translator instructions tell
  the LLM not to alter them.
- Brand tokens (VidSOPEngine, SOP, SOP AI, AI, API, QR, PDF, …) are
  left as-is via the same allowlist used by translate_locale.

Usage
-----
    cd backend
    python -m tools.translate_markdown ../docs/user-guide.md mr
    python -m tools.translate_markdown ../docs/user-guide.md hi --dry-run
    python -m tools.translate_markdown ../docs/user-guide.md mr --out ../docs/user-guide.mr.md

If ``--out`` is omitted, the output filename is derived by inserting
the language code before the extension: ``user-guide.md`` →
``user-guide.mr.md``.

Idempotent caveat: unlike translate_locale, this tool overwrites the
target file on each run. The source markdown is the source of truth;
the translated files are derived artifacts.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from pathlib import Path
from typing import Optional

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
from tools.translate_locale import BRAND_TOKENS  # noqa: E402

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s\-:|]+\|?\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _is_brand_or_trivial(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if stripped in BRAND_TOKENS:
        return True
    # Pure punctuation / numeric / single-token chrome stays as-is.
    if re.fullmatch(r"[\d\W_]+", stripped):
        return True
    return False


def _split_blocks(text: str) -> list[tuple[str, str]]:
    """Walk the markdown line-by-line, emitting (kind, content) blocks.

    Kinds:
      "code"      — fenced code block, emit verbatim
      "table"     — pipe table, translate cell-by-cell
      "heading"   — '#'+ line; translate the title only
      "prose"     — paragraph (one or more contiguous non-empty lines)
      "blank"     — empty line (preserved for spacing)
    """
    lines = text.splitlines()
    blocks: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Fenced code block — read until the closing fence.
        if _FENCE_RE.match(line):
            start = i
            i += 1
            while i < len(lines) and not _FENCE_RE.match(lines[i]):
                i += 1
            # Include the closing fence if present
            if i < len(lines):
                i += 1
            blocks.append(("code", "\n".join(lines[start:i])))
            continue
        # Table — two-or-more consecutive pipe lines starting with a
        # header row followed by a separator. Walk until the table ends.
        if _TABLE_ROW_RE.match(line) and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
            start = i
            i += 1
            while i < len(lines) and _TABLE_ROW_RE.match(lines[i]):
                i += 1
            blocks.append(("table", "\n".join(lines[start:i])))
            continue
        # Heading
        if _HEADING_RE.match(line):
            blocks.append(("heading", line))
            i += 1
            continue
        # Blank
        if not line.strip():
            blocks.append(("blank", ""))
            i += 1
            continue
        # Prose paragraph — collect contiguous non-empty, non-table,
        # non-heading, non-fence lines.
        start = i
        while (
            i < len(lines)
            and lines[i].strip()
            and not _FENCE_RE.match(lines[i])
            and not _HEADING_RE.match(lines[i])
            and not (_TABLE_ROW_RE.match(lines[i]) and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]))
        ):
            i += 1
        blocks.append(("prose", "\n".join(lines[start:i])))
    return blocks


def _translate_text(
    text: str,
    *,
    target_label: str,
    model: Optional[str],
    timeout: int = 60,
) -> str:
    """Single LLM call. Returns the original on any failure (so a
    partial output is still readable in mixed-language form)."""
    if _is_brand_or_trivial(text):
        return text

    prompt = (
        f"Translate the following markdown content from English to {target_label}.\n\n"
        "CRITICAL: Output ONLY the translation. No commentary. No \"Translation:\" "
        "prefix. No code fences or `---` delimiters wrapping the answer. Your "
        "first character should be the first character of the translation.\n\n"
        "Brand and product names — KEEP THESE EXACTLY in Latin script. Do NOT "
        "transliterate or translate them: VidSOPEngine, SOP, SOP AI, AI, API, QR, "
        "PDF, OCR, JSON, ID, OK, EN, R2, Neon, Render, Groq, Vercel, OpenRouter, "
        f"Together, Llama, Qwen, Whisper, DSPy. (Example: if the source says "
        f"\"VidSOPEngine does three things\", your {target_label} must still contain "
        "the literal word VidSOPEngine in Latin letters, not a phonetic spelling.)\n\n"
        "Markdown — preserve EVERY control character: `*`, `_`, `[`, `]`, `(`, "
        "`)`, `<`, `>`, `|`, backticks, leading `-`/`+`/`*` for lists, "
        "indentation, blank lines. Do NOT translate text inside backticks "
        "(`code`), URLs in `[text](url)` (translate the display text only), or "
        "HTML tags.\n\n"
        f"Audience: a non-technical small-business owner reading on a phone. "
        f"Write natural, conversational {target_label}. Avoid formal or "
        "academic vocabulary. Keep length similar to the source.\n\n"
        "Source content:\n"
        f"{text}"
    )

    try:
        provider = get_provider("router")
        resp = provider.chat(
            prompt,
            model=model or settings.sop_translation_model,
            timeout=timeout,
            temperature=0.2,
        )
        translated = (resp.text or "").strip()
        if not translated:
            logger.warning("[md] empty response — keeping original block")
            return text
        # Strip accidental wrapping fences or "Translation:" prefix.
        translated = re.sub(r"^```(?:markdown|md)?\s*\n?|\n?```$", "", translated, flags=re.MULTILINE).strip()
        translated = re.sub(r"^(translation|अनुवाद|भाषांतर)\s*[:\-]\s*", "", translated, flags=re.IGNORECASE).strip()
        # Strip any leading/trailing `---` delimiter lines the LLM may have
        # added (they would be parsed as horizontal rules inside the
        # surrounding block, breaking the layout).
        lines = translated.splitlines()
        while lines and lines[0].strip() in ("---", "***", "___"):
            lines.pop(0)
        while lines and lines[-1].strip() in ("---", "***", "___"):
            lines.pop()
        translated = "\n".join(lines).strip()
        return translated or text
    except Exception as exc:
        logger.warning("[md] block translation failed (%s) — keeping original", exc)
        return text


def _translate_table(
    block: str,
    *,
    target_label: str,
    model: Optional[str],
) -> str:
    """Translate each cell individually so column alignment survives.

    The separator row (---|---|...) is skipped. Brand tokens and empty
    cells pass through unchanged.
    """
    lines = block.splitlines()
    out_lines: list[str] = []
    for line in lines:
        if _TABLE_SEP_RE.match(line):
            out_lines.append(line)
            continue
        # Split on `|`, keeping the leading/trailing empties so the
        # number of pipes is preserved.
        parts = line.split("|")
        translated_parts: list[str] = []
        for cell in parts:
            stripped = cell.strip()
            if not stripped or _is_brand_or_trivial(stripped):
                translated_parts.append(cell)
                continue
            # Preserve original whitespace shape (leading/trailing) so
            # the rendered table doesn't shift columns.
            leading = cell[: len(cell) - len(cell.lstrip())]
            trailing = cell[len(cell.rstrip()):]
            new_text = _translate_text(stripped, target_label=target_label, model=model, timeout=30)
            translated_parts.append(f"{leading}{new_text}{trailing}")
        out_lines.append("|".join(translated_parts))
    return "\n".join(out_lines)


def _translate_heading(
    line: str,
    *,
    target_label: str,
    model: Optional[str],
) -> str:
    m = _HEADING_RE.match(line)
    if not m:
        return line
    hashes, title = m.group(1), m.group(2)
    new_title = _translate_text(title, target_label=target_label, model=model, timeout=30)
    return f"{hashes} {new_title}"


def translate_document(
    text: str,
    *,
    target_label: str,
    model: Optional[str] = None,
    log_prefix: str = "[md]",
    pacing_seconds: float = 1.5,
    existing_text: Optional[str] = None,
) -> str:
    """Translate every prose/heading/table block.

    ``pacing_seconds`` inserts a sleep between LLM calls so we stay under
    Groq's free-tier rate limit (~30 RPM on Llama-3.3-70b). Without
    pacing, the circuit breaker opens mid-run and the back half of the
    document falls back to English.

    ``existing_text`` is the contents of a prior translation run, if any.
    Blocks already translated there (i.e. block content differs from the
    English source) are reused — only English-still blocks are re-sent
    to the LLM. This makes the tool idempotent: re-run until happy.
    """
    blocks = _split_blocks(text)
    existing_blocks: list[tuple[str, str]] = []
    if existing_text:
        existing_blocks = _split_blocks(existing_text)
        if len(existing_blocks) != len(blocks):
            # Shape mismatch — the source has been edited since the prior
            # run. Discard the cache so we don't paste stale content.
            print(f"{log_prefix} existing file's block count differs from source; ignoring cache")
            existing_blocks = []

    out: list[str] = []
    prose_n = sum(1 for kind, _ in blocks if kind == "prose")
    heading_n = sum(1 for kind, _ in blocks if kind == "heading")
    table_n = sum(1 for kind, _ in blocks if kind == "table")
    work = prose_n + heading_n + table_n
    cached_n = 0

    # Precompute which translatable blocks can be reused from cache.
    reuse_mask: list[bool] = []
    for idx, (kind, content) in enumerate(blocks):
        if kind in ("code", "blank") or not existing_blocks:
            reuse_mask.append(False)
            continue
        ex_kind, ex_content = existing_blocks[idx]
        # Reuse iff the existing block has a non-empty translation that
        # actually differs from the English source (i.e. the prior run
        # succeeded for this block).
        if ex_kind == kind and ex_content.strip() and ex_content.strip() != content.strip():
            reuse_mask.append(True)
            cached_n += 1
        else:
            reuse_mask.append(False)

    print(
        f"{log_prefix} blocks: prose={prose_n} headings={heading_n} tables={table_n} "
        f"code/blank={len(blocks) - work} cached={cached_n} to_translate={work - cached_n}"
    )

    done = 0
    for idx, (kind, content) in enumerate(blocks):
        if kind == "code" or kind == "blank":
            out.append(content)
            continue
        if reuse_mask[idx]:
            out.append(existing_blocks[idx][1])
            done += 1
            continue
        if kind == "heading":
            out.append(_translate_heading(content, target_label=target_label, model=model))
        elif kind == "table":
            out.append(_translate_table(content, target_label=target_label, model=model))
        elif kind == "prose":
            out.append(_translate_text(content, target_label=target_label, model=model))
        done += 1
        if done % 10 == 0 or done == work:
            print(f"{log_prefix} processed {done}/{work} blocks")
        if pacing_seconds > 0 and done < work:
            time.sleep(pacing_seconds)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def derive_output_path(src: Path, code: str) -> Path:
    stem = src.stem
    return src.with_name(f"{stem}.{code}{src.suffix}")


def run(
    src_path: str,
    target_code: str,
    *,
    out_path: Optional[str] = None,
    dry_run: bool = False,
    model: Optional[str] = None,
    pacing_seconds: float = 1.5,
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

    src = Path(src_path).resolve()
    if not src.exists():
        raise SystemExit(f"Source file not found: {src}")
    if out_path:
        dest = Path(out_path).resolve()
    else:
        dest = derive_output_path(src, target_code)

    text = src.read_text(encoding="utf-8")
    print(f"[md] {target_code}: source={src.name} ({len(text):,} bytes) -> {dest.name}")

    if dry_run:
        blocks = _split_blocks(text)
        prose_n = sum(1 for kind, _ in blocks if kind == "prose")
        heading_n = sum(1 for kind, _ in blocks if kind == "heading")
        table_n = sum(1 for kind, _ in blocks if kind == "table")
        print(f"[md] dry-run: would translate prose={prose_n} headings={heading_n} tables={table_n}")
        return {"blocks": len(blocks), "prose": prose_n, "headings": heading_n, "tables": table_n}

    existing_text: Optional[str] = None
    if dest.exists():
        existing_text = dest.read_text(encoding="utf-8")
        print(f"[md] found existing translation at {dest.name} -- will reuse blocks that are already translated")

    translated = translate_document(
        text,
        target_label=target_label,
        model=model,
        pacing_seconds=pacing_seconds,
        existing_text=existing_text,
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(translated, encoding="utf-8")
    print(f"[md] wrote {dest}")
    return {"src": str(src), "dest": str(dest), "bytes_in": len(text), "bytes_out": len(translated)}


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("src", help="Path to the source markdown file (e.g. ../docs/user-guide.md)")
    parser.add_argument("language", help="Target language code (e.g. mr, hi, ta)")
    parser.add_argument("--out", default=None, help="Override the output path (default: <stem>.<code>.md sibling).")
    parser.add_argument("--dry-run", action="store_true", help="Show block breakdown; don't call the LLM.")
    parser.add_argument("--model", default=None, help="Override the translation model.")
    parser.add_argument("--pacing", type=float, default=1.5,
                        help="Seconds to sleep between LLM calls (default 1.5 to stay under Groq's ~30 RPM free tier).")
    args = parser.parse_args()
    run(
        args.src,
        args.language,
        out_path=args.out,
        dry_run=args.dry_run,
        model=args.model,
        pacing_seconds=args.pacing,
    )


if __name__ == "__main__":
    main()
