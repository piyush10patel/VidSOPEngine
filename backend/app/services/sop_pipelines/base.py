"""Shared utilities for both pipelines — schema parsing, image assignment.

Anything that's TRULY generic (not pipeline-specific) lives here.
Pipeline-specific logic lives in physical.py / ui.py.
"""
import json
import logging
import re
from typing import List, Optional

from app.schemas.sop import SOPSchema


logger = logging.getLogger(__name__)


_FRAME_NUM_RE = re.compile(r"frame\s*#?\s*(\d+)", re.IGNORECASE)
_WORD_RE = re.compile(r"[A-Za-zऀ-ॿ][A-Za-z0-9ऀ-ॿ]+")


def extract_json_from_response(response: str) -> str:
    """Extract a JSON object from an LLM response, tolerating markdown fences."""
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response)
    if json_match:
        return json_match.group(1).strip()
    json_match = re.search(r'\{[\s\S]*\}', response)
    if json_match:
        return json_match.group(0).strip()
    return response.strip()


def _despread_cluster_runs(
    values: List[Optional[int]],
    n_frames: int,
) -> List[Optional[int]]:
    """Replace adjacent same-value runs of ``source_frame_num`` with a spread.

    Synthesis models routinely emit ``source_frame_num=N`` (typically the
    last frame) for every closing step of an SOP, even when the prompt asks
    for distinct frames. The monotonic clamping downstream bumps those to
    consecutive frames at the end of the video — visually almost identical
    because the camera barely moves across the closing seconds. Operators
    perceive this as "the same picture repeated for the last 4 steps".

    This helper detects any run of 2+ adjacent positions with the same
    integer value and replaces the run with a proportional spread between
    the previous distinct anchor and that value (or between that value and
    the next distinct anchor, depending on which side the run is on). The
    earlier the run, the more space it gets on the left; the later the run,
    the more space it gets on the right. Steps that emitted ``None`` are
    untouched here — they go through the regular fallback chain.
    """
    n = len(values)
    if n < 2:
        return list(values)
    result = list(values)
    i = 0
    while i < n:
        v = result[i]
        if v is None:
            i += 1
            continue
        j = i
        while j + 1 < n and result[j + 1] == v:
            j += 1
        run_len = j - i + 1
        if run_len >= 2:
            # Anchors: previous distinct value before the run (exclusive),
            # next distinct value after the run (exclusive). Use frame 0 /
            # n_frames + 1 as virtual anchors when one side is missing.
            prev_anchor = 0
            for k in range(i - 1, -1, -1):
                if result[k] is not None:
                    prev_anchor = result[k]
                    break
            next_anchor = n_frames + 1
            for k in range(j + 1, n):
                if result[k] is not None:
                    next_anchor = result[k]
                    break
            lower = max(prev_anchor + 1, 1)
            upper = min(next_anchor - 1, n_frames)

            # Special case: the cluster spans the entire SOP and there are
            # no anchors on either side. The LLM gave us a single value
            # for every step — that is no signal at all, not "they all
            # happen at frame N". Spread proportionally across the full
            # frame range so the first step lands at frame 1 and the
            # last step at n_frames (matching uniform pacing).
            if prev_anchor == 0 and next_anchor == n_frames + 1:
                start, end = 1, n_frames
            else:
                # Otherwise honour the clustered target. If it sits near
                # the upper bound, spread leftward from it; near the
                # lower bound, spread rightward. Operators still get
                # frames close to where the model thought things happen.
                target = max(lower, min(upper, v))
                if target - lower < upper - target:
                    start = target
                    end = min(upper, target + run_len - 1)
                else:
                    end = target
                    start = max(lower, target - run_len + 1)
                if end - start + 1 < run_len:
                    # Not enough room on either side — fall back to a
                    # uniform spread across [lower, upper].
                    start, end = lower, upper
            for k in range(run_len):
                if run_len == 1:
                    result[i + k] = max(lower, min(upper, v))
                else:
                    result[i + k] = round(start + k * (end - start) / (run_len - 1))
            logger.info(
                "[frame_assign] despread run target=%s len=%d -> %s",
                v, run_len, result[i:j + 1],
            )
        i = j + 1
    return result


def _coerce_frame_num(value) -> Optional[int]:
    """Pull a 1-based frame number out of whatever shape the LLM emitted.

    Models occasionally wrap the integer in a string, append `/N`, or write
    `"Frame 5"` instead of the bare digit. Be lenient — anything we can parse
    becomes a positive int; everything else becomes None and the assignment
    falls back to evidence + content similarity.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        ivalue = int(value)
        return ivalue if ivalue > 0 else None
    text = str(value).strip()
    if not text:
        return None
    match = _FRAME_NUM_RE.search(text)
    if match:
        try:
            num = int(match.group(1))
            return num if num > 0 else None
        except ValueError:
            return None
    try:
        num = int(text.split("/")[0].strip())
        return num if num > 0 else None
    except (ValueError, AttributeError):
        return None


def normalise_event_format(data: dict) -> dict:
    """Normalise the new sop[]/instruction format → SOPSchema fields."""
    if "sop" in data and isinstance(data["sop"], list):
        normalised_steps = []
        for item in data["sop"]:
            normalised_steps.append({
                "step_number": item.get("step_number", 0),
                "title": item.get("title", ""),
                "description": item.get("instruction", item.get("description", "")),
                "tools": item.get("objects", item.get("tools", [])),
                "checks": item.get("checks", []),
                "evidence": item.get("evidence", []),
                "confidence": item.get("confidence", 1.0),
                    "notes": item.get("notes"),
                    "verified": item.get("verified"),
                    "verification_quote": item.get("verification_quote"),
                    "correctness_score": item.get("correctness_score"),
                    "correctness_label": item.get("correctness_label"),
                    "correctness_reason": item.get("correctness_reason"),
                    "correctness_issue_type": item.get("correctness_issue_type"),
                    "user_marked_wrong": item.get("user_marked_wrong", False),
                    "user_correction_note": item.get("user_correction_note"),
                    # The synthesis prompt now requires this; accept the legacy
                    # `frame_num` spelling too so we do not regress older models.
                    "source_frame_num": _coerce_frame_num(
                        item.get("source_frame_num", item.get("frame_num"))
                    ),
                })
        return {
            "title": data.get("title", "Untitled SOP"),
            "description": data.get("summary", data.get("description", "")),
            "steps": normalised_steps,
            "notes": data.get("warnings", data.get("notes", [])),
            "overall_confidence": data.get("overall_confidence", 1.0),
            "warnings": data.get("warnings", []),
            "needs_review": data.get("needs_review", False),
            "generation_metadata": data.get("generation_metadata", {}),
            "video_type": data.get("video_type"),
        }
    return data


def parse_sop_response(response: str, video_type: str) -> SOPSchema:
    """Parse an LLM response (JSON string) into a validated SOPSchema."""
    json_str = extract_json_from_response(response)
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in response: {e}")

    data = normalise_event_format(data)
    data["video_type"] = video_type

    try:
        return SOPSchema(**data)
    except Exception as e:
        raise ValueError(f"Response does not match SOP schema: {e}")


# Stopwords stripped before computing step↔frame word overlap. English first;
# the most common Devanagari connectors second so translated SOPs still match
# on content words. Frame descriptions are written in English by the vision
# prompt, so cross-language matching here is best-effort — when it fails we
# still have the monotonic fallback.
_STOPWORDS = frozenset(
    """
    a an and any are as at be been being but by can could did do does done
    each for from had has have he her his how i if in into is it its just
    me might more most must my no nor not now of off on once one only or
    other our out over per same she should so some such than that the their
    them then there these they this those through to too under up upon use
    used using very was way we well were what when where which while who why
    will with would you your also into around toward across before after via
    next previous step number show shown showing visible see seen observe
    observed
    है हैं था थे थी थी हो रहा रही रहे करना करते करता करती किया गया गई गए
    और या तो ही भी से के की का को में पर एक यह वह जो कि अब तब जब
    """.split()
)


def _tokenize(text) -> set:
    """Tokenize a string into a set of content words for overlap scoring."""
    if not text:
        return set()
    if isinstance(text, (list, tuple)):
        text = " ".join(str(t) for t in text)
    tokens = {tok.lower() for tok in _WORD_RE.findall(str(text))}
    return {t for t in tokens if len(t) > 2 and t not in _STOPWORDS}


def _step_keywords(step) -> set:
    """Build a keyword set covering the user-visible text on a step."""
    parts = [
        getattr(step, "title", ""),
        getattr(step, "description", ""),
    ]
    tools = getattr(step, "tools", None) or []
    if tools:
        parts.extend(str(t) for t in tools)
    return _tokenize(" ".join(str(p) for p in parts if p))


def _frame_keywords(obs: dict) -> set:
    return _tokenize(obs.get("description", ""))


# Suffix-stripper that handles the same -ing / -ed / -s endings the
# deterministic-eval module strips. The trailing -e is included so
# "wipe" and "wiping" both stem to "wip"; same for "place"/"placing",
# "remove"/"removing". Without that, the verb-match swap couldn't see
# that the chosen frame's PRIMARY ACTION "wiping the housing" matches
# a step whose instruction starts with "wipe".
_VERB_SUFFIXES = ("ing", "ed", "es", "s", "e")


def _stem_verb(word: str) -> str:
    word = word.lower().rstrip(".,!?:;")
    for suffix in _VERB_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


# Verb synonym map: every synonym lookup returns its canonical family
# key. This catches the common case where the step text uses one
# wording ("Mount the bracket") and the vision model describes the
# frame using a different one ("attaching the bracket"). Without the
# map, _step_verb_stems would emit {"mount"} and _action_contains_any
# would never match "attaching" → stems → "attach" ≠ "mount".
#
# Pairs are CHOSEN conservatively: we group only verbs that
# substitute cleanly in procedural language. Opposite-direction
# pairs (tighten/loosen, attach/detach) live in DIFFERENT families
# so the verb-match swap still distinguishes them.
_VERB_SYNONYMS: dict[str, str] = {}


def _register_family(canonical: str, *aliases: str) -> None:
    """Register every alias as mapping to one canonical verb."""
    for word in (canonical, *aliases):
        _VERB_SYNONYMS[word] = canonical
        # Pre-stem so lookups can match e.g. "tighten" -> "tighten"
        # even when the input has been stemmed to "tighten".
        stem = _stem_verb(word)
        _VERB_SYNONYMS.setdefault(stem, canonical)


_register_family("attach", "fasten", "secure", "mount", "install", "fix", "affix", "connect", "hook")
_register_family("detach", "remove", "dismount", "uninstall", "unfasten", "disconnect", "unhook")
_register_family("tighten", "torque", "snug")
_register_family("loosen")
_register_family("inspect", "check", "verify", "confirm", "test", "examine", "review")
_register_family("clean", "wipe", "scrub", "wash", "rinse", "polish", "brush", "dust", "dry")
_register_family("cut", "trim", "slice", "snip", "chop")
_register_family("place", "put", "set", "lay", "position", "deposit")
_register_family("rotate", "turn", "twist", "spin")
_register_family("press", "push", "depress", "click", "tap")
_register_family("pull", "draw", "yank", "tug")
_register_family("lift", "raise", "hoist", "elevate", "pick")
_register_family("lower", "drop", "descend")
_register_family("open", "uncap", "unfold")
_register_family("close", "cap", "fold", "shut")
_register_family("pour")
_register_family("fill")
_register_family("empty", "drain")
_register_family("mix", "stir", "blend", "whisk", "shake")
_register_family("add", "insert")
_register_family("screw")
_register_family("unscrew")
_register_family("scan")
_register_family("read")
_register_family("record", "note", "label")
_register_family("wrap")
_register_family("unwrap")
_register_family("load")
_register_family("unload")
_register_family("start")
_register_family("stop")
_register_family("switch", "toggle")
_register_family("type")
_register_family("tilt")
_register_family("approach", "reach")
_register_family("release")


def _canonical_verb(word: str) -> str | None:
    """Map a token to its canonical verb family, or None if not a known verb.

    Tries the lower-case word first, then its stem. Returns None for
    non-verbs so we can build a step's verb set without polluting it
    with random nouns.
    """
    if not word:
        return None
    w = word.lower().rstrip(".,!?:;")
    if w in _VERB_SYNONYMS:
        return _VERB_SYNONYMS[w]
    return _VERB_SYNONYMS.get(_stem_verb(w))


def _step_verb_stems(step) -> set:
    """Return the set of canonical action verbs that identify this step.

    Same name as before for callers, but now returns canonical family
    keys rather than raw stems. "Mount the bracket" emits {"attach"};
    "Secure the bolt" also emits {"attach"}; "Tighten the bolt" emits
    {"tighten"}. So a chosen frame whose PRIMARY ACTION says
    "attaching the bracket" matches both phrasings.
    """
    text = " ".join(
        str(p) for p in (
            getattr(step, "title", ""),
            getattr(step, "description", ""),
        ) if p
    ).lower()
    if not text:
        return set()
    tokens = re.findall(r"[A-Za-z]+", text)
    if not tokens:
        return set()
    canonicals: set = set()
    # First-token bias: the synthesis prompt enforces verb-first
    # imperatives, so the leading token is usually the action verb.
    canon = _canonical_verb(tokens[0])
    if canon:
        canonicals.add(canon)
    # Then scan the rest of title+description for any known verb.
    for tok in tokens[1:]:
        canon = _canonical_verb(tok)
        if canon:
            canonicals.add(canon)
    return canonicals


def _action_contains_any_verb(action_text: str, verb_stems: set) -> bool:
    """True if the frame's PRIMARY ACTION mentions a verb in the same
    canonical family as any of the step's verbs."""
    if not action_text or not verb_stems:
        return False
    for tok in re.findall(r"[A-Za-z]+", action_text):
        canon = _canonical_verb(tok)
        if canon and canon in verb_stems:
            return True
    return False


def _extract_evidence_frame(step) -> Optional[int]:
    """Return the first frame number cited in this step's evidence list.

    The synthesis prompts tell the model to write evidence strings like
    ``"Frame 5"`` or ``"Frame 5/12"``. When present this is the strongest
    grounding signal and overrides the content-similarity score.
    """
    evidence = getattr(step, "evidence", None) or []
    for entry in evidence:
        match = _FRAME_NUM_RE.search(str(entry))
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return None


def assign_frame_images_linear(steps: List, frame_observations: List[dict]) -> None:
    """Map each step to the frame that visually best supports it.

    Signal priority — the LLM that wrote the step is the only thing with the
    full context (events + transcript + frame descriptions) needed to pick
    the right picture, so we trust its choice first and treat everything else
    as a fallback safety net:

    1. **``source_frame_num`` field** (primary, deterministic). The synthesis
       signatures now REQUIRE every step to emit the single frame number that
       visually illustrates the step. When present, that frame's image is
       used as-is — no scoring, no interpolation.
    2. **Evidence citation** (fallback). Older SOPs sometimes only have
       ``"Frame N"`` references in the free-text evidence list; we parse
       those when ``source_frame_num`` is missing.
    3. **Content similarity** (fallback). Jaccard word overlap between the
       step text and each frame's vision description. Catches steps the
       model failed to anchor at all.
    4. **Proportional anchor** (last resort, monotonic). Steps with no usable
       signal get pinned to a position interpolated between their assigned
       neighbours, bounded below by the previous step's frame.

    Assignment is greedy left→right with a monotonic constraint (frame index
    never goes backwards) and we reserve one frame per remaining step so
    later steps still have room to advance.

    Mutates the steps list in place.
    """
    if not steps or not frame_observations:
        return
    n_steps = len(steps)
    n_frames = len(frame_observations)

    def _image_for_frame_num(num: int) -> str:
        # Prefer an exact frame_num match; fall back to clamped 1-based index
        # because not every pipeline stamps frame_num on its observations.
        for obs in frame_observations:
            try:
                if int(obs.get("frame_num")) == num:
                    return obs.get("image_url", "") or ""
            except (TypeError, ValueError):
                continue
        clamped = max(1, min(num, n_frames))
        return frame_observations[clamped - 1].get("image_url", "") or ""

    frame_tokens = [_frame_keywords(obs) for obs in frame_observations]
    step_tokens = [_step_keywords(step) for step in steps]
    cited = [_extract_evidence_frame(step) for step in steps]
    # Pull the LLM's source_frame_num up front, then redistribute adjacent
    # duplicates BEFORE the monotonic clamping pass. Without this step the
    # synthesis model's tendency to write the same final frame for every
    # closing step would produce visually-identical pictures even though
    # the assigned numbers differ by 1.
    explicit_raw = [getattr(step, "source_frame_num", None) for step in steps]
    explicit = _despread_cluster_runs(explicit_raw, n_frames)

    # Greedy monotonic assignment.
    assigned: List[int] = []
    explicit_anchored = 0
    citation_anchored = 0
    similarity_anchored = 0
    last_frame = 0  # 1-based; 0 means "no previous assignment"
    for i, step in enumerate(steps):
        floor = max(last_frame + 1, 1)
        remaining = n_steps - i - 1
        ceiling = max(floor, n_frames - remaining)
        ceiling = min(ceiling, n_frames)
        if floor > ceiling:
            floor = ceiling

        proportional = (
            1
            if n_steps == 1
            else round(i * (n_frames - 1) / (n_steps - 1)) + 1
        )

        # 1. Explicit source_frame_num wins. We respect it even when the
        # monotonic constraint would push it forward — the LLM picked this
        # specific frame on purpose, and operators noticed when adjacent
        # steps shared a frame purely because of our floor-bumping. We only
        # clamp it into [1, n_frames] and into [floor, ceiling] when the
        # model produced something obviously inconsistent (e.g. a frame
        # number below a strictly later step's anchor).
        chosen = explicit[i]
        if chosen is not None:
            chosen = max(1, min(int(chosen), n_frames))
            # Soft monotonic: never go below floor or above ceiling.
            chosen = max(floor, min(ceiling, chosen))
            assigned.append(chosen)
            last_frame = chosen
            explicit_anchored += 1
            continue

        # 2. Evidence citation parsed from the free-text "Frame N" string.
        cited_frame = cited[i]
        if cited_frame is not None:
            cited_frame = max(floor, min(ceiling, cited_frame))
            assigned.append(cited_frame)
            last_frame = cited_frame
            citation_anchored += 1
            continue

        # 3. Content similarity within [floor, ceiling].
        best_idx: Optional[int] = None
        best_score = float("-inf")
        s_tokens = step_tokens[i]
        for f_idx in range(floor, ceiling + 1):
            f_tokens = frame_tokens[f_idx - 1]
            overlap = len(s_tokens & f_tokens) if s_tokens and f_tokens else 0
            union = max(1, len(s_tokens | f_tokens))
            similarity = overlap / union
            score = similarity * 10.0
            score -= abs(f_idx - proportional) * 0.1
            if score > best_score:
                best_score = score
                best_idx = f_idx

        if best_idx is None or best_score <= 0:
            # 4. No signal — proportional fallback.
            best_idx = max(floor, min(ceiling, proportional))
        elif best_score > 0 and s_tokens and len(s_tokens & frame_tokens[best_idx - 1]) > 0:
            similarity_anchored += 1

        assigned.append(best_idx)
        last_frame = best_idx

    # Three-stage safety net after the greedy assignment above. Each
    # stage is narrow on its own; they don't reuse word-overlap (the
    # heuristic that caused the jumble bug in commit 17a2b9c).
    #
    # Stage 1 - PHASE swap (look ±1, then ±2 in the right direction)
    #   If the chosen frame's PHASE tag is "before" or "after", look
    #   forward (for "before") or backward (for "after") by 1, then by
    #   2, for a frame tagged "during". First match wins. Phase is
    #   direction-of-action data so this cannot pick the wrong action
    #   (tighten vs loosen both have "during" frames; we'd land on a
    #   "during" but couldn't tell them apart - that's stage 2's job).
    #
    # Stage 2 - VERB-MATCH swap (look ±1, then ±2)
    #   If the chosen frame's PRIMARY ACTION text doesn't contain any
    #   verb stem from the step's instruction, but a nearby frame's
    #   PRIMARY ACTION does, swap to it. This catches the "LLM cited a
    #   wipe frame for an inspect step" pattern that phase alone can't
    #   see. Compares stemmed VERBS only (first content words of step
    #   instruction), not arbitrary tokens, so the tighten/loosen-style
    #   noun overlap can't trigger a swap.
    #
    # Both stages respect monotonic ordering and never reuse another
    # step's frame.
    phases = [obs.get("phase") for obs in frame_observations]
    primary_actions = [
        (obs.get("primary_action") or "").lower()
        for obs in frame_observations
    ]
    # VLM self-confidence per frame, used to gate the verb-match swap.
    # A high-confidence pick (≥0.8) means the vision model was sure
    # about what's happening; we should NOT override the synthesis
    # LLM's choice when the underlying frame description was solid.
    # None for frames that didn't emit a CONFIDENCE tag.
    confidences = [obs.get("confidence") for obs in frame_observations]
    phase_swap_count = 0
    verb_swap_count = 0

    def _bounds(i: int) -> tuple[int, int]:
        prev_floor = (assigned[i - 1] + 1) if i > 0 else 1
        next_ceiling = (assigned[i + 1] - 1) if i < n_steps - 1 else n_frames
        return max(prev_floor, 1), min(next_ceiling, n_frames)

    # --- Stage 1: PHASE swap ---
    for i in range(n_steps):
        chosen = assigned[i]
        if chosen < 1 or chosen > n_frames:
            continue
        chosen_phase = phases[chosen - 1]
        if chosen_phase not in ("before", "after"):
            continue
        floor, ceiling = _bounds(i)
        # Direction of search: "before" peaks ahead, "after" peaks
        # behind. Single direction only - no ambiguity.
        direction = +1 if chosen_phase == "before" else -1
        for offset in (1, 2):
            candidate = chosen + direction * offset
            if not (1 <= candidate <= n_frames):
                continue
            if not (floor <= candidate <= ceiling):
                continue
            if phases[candidate - 1] == "during":
                assigned[i] = candidate
                phase_swap_count += 1
                break

    # --- Stage 2: VERB-MATCH swap ---
    # We extract a small set of verb stems from each step's text and
    # check whether the chosen frame's PRIMARY ACTION contains any of
    # them. If not, look ±1 and ±2 for a frame whose PRIMARY ACTION
    # does. Skipped silently when primary_action data is absent on
    # the frames (e.g. fixtures, older pipelines).
    #
    # CONFIDENCE gate: if the vision model reported a high confidence
    # (≥0.8) for the chosen frame, we trust its description and skip
    # the override. The verb mismatch in that case is more likely a
    # vocabulary gap than an LLM picking the wrong frame.
    _CONFIDENCE_TRUST_FLOOR = 0.8
    step_verbs: List[set] = [_step_verb_stems(s) for s in steps]
    for i in range(n_steps):
        chosen = assigned[i]
        if chosen < 1 or chosen > n_frames:
            continue
        verbs = step_verbs[i]
        if not verbs or not primary_actions[chosen - 1]:
            continue
        if _action_contains_any_verb(primary_actions[chosen - 1], verbs):
            continue  # already a verb match
        chosen_conf = confidences[chosen - 1]
        if chosen_conf is not None and chosen_conf >= _CONFIDENCE_TRUST_FLOOR:
            continue  # VLM was confident; do not override
        floor, ceiling = _bounds(i)
        # Prefer ±1, then ±2, in both directions. First match wins.
        candidates = [
            chosen - 1, chosen + 1, chosen - 2, chosen + 2,
        ]
        for cand in candidates:
            if cand == chosen or not (1 <= cand <= n_frames):
                continue
            if not (floor <= cand <= ceiling):
                continue
            cand_action = primary_actions[cand - 1]
            if cand_action and _action_contains_any_verb(cand_action, verbs):
                assigned[i] = cand
                verb_swap_count += 1
                break

    for step, frame_num in zip(steps, assigned):
        step.image_url = _image_for_frame_num(frame_num)
        # Persist the FINAL chosen frame back onto the step. Before this
        # write the step still carried the LLM's pre-despread value, so a
        # lazy "all 8s" output would look identical to a clean one when
        # the SOP was serialised — only the image_url silently differed.
        # Writing the post-assignment number means downstream consumers
        # (frontend, training generation, metrics, debug dumps) all see
        # the same frame our picture came from.
        step.source_frame_num = frame_num

    known_confidences = [c for c in confidences if c is not None]
    avg_conf = (
        sum(known_confidences) / len(known_confidences)
        if known_confidences else None
    )
    logger.info(
        "[frame_assign] steps=%d frames=%d explicit=%d citation=%d similarity=%d "
        "fallback=%d phase_swaps=%d verb_swaps=%d phases_known=%d/%d "
        "actions_known=%d/%d avg_frame_confidence=%s",
        n_steps,
        n_frames,
        explicit_anchored,
        citation_anchored,
        similarity_anchored,
        n_steps - explicit_anchored - citation_anchored - similarity_anchored,
        phase_swap_count,
        verb_swap_count,
        sum(1 for p in phases if p in ("before", "during", "after")),
        n_frames,
        sum(1 for a in primary_actions if a),
        n_frames,
        f"{avg_conf:.2f}" if avg_conf is not None else "n/a",
    )
