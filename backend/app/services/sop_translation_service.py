"""Language projection helpers for SOP presentation.

The SOP generator remains the source of structure. This service translates
only user-facing operational text and then overlays it back onto the original
schema so media references, confidence, evidence, links, and metadata survive.

Strategy: we collect every translatable string into a flat numbered list,
ask the LLM to translate the list (one line in, one line out), then splice
the translations back onto the schema. This avoids the JSON-mode failures
we hit when asking a model to maintain a nested object shape, and lets us
process big SOPs in fixed-size batches without going over output token
limits.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Iterable

from app.core.config import settings
from app.schemas.sop import SOPSchema
from app.services.llm import get_provider


logger = logging.getLogger(__name__)


from app.core.languages import (  # noqa: E402 — local import to avoid circular at module load
    DEFAULT_LANGUAGE as _LANG_DEFAULT,
    language_label as _lang_label,
    normalize_language as _lang_normalize,
    supported_codes as _lang_codes,
)

# Backwards-compatible aliases — call sites that imported these keep working.
SUPPORTED_SOP_LANGUAGES = set(_lang_codes())
LANGUAGE_LABELS = {code: _lang_label(code) for code in _lang_codes()}

# Devanagari covers Hindi AND Marathi; the script range alone can't tell
# them apart, so language identification falls back to the SOP metadata
# field (output_language). Anything outside this range is treated as not
# in a Devanagari language.
_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")

# Max strings sent to the LLM per call. Smaller batches keep output token
# budgets small and reduce the rate at which the model skips/renumbers items.
_BATCH_SIZE = 6

def _looks_like_identifier(text: str) -> bool:
    """Short identifier-like strings (brands, IDs, measurements, model numbers)
    legitimately stay in Latin script even in a Hindi SOP. A real English
    sentence does not.

    Rule of thumb: ≤3 tokens, ≤24 chars, and no alphabetic token longer than
    8 characters. "Funnel", "M8", "Step 1", "5.0 kg" pass; "Pour the liquid"
    does not.
    """
    t = (text or "").strip()
    if not t or len(t) > 24:
        return False
    tokens = t.split()
    if len(tokens) > 3:
        return False
    for tok in tokens:
        if len(tok) > 8 and tok.replace("-", "").replace("_", "").isalpha():
            return False
    return True


class SOPTranslationError(RuntimeError):
    """Raised when the LLM fails to produce a usable translation."""


def normalize_sop_language(language: str | None) -> str:
    """Normalize UI/API language names to the compact SOP language code.

    Thin wrapper around the central languages registry. Any new language
    added to ``app.core.languages.SUPPORTED_LANGUAGES`` is picked up here
    automatically.
    """
    return _lang_normalize(language) or _LANG_DEFAULT


def sop_language_label(language: str | None) -> str:
    return _lang_label(language)


def sop_output_language(sop: SOPSchema | dict[str, Any]) -> str:
    """Read stored output language metadata from an SOP-like object."""
    if isinstance(sop, SOPSchema):
        metadata = sop.generation_metadata or {}
    else:
        metadata = sop.get("generation_metadata") or {}
    return normalize_sop_language(str(metadata.get("output_language") or "en"))


def _collect_segments(sop: SOPSchema) -> list[tuple[str, str]]:
    """Build a stable, ordered list of (slot_id, text) translatable strings.

    Slot IDs are dotted paths that ``_apply_segments`` uses to splice the
    translations back onto the SOP. Empty strings are skipped.
    """
    items: list[tuple[str, str]] = []

    if sop.title and sop.title.strip():
        items.append(("title", sop.title))
    if sop.description and sop.description.strip():
        items.append(("description", sop.description))
    for i, note in enumerate(sop.notes or []):
        if note and note.strip():
            items.append((f"notes[{i}]", note))
    for i, warn in enumerate(sop.warnings or []):
        if warn and warn.strip():
            items.append((f"warnings[{i}]", warn))
    for i, tool in enumerate(sop.tools_materials or []):
        if tool and tool.strip():
            items.append((f"tools_materials[{i}]", tool))
    for step in sop.steps:
        sid = step.step_number
        if step.title and step.title.strip():
            items.append((f"step[{sid}].title", step.title))
        if step.description and step.description.strip():
            items.append((f"step[{sid}].description", step.description))
        for j, tool in enumerate(step.tools or []):
            if tool and tool.strip():
                items.append((f"step[{sid}].tools[{j}]", tool))
        for j, check in enumerate(step.checks or []):
            if check and check.strip():
                items.append((f"step[{sid}].checks[{j}]", check))
        if step.notes and isinstance(step.notes, str) and step.notes.strip() and step.notes != "null":
            items.append((f"step[{sid}].notes", step.notes))
        if step.warning and isinstance(step.warning, str) and step.warning.strip():
            items.append((f"step[{sid}].warning", step.warning))
    return items


def _apply_segments(sop: SOPSchema, translated: dict[str, str], target_language: str, model_name: str | None) -> SOPSchema:
    """Splice translated strings back onto a copy of the SOP."""
    data = sop.model_dump(mode="json")

    def _put_string(key: str, slot: str) -> None:
        text = translated.get(slot)
        if isinstance(text, str) and text.strip():
            data[key] = text.strip()

    def _put_list_item(key: str, index: int, slot: str) -> None:
        text = translated.get(slot)
        if not isinstance(text, str) or not text.strip():
            return
        arr = data.get(key)
        if isinstance(arr, list) and 0 <= index < len(arr):
            arr[index] = text.strip()

    _put_string("title", "title")
    _put_string("description", "description")
    for i in range(len(data.get("notes") or [])):
        _put_list_item("notes", i, f"notes[{i}]")
    for i in range(len(data.get("warnings") or [])):
        _put_list_item("warnings", i, f"warnings[{i}]")
    for i in range(len(data.get("tools_materials") or [])):
        _put_list_item("tools_materials", i, f"tools_materials[{i}]")

    for step in data.get("steps", []):
        sid = step.get("step_number")
        if sid is None:
            continue
        title_text = translated.get(f"step[{sid}].title")
        if isinstance(title_text, str) and title_text.strip():
            step["title"] = title_text.strip()
        desc_text = translated.get(f"step[{sid}].description")
        if isinstance(desc_text, str) and desc_text.strip():
            step["description"] = desc_text.strip()
        for j in range(len(step.get("tools") or [])):
            text = translated.get(f"step[{sid}].tools[{j}]")
            if isinstance(text, str) and text.strip():
                step["tools"][j] = text.strip()
        for j in range(len(step.get("checks") or [])):
            text = translated.get(f"step[{sid}].checks[{j}]")
            if isinstance(text, str) and text.strip():
                step["checks"][j] = text.strip()
        notes_text = translated.get(f"step[{sid}].notes")
        if isinstance(notes_text, str) and notes_text.strip():
            step["notes"] = notes_text.strip()
        warning_text = translated.get(f"step[{sid}].warning")
        if isinstance(warning_text, str) and warning_text.strip():
            step["warning"] = warning_text.strip()

    metadata = dict(data.get("generation_metadata") or {})
    previous_language = metadata.get("output_language") or metadata.get("translated_to") or "en"
    metadata.update(
        {
            "output_language": target_language,
            "translated_to": target_language,
            "translated_from": normalize_sop_language(str(previous_language)),
            "translation_strategy": "flat_segments_v2",
        }
    )
    if model_name:
        metadata["translation_model"] = model_name
    data["generation_metadata"] = metadata
    return SOPSchema(**data)


def _build_batch_prompt(texts: list[str], target_label: str, target_code: str) -> str:
    """Build a numbered-list prompt for a chunk of strings.

    Numbering is local (1..N within this batch) — the caller maps results
    back to global indices. This avoids the failure mode where the model
    silently renumbers a batch starting from 1 and we drop everything.
    """
    if target_code == "hi":
        script_hint = (
            "Translate every line into natural operational Hindi using the "
            "Devanagari script (हिन्दी). DO NOT output Roman/Latin transliteration."
        )
    else:
        script_hint = "Translate every line into clear, operational English."

    numbered = "\n".join(f"{i + 1}. {text}" for i, text in enumerate(texts))
    return (
        f"You are an industrial translator. {script_hint}\n\n"
        "Rules:\n"
        f"- Output EXACTLY {len(texts)} lines, one translation per input line.\n"
        f"- Number the output lines 1 through {len(texts)} in the same order as the input.\n"
        "- Each output line starts with the line number and a dot, e.g. `1. ...`.\n"
        "- Preserve brand names, product names, identifiers, file names, URLs, and numeric measurements verbatim.\n"
        "- Do not add commentary, headers, or trailing notes.\n"
        "- Do not collapse, merge, or reorder lines. Empty or single-word inputs still produce one output line.\n\n"
        f"Translate the following lines to {target_label}:\n\n"
        f"{numbered}\n"
    )


_NUMBERED_LINE_RE = re.compile(r"^\s*(\d+)\s*[.)\]:]\s*(.*\S)\s*$")


def _parse_numbered_lines(text: str, max_index: int) -> dict[int, str]:
    """Parse `1. text` style lines into a dict keyed by 1-based local index.

    Tolerates ``.``, ``)``, ``]``, and ``:`` delimiters and extra whitespace.
    Lines without a recognized number prefix are stitched onto the previous
    entry to handle translations that contained a newline.
    Indices outside 1..max_index are also treated as continuations rather
    than dropped, so a stray heading doesn't break the rest of the batch.
    """
    if not text:
        return {}
    out: dict[int, str] = {}
    last_idx: int | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        match = _NUMBERED_LINE_RE.match(line)
        if match:
            idx = int(match.group(1))
            value = match.group(2).strip()
            if 1 <= idx <= max_index:
                out[idx] = value
                last_idx = idx
                continue
        if last_idx is not None:
            out[last_idx] = (out[last_idx] + " " + line.strip()).strip()
    return out


def _is_acceptable_translation(original: str, translated: str, target_code: str) -> bool:
    """Heuristic per-segment validity check.

    For Hindi targets, the translation must either contain Devanagari OR be
    a pure brand/identifier/measurement string that legitimately stays in
    Latin script (e.g. ``Funnel``, ``M8``, ``5.0 kg``, ``Step 1``).
    """
    if not isinstance(translated, str) or not translated.strip():
        return False
    if target_code != "hi":
        return True
    if _DEVANAGARI_RE.search(translated):
        return True
    # No Devanagari — only accept if it's a short Latin identifier that
    # likely SHOULD be preserved verbatim from the source.
    return _looks_like_identifier(translated)


async def _llm_chat(prompt: str, model_name: str) -> str:
    provider = get_provider()

    def _invoke() -> str:
        response = provider.chat(prompt, model=model_name, timeout=120, temperature=0)
        return response.text or ""

    return await asyncio.to_thread(_invoke)


async def _translate_single(text: str, target_code: str, model_name: str) -> str | None:
    """Translate one string in isolation; used to repair missed segments."""
    target_label = sop_language_label(target_code)
    if target_code == "hi":
        instruction = (
            "Translate the following text into natural operational Hindi "
            "using Devanagari script (हिन्दी). Preserve brand names, IDs, "
            "file names, URLs, and numeric measurements verbatim. Reply with "
            "ONLY the translated text — no commentary, no quotes, no labels."
        )
    else:
        instruction = (
            "Translate the following text into clear operational English. "
            "Preserve brand names, IDs, file names, URLs, and numeric "
            "measurements verbatim. Reply with ONLY the translated text — "
            "no commentary, no quotes, no labels."
        )
    prompt = f"{instruction}\n\nText to translate to {target_label}:\n{text}"
    raw = (await _llm_chat(prompt, model_name)).strip()
    if raw.startswith(('"', "'")) and raw.endswith(('"', "'")) and len(raw) >= 2:
        raw = raw[1:-1].strip()
    return raw or None


async def _translate_batch(
    texts: list[str],
    target_code: str,
    model_name: str,
) -> dict[int, str]:
    """Translate one batch via the LLM. Returns dict keyed by 1-based local index.

    Runs one retry if the parsed output is missing entries. Any segments
    still missing after the retry are left for the per-line repair pass
    in ``translate_sop_schema``.
    """
    if not texts:
        return {}
    prompt = _build_batch_prompt(texts, sop_language_label(target_code), target_code)
    expected_count = len(texts)

    raw = await _llm_chat(prompt, model_name)
    parsed = _parse_numbered_lines(raw, expected_count)

    missing = [i for i in range(1, expected_count + 1) if i not in parsed]
    if missing:
        logger.warning(
            "[sop_translation] batch incomplete on first pass (got %d/%d, missing=%s); retrying",
            len(parsed),
            expected_count,
            missing[:5],
        )
        retry_prompt = (
            prompt
            + "\n\nIMPORTANT: Your previous output was incomplete. You MUST "
            f"output exactly {expected_count} lines, numbered 1 through "
            f"{expected_count}, with one translation per input line. "
            "Do NOT skip any line, even short or single-word inputs."
        )
        raw = await _llm_chat(retry_prompt, model_name)
        parsed.update(_parse_numbered_lines(raw, expected_count))

    return parsed


async def translate_sop_schema(
    sop: SOPSchema,
    target_language: str,
    *,
    source_language: str | None = None,
) -> SOPSchema:
    """Translate user-facing SOP text to the requested language.

    Raises SOPTranslationError if the LLM cannot produce a usable translation.
    Callers (the FastAPI route) should surface that to the user instead of
    silently persisting the original.
    """
    target = normalize_sop_language(target_language)
    source = normalize_sop_language(source_language or sop_output_language(sop))
    metadata = dict(sop.generation_metadata or {})

    if target == source and metadata.get("output_language") == target:
        return sop

    segments = _collect_segments(sop)
    if not segments:
        # Nothing to translate; just stamp the metadata so the toggle UI
        # knows the user explicitly chose this language.
        return _apply_segments(sop, {}, target, None)

    model_name = settings.sop_translation_model or settings.sop_synthesis_model
    logger.info(
        "[sop_translation] start source=%s target=%s segments=%d model=%s",
        source,
        target,
        len(segments),
        model_name,
    )

    # Split segments into fixed-size batches with LOCAL 1..N numbering each.
    # Each batch result is a dict {1: translation, 2: translation, ...}; the
    # caller maps that back to global slot paths.
    batches: list[list[tuple[str, str]]] = []
    for start in range(0, len(segments), _BATCH_SIZE):
        batches.append(segments[start : start + _BATCH_SIZE])

    try:
        results = await asyncio.gather(
            *[
                _translate_batch([text for _slot, text in batch], target, model_name)
                for batch in batches
            ]
        )
    except Exception as exc:
        logger.exception("[sop_translation] LLM call failed: %s", exc)
        raise SOPTranslationError(f"Translation provider unavailable: {exc}") from exc

    # Map (batch result, local index) → slot. Filter out entries that didn't
    # pass per-segment validation (empty, missing, or English-when-Hindi-expected).
    translated_by_slot: dict[str, str] = {}
    failed_slots: list[tuple[str, str]] = []  # (slot, original_text) needing repair
    for batch, batch_result in zip(batches, results):
        for local_idx, (slot, original_text) in enumerate(batch, start=1):
            candidate = batch_result.get(local_idx)
            if candidate and _is_acceptable_translation(original_text, candidate, target):
                translated_by_slot[slot] = candidate.strip()
            else:
                failed_slots.append((slot, original_text))

    # Per-line repair: anything the batched pass missed (or returned in the
    # wrong script) gets one isolated translate call. This is what catches
    # the "some steps stayed in English" case from large SOPs.
    if failed_slots:
        logger.warning(
            "[sop_translation] %d/%d segments failed batch pass; repairing per-line",
            len(failed_slots),
            len(segments),
        )
        repair_results = await asyncio.gather(
            *[_translate_single(text, target, model_name) for _slot, text in failed_slots],
            return_exceptions=True,
        )
        repaired = 0
        for (slot, original_text), candidate in zip(failed_slots, repair_results):
            if isinstance(candidate, Exception):
                logger.warning("[sop_translation] repair raised for slot=%s: %s", slot, candidate)
                continue
            if candidate and _is_acceptable_translation(original_text, candidate, target):
                translated_by_slot[slot] = candidate.strip()
                repaired += 1
        logger.info(
            "[sop_translation] per-line repair recovered %d/%d segments",
            repaired,
            len(failed_slots),
        )

    if not translated_by_slot:
        logger.error("[sop_translation] LLM returned no parsable translations")
        raise SOPTranslationError("Translation produced no usable output.")

    if target == "hi":
        blob = "\n".join(translated_by_slot.values())
        if not _DEVANAGARI_RE.search(blob):
            logger.error(
                "[sop_translation] target=hi but response contains no Devanagari; rejecting"
            )
            raise SOPTranslationError(
                "Translation returned no Hindi script. The model may not support this language pair."
            )

    coverage = len(translated_by_slot) / len(segments) if segments else 1.0
    logger.info(
        "[sop_translation] success source=%s target=%s coverage=%.0f%% model=%s segments=%d",
        source,
        target,
        coverage * 100,
        model_name,
        len(segments),
    )

    # Hard threshold: if we ended up with less than half the segments
    # actually translated, treat the whole call as a failure so the user
    # sees the red banner instead of a half-Hindi SOP.
    if coverage < 0.5:
        raise SOPTranslationError(
            f"Translation only covered {int(coverage * 100)}% of the SOP. Try again."
        )

    return _apply_segments(sop, translated_by_slot, target, model_name)


# Kept for backwards-compatible imports elsewhere.
def _build_prompt(*args: Any, **kwargs: Any) -> str:  # pragma: no cover
    raise NotImplementedError("Replaced by _build_batch_prompt")


def _looks_translated(*args: Any, **kwargs: Any) -> bool:  # pragma: no cover
    raise NotImplementedError("Replaced by inline sanity check in translate_sop_schema")
