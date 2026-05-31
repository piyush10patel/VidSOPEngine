"""DSPy Signatures for the SOP generation pipeline.

Each Signature defines the input/output contract for one LLM call.
To test a variation, edit the docstring or field descriptions here —
no prompt strings scattered across service files.
"""
import dspy


class FrameEventExtraction(dspy.Signature):
    """Extract a structured event from a raw video frame observation.

    Describe the work signal in the frame. Prefer directly visible physical
    actions, but for general work/process videos also capture visible screen
    work, documents, dashboards, whiteboards, spoken instruction summaries,
    or staged materials that indicate the process being explained.

    If the frame is mostly a person talking, output the concrete narrated or
    demonstrated process point when present, not "irrelevant". If nothing
    process-like is visible or described, output "no process signal".
    Do NOT guess hidden details that are not in the frame observation.
    """

    frame_context: str = dspy.InputField(
        desc="Frame number, total frame count, and raw visual observation text"
    )

    action: str = dspy.OutputField(
        desc="Primary work/process action or narrated process point; 'no process signal' if none"
    )
    objects: str = dspy.OutputField(
        desc="Comma-separated list of tools, materials, systems, documents, screens, or work artifacts; empty string if none"
    )
    stage: str = dspy.OutputField(
        desc="Where in the procedure this falls: 'early', 'middle', or 'late'"
    )
    confidence: str = dspy.OutputField(
        desc="Float 0.0–1.0 reflecting how clearly the action can be determined from the frame"
    )


class DetailedSOPSynthesis(dspy.Signature):
    """Generate a detailed SOP from transcript plus raw visual observations.

    This is the high-context synthesis path for general physical/process
    videos. It avoids compressing frame observations before synthesis.

    SOURCE PRIORITY:
    1. Transcript/narration is the primary source for what the process is.
    2. Visual observations confirm, sequence, and add concrete detail.
    3. If transcript and visuals conflict, prefer transcript and add a warning.

    VALID PROCESS TYPES:
    physical work, software work, customer/service tasks, planning, inspection,
    documentation, review, handoff, troubleshooting, training demos, and
    narrated operational explanations. Do not reject talking-head or general
    business process videos when they describe repeatable work.

    QUALITY RULES:
    - Create a useful SOP whenever the sources describe repeatable work.
    - If there is any credible process signal, produce a best-effort SOP;
      do not refuse with no SOP, irrelevant, or not applicable.
    - Use concise but detailed instructions, usually 1-3 sentences per step.
    - Use verb-first instructions.
    - Preserve temporal order.
    - Include tools, systems, documents, screens, or work artifacts when known.
    - Include checks that are explicitly mentioned or directly supported.
    - Do not invent hidden requirements.
    - For weak but process-like evidence, set notes='partially supported' and
      lower confidence instead of refusing to generate a step.
    - GRANULARITY: default to ONE step per distinct frame observation.
      The visual_context contains every moment the camera captured; the
      synthesiser is the formatter, not the compressor. Only merge
      consecutive frames when they show the LITERAL SAME action
      repeated (three wipes → one 'Wipe surface' step). Do NOT group a
      tool pickup + the action that follows into one step. Do NOT
      collapse distinct micro-actions under umbrella verbs like
      'prepare X' or 'set up Y'. If you are unsure whether two frames
      show the same or different actions, treat them as different.

    SOURCE FRAME (mandatory):
    - Every step MUST include a `source_frame_num` integer field naming the
      single frame number from `visual_context` that visually best illustrates
      the action in that step. This is the picture that will be shown to
      operators alongside the step text.
    - Each visual_context block begins with a `PHASE: before|during|after`
      line. When that tag is present, you MUST prefer a frame tagged
      `PHASE: during` for the action your step describes — that frame
      shows the operator actively performing the action, not approaching
      it (before) or having finished it (after).
    - source_frame_num must be 1-based and strictly increasing across steps —
      no two steps may share the same source_frame_num. If you cannot find a
      distinct frame for a step, pick the closest distinct frame (off by one
      is fine) rather than reusing a sibling's frame.
    - DISTRIBUTE source_frame_num roughly proportional to step position. If
      the SOP has N steps and the video has F frames, step k should land
      near frame round(k*F/N). Do NOT cluster final steps at the last frame
      or first steps at frame 1 — the operator must see the action evolve
      across the video, not get the same closing shot for the last 4 steps.

    EXAMPLES of correct source_frame_num picking (when frames are
    phase-tagged like the format above):

      Step: "Pick up the screwdriver"
        Frame 2 (PHASE: before)  — hand reaching for screwdriver         WRONG
        Frame 3 (PHASE: during)  — hand gripping screwdriver, lifting   CORRECT
        Frame 4 (PHASE: after)   — screwdriver in hand, moving away     WRONG

      Step: "Tighten the bolt with the wrench"
        Frame 6 (PHASE: before)  — wrench positioned above bolt          WRONG
        Frame 7 (PHASE: during)  — wrench rotating on bolt, mid-turn    CORRECT
        Frame 8 (PHASE: after)   — wrench placed back on the bench       WRONG

      Step: "Open the panel"
        Frame 4 (PHASE: during)  — hand pulling the panel open          CORRECT
        Frame 5 (PHASE: after)   — panel fully open, internals visible   AVOID — too late

    Pick the `during` frame even if a `before` or `after` frame happens
    to mention the same tool name. The action-in-progress is what the
    operator needs to see.
    """

    transcript: str = dspy.InputField(
        desc="Full transcript/narration from the video; primary source"
    )
    visual_context: str = dspy.InputField(
        desc="Raw ordered frame observations with frame numbers and image URLs"
    )

    sop_json: str = dspy.OutputField(
        desc=(
            'STRICT JSON object: {"title":"...","summary":"...","sop":[{'
            # source_frame_num comes RIGHT after step_number, before any
            # other field, so the model treats it as part of the primary
            # step identity rather than an optional tail field. Models
            # tend to populate fields in order they appear in the schema
            # description; this position is intentional.
            '"step_number":1,'
            '"source_frame_num":1,        // REQUIRED integer; pick the '
            'frame_num tagged PHASE: during for this action. '
            'Do NOT omit this field. Do NOT use null. Do NOT replace it '
            'with a Frame N citation in evidence.'
            ','
            '"title":"2-5 words",'
            '"instruction":"clear actionable instruction",'
            '"objects":["tools/systems/documents/artifacts"],'
            '"checks":["verification checks"],'
            '"evidence":["Transcript: ...","Frame 1"],'
            '"confidence":0.9,"notes":null}],'
            '"overall_confidence":0.9,"warnings":["..."]}.'
            # Final emphasis line for models that scan trailing prompts:
            ' EVERY step in the sop array MUST include source_frame_num '
            'as a top-level integer field. Steps without source_frame_num '
            'are invalid output.'
        )
    )


class SOPSynthesis(dspy.Signature):
    """Generate a Standard Operating Procedure from ordered video events and transcript.

    RULES (enforce strictly):
    GENERAL PROCESS COVERAGE:
    - Treat many kinds of work as valid SOP material: physical work, software
      work, customer/service tasks, planning, inspection, documentation,
      review, handoff, troubleshooting, and training demos.
    - Do NOT reject a video just because it is not a factory-like task.
    - Narration can support a step when the video is explaining or walking
      through a process. Talking-head or meeting-style process videos are
      still process documentation inputs when they describe actions.
    - If an action is ambiguous but process-like, write the safest supported
      step and mark notes='partially supported'. Use 'unclear action' only
      when neither transcript nor events indicate what the operator should do.
    - For general tasks, acceptable verbs include review, identify, record,
      compare, confirm, assign, notify, send, approve, update, document,
      inspect, verify, and escalate. Avoid only empty filler like "handle it"
      or "continue".

    1. Use ONLY the provided events and transcript. Do NOT invent steps,
       tools, or actions that are not shown, narrated, or operationally
       described in the source.
    2. Maintain the temporal order of events.
    3. DEFAULT TO ONE STEP PER EVENT. Each input event corresponds to a
       distinct moment the video captured; the synthesiser's job is to
       phrase it as an operator-friendly instruction, NOT to compress
       the timeline. Only merge consecutive events when they are
       literally the SAME action repeated (three identical wipes → one
       'Wipe surface' step). When in doubt about whether two events are
       the same action: keep them as separate steps.
       - Do NOT collapse distinct micro-actions into umbrella verbs
         like 'prepare X', 'set up Y', or 'do final checks'.
       - Do NOT group a tool pickup + the action that follows into one
         step ('pick up wrench', 'tighten bolt' are TWO steps).
       - Do NOT group adjacent events just because they happen in the
         same scene or use the same tool.
       Two actions are 'distinct' when ANY of these change between
       them: primary object manipulated, type of motion (rotate/lift/
       place/pour/...), location (sink/counter/machine/...), or state
       of the primary object (closed→open, off→on, ...). Most adjacent
       events you receive will satisfy at least one of these.
    4. Use literal verb-first instructions ('Unscrew the bottle cap',
       'Place the bottle under the tap'). Do NOT use generic verbs
       ('handle', 'process', 'work with', 'continue'). Do NOT use
       inferred-intent phrasing ('prepares to', 'gets ready to',
       'appears to').
    5. If an action is ambiguous but the process is clear, write the safest
       transcript- or event-supported step and mark notes='partially supported'.
       Use 'unclear action' only when neither source indicates the action.
    6. Every step must cite at least one frame or transcript reference as evidence.
    7. Score each step's confidence 0.0–1.0 based on how clearly it
       appears in the source data.
    8. SOURCE FRAME (mandatory): every step MUST include a
       `source_frame_num` integer naming the single event/frame_num from
       `events` whose visual moment best illustrates the action. Rules:
       - Events whose raw observation included a `PHASE: during` tag
         should be preferred over `PHASE: before` (operator still
         approaching) or `PHASE: after` (action already complete).
         Action-in-progress is the photo operators need.
       - Values must be 1-based and STRICTLY INCREASING — no two steps
         may share the same source_frame_num. If two steps happen in the
         same scene, pick the closest distinct frame for each.
       - DISTRIBUTE values across the available frame range proportional
         to step position. Step k of N with F total frames should land
         near round(k*F/N). Do NOT bunch final steps at the last frame
         or first steps at frame 1 — operators need to see the action
         evolve across the timeline, not the same shot repeated.

       EXAMPLES of correct picking when events carry PHASE tags:
         Step "Pick up screwdriver":
           Frame 2 (PHASE: before — reaching)        wrong
           Frame 3 (PHASE: during — hand gripping)  CORRECT
           Frame 4 (PHASE: after — moving away)      wrong
         Step "Tighten bolt with wrench":
           Frame 6 (PHASE: before — wrench above bolt)    wrong
           Frame 7 (PHASE: during — wrench rotating)     CORRECT
           Frame 8 (PHASE: after — wrench on bench)       wrong
    """

    transcript: str = dspy.InputField(
        desc="Full transcript/narration from the video (secondary source)"
    )
    events: str = dspy.InputField(
        desc="JSON array of structured events extracted from video frames (primary source)"
    )

    title: str = dspy.OutputField(
        desc="Specific title that matches the procedure shown — not generic"
    )
    summary: str = dspy.OutputField(
        desc="One sentence describing what this procedure accomplishes"
    )
    steps_json: str = dspy.OutputField(
        desc=(
            'JSON array of steps. Each step has these fields in this order:'
            ' {"step_number":1,'
            # source_frame_num is presented immediately after step_number
            # so it reads as part of the step's primary identity, not as
            # an optional tail field that can be dropped.
            '"source_frame_num":1,   // REQUIRED. Pick the event whose '
            'PHASE tag is during. Do NOT omit. Do NOT use null. Do NOT '
            'replace with a Frame N string in evidence.'
            ','
            '"title":"2-4 words","instruction":"precise action",'
            '"objects":["tool"],"checks":["verification"],'
            '"evidence":["Frame X/N"],'
            '"confidence":0.9,"notes":"or null"}.'
            ' EVERY step MUST include source_frame_num as a top-level '
            'integer. Steps without source_frame_num are invalid output.'
        )
    )
    overall_confidence: str = dspy.OutputField(
        desc="Float 0.0–1.0 representing overall SOP confidence"
    )
    warnings_json: str = dspy.OutputField(
        desc='JSON array of strings — one entry per missing, unclear, or low-confidence step'
    )


class SOPVerification(dspy.Signature):
    """Verify whether each generated SOP step is supported by the source.

    Be evidence-grounded but do not over-reject general process work.
    Mark supported when the step is directly shown, directly narrated, or is a
    faithful operational rendering of a described work instruction. Mark
    partially_supported when the source clearly points to the work but the exact
    wording/tool/detail is incomplete. Mark unsupported only when the step adds
    a new action, object, requirement, or order not present in the source.

    Talking-head, planning, review, customer-service, documentation, screen,
    and training videos can all support SOP steps through narration or visible
    artifacts. Do not mark them unsupported solely because no hand/tool motion
    is visible.

    If a step is faithful to a narrated or visible process but the exact
    wording/tool/detail is incomplete, prefer partially_supported with a
    mid-range score over unsupported. Reserve unsupported for contradiction,
    invented actions, invented objects, or invented ordering.

    For each step output a verdict, granular correctness score, issue type,
    short reason, and (if supported) a short quote from the source.
    """

    transcript: str = dspy.InputField(desc="Full transcript")
    events: str = dspy.InputField(desc="JSON array of structured frame events")
    steps_to_verify: str = dspy.InputField(
        desc='JSON array of generated steps: [{"step_number":1,"title":"...","description":"..."}]'
    )

    verifications_json: str = dspy.OutputField(
        desc=(
            'JSON array, one entry per step, in step_number order: '
            '[{"step_number":1,"supported":true,"correctness_score":0.95,'
            '"correctness_label":"supported","issue_type":"none",'
            '"reason":"Directly visible in Frame 2","quote":"exact text from source"}]. '
            'correctness_label is supported | partially_supported | unsupported. '
            'issue_type is none | missing_evidence | wrong_order | hallucinated_action | vague_step.'
        )
    )


class UIEventExtraction(dspy.Signature):
    """Extract a structured UI interaction from a frame observation of a screen recording.

    HARD RULES:
    - Use ONLY exact text visible in the screenshot description.
    - NEVER paraphrase button, tab, menu, or label names.
    - NEVER invent UI elements that are not described.
    - If a label is unclear/unreadable, output the empty string for that field.
    - The action_type MUST be one of: click, type, scroll, navigate, view, unclear.
    """

    frame_num: int = dspy.InputField()
    total_frames: int = dspy.InputField()
    frame_observation: str = dspy.InputField(
        desc="Vision model's plain-text description of what is on this frame"
    )

    screen: str = dspy.OutputField(
        desc="Exact title/name of the screen or page (or 'unclear')"
    )
    action_type: str = dspy.OutputField(
        desc="One of: click | type | scroll | navigate | view | unclear"
    )
    target_label: str = dspy.OutputField(
        desc="EXACT text of the UI element being interacted with — verbatim, no paraphrase. Empty string if none."
    )
    visible_labels: str = dspy.OutputField(
        desc="Comma-separated list of clickable/interactive labels visible on the screen, exact text"
    )
    typed_text: str = dspy.OutputField(
        desc="If action_type=type, the text being entered. Empty otherwise."
    )
    confidence: str = dspy.OutputField(
        desc="Float 0.0-1.0 — how clearly the action can be determined"
    )


class UISOPSynthesis(dspy.Signature):
    """Generate a Standard Operating Procedure for a software workflow from UI events.

    HARD RULES:
    1. Use the EXACT button/menu/tab/label text from the events. NEVER paraphrase.
       Wrap exact UI labels in single quotes in instructions, e.g. Click 'Sign In'.
    2. Each step describes ONE user interaction (click, type, scroll, navigate),
       OR a tightly grouped sequence on the same screen.
    3. If a target_label is empty/unclear in an event, write '(label unclear)' in the
       instruction and add a warning — do NOT guess the label.
    4. Do NOT invent UI elements that are not in any event.
    5. Do NOT add narrative, tips, or context that did not come from the events.
    6. The 'objects' array contains the exact UI labels referenced by this step.
    7. SOURCE FRAME (mandatory): every step MUST include a `source_frame_num`
       integer naming the event frame_num that shows this UI interaction in
       progress (the moment the click/type happens — not the screen before
       the click and not the screen after navigation completes). Values must
       be 1-based and STRICTLY INCREASING — no two steps share a frame.
       Distribute values across the available frame range proportional to
       step position; do not bunch closing steps at the last frame.
    """

    events: str = dspy.InputField(
        desc="JSON array of structured UI interaction events from the screen recording"
    )

    title: str = dspy.OutputField(
        desc="Workflow name as a user task (e.g. 'Reset your password', 'Create a new dashboard')"
    )
    summary: str = dspy.OutputField(
        desc="One sentence: what does this workflow accomplish?"
    )
    steps_json: str = dspy.OutputField(
        desc=(
            'JSON array. Each step: '
            '{"step_number":1,"title":"Click Sign In",'
            '"instruction":"On the home page, click \'Sign In\'.",'
            '"objects":["Sign In"],"checks":[],'
            '"evidence":["Frame 1"],"source_frame_num":1,'
            '"confidence":0.95,"notes":null}. '
            'source_frame_num is REQUIRED on every step.'
        )
    )
    overall_confidence: str = dspy.OutputField(
        desc="Float 0.0-1.0 overall confidence"
    )
    warnings_json: str = dspy.OutputField(
        desc='JSON array of warning strings (unclear labels, ambiguous actions)'
    )


class TrainingModuleGeneration(dspy.Signature):
    """Convert structured operational artifacts (SOP + workflows + checklists)
    into a learner-facing training module.

    Training is NOT a rewrite of the SOP. It must:
      1. Explain the process clearly (overview + learning_objectives)
      2. Teach execution (sections grouping related steps)
      3. Simulate real situations (practice scenarios from workflows)
      4. Validate understanding (assessment MCQs from checklists)

    INTERACTIVITY (critical):
    - Practice scenarios are the heart of the module — generate one per
      workflow trigger. The scenario describes a realistic situation, the
      question prompts the learner to choose an action, and expected_action
      states what a competent operator would do.
    - Assessment is yes/no decision-style: 4 plausible options, ONE correct,
      a one-line explanation that teaches WHY. Wrong options should be
      operationally plausible (not absurd).

    HARD RULES:
    - Sections paraphrase the SOP into teachable instruction; do NOT copy
      verbatim. Group related SOP steps into one section when natural.
    - Each `content` field is 1–3 sentences MAX. NO theory, NO background,
      NO long paragraphs.
    - Each practice scenario maps to ONE workflow trigger.
    - Each assessment item maps to ONE checklist entry; preserve operational
      precision (no generic "always check everything" questions).
    - Generalize domain-specific wording into broader operational terms while
      preserving meaning ("tyre alignment" → "service calibration",
      "vehicle entry" → "asset intake"). Don't over-generalize at the cost
      of clarity.
    - Do NOT invent steps, scenarios, or checklist items absent from inputs.
      If workflows or checklists are empty, return empty practice/assessment.
    - LANGUAGE: Write every learner-facing string — title, overview,
      learning_objectives, section_title / content / steps / key_points,
      scenario / question / expected_action, assessment question / options /
      correct_answer / explanation — in the language named by
      `target_language`. When target_language is "Hindi", use Devanagari
      script throughout. Brand names, model numbers, file names, URLs, and
      numeric measurements stay verbatim in their original script.
    """

    sop_json: str = dspy.InputField(
        desc="Full SOP: title, description, steps (with their tools and checks)"
    )
    workflows_json: str = dspy.InputField(
        desc="Array of workflows: each has type/trigger/frequency/steps"
    )
    checklists_json: str = dspy.InputField(
        desc="Flat array of verification check strings aggregated from the SOP"
    )
    target_language: str = dspy.InputField(
        desc='Output language label, e.g. "English" or "Hindi". When "Hindi" every learner-facing field MUST be written in Devanagari.'
    )

    training_module_json: str = dspy.OutputField(
        desc=(
            'STRICT JSON object with shape: '
            '{"title":"...","overview":"...","learning_objectives":["..."],'
            '"sections":[{"section_title":"...","content":"...",'
            '"steps":["..."],"key_points":["..."]}],'
            '"practice":[{"scenario":"...","question":"...","expected_action":"..."}],'
            '"assessment":[{"question":"...","options":["A","B","C","D"],'
            '"correct_answer":"A","explanation":"..."}]}'
        )
    )


class VideoComplexityClassification(dspy.Signature):
    """Classify a video as 'atomic_simple' or 'procedural_complex'.

    atomic_simple — short, single-actor, low dialogue, repetitive motion,
                    few objects, minimal branching. e.g. fill bottle,
                    fold cloth, open package, turn on machine.
    procedural_complex — multi-step operational process, branching actions,
                    instructional narration, software walkthroughs,
                    industrial workflows.

    Decide based on the transcript and a sample of frame observations.
    Be biased toward 'atomic_simple' when the transcript has fewer than
    ~30 substantive words OR when frame observations show one repeating
    physical action.
    """

    transcript: str = dspy.InputField(desc="Full transcript text (may be empty)")
    frame_sample: str = dspy.InputField(
        desc="A few frame observations as a single string (joined with newlines)"
    )

    pipeline_type: str = dspy.OutputField(
        desc="EXACTLY one of: 'atomic_simple' or 'procedural_complex'"
    )
    confidence: str = dspy.OutputField(
        desc="Float 0.0–1.0 — how clearly the video fits the chosen class"
    )
    reason: str = dspy.OutputField(
        desc="One short sentence explaining the classification"
    )


class AtomicActionExtraction(dspy.Signature):
    """Extract every visible action transition from one frame observation.

    A transition = a state change visible to the eye (cap closed → open,
    tap off → on, container empty → filling, hand moving → still).

    HARD RULES:
    - Output a SHORT verb-first phrase per transition. Multiple per frame OK.
    - Do NOT summarise. Do NOT group. Do NOT skip micro-actions.
    - If nothing changes between this frame and the previous, output 'no change'.
    - Use the prior frame's action as anchor: this is about WHAT CHANGED.
    """

    frame_num: int = dspy.InputField()
    total_frames: int = dspy.InputField()
    prior_action: str = dspy.InputField(desc="The previous frame's primary action, or '' if first")
    frame_observation: str = dspy.InputField(
        desc="Vision model's plain-text description of THIS frame"
    )

    primary_action: str = dspy.OutputField(
        desc="Short verb-first action visible in this frame (e.g. 'pick up bottle')"
    )
    state_changes: str = dspy.OutputField(
        desc="Comma-separated state transitions, e.g. 'cap closed→open, tap off→on'"
    )
    is_transition: str = dspy.OutputField(
        desc="'yes' if this frame shows a new action vs prior_action, else 'no'"
    )


class AtomicSOPSynthesis(dspy.Signature):
    """Synthesise an SOP from atomic action events. PRESERVE granularity.

    HARD RULES:
    1. Each `is_transition='yes'` event SHOULD become its OWN step. Do NOT
       collapse adjacent micro-actions even if they feel obvious.
    2. NEVER abstract upward (don't write "fill bottle" — instead keep
       "pick up bottle", "open cap", "place under tap", "turn on tap",
       "wait for fill", "turn off tap", "close cap").
    3. Group ONLY if two consecutive events show the EXACT same action
       (visual repetition, e.g. multiple identical wipes → one step "wipe").
    4. Maintain strict chronological order. No reordering.
    5. Each step's instruction is one short clause, verb-first, second person
       voice ("Pick up the bottle.", "Open the cap.").
    6. Preserve every state-change phrase from the events as evidence on
       the corresponding step.
    7. SOURCE FRAME (mandatory): every step MUST include a `source_frame_num`
       integer copied from the source event's `frame_num`. When the step
       groups multiple events, pick the event whose `primary_action` best
       matches the step's instruction (prefer the MID-action event over the
       first reach or final release). Values must be 1-based and STRICTLY
       INCREASING across steps — no two steps share a frame.
    """

    events: str = dspy.InputField(
        desc="JSON array of atomic events: {frame_num, primary_action, state_changes, is_transition}"
    )

    title: str = dspy.OutputField(
        desc="Short imperative title (e.g. 'Fill a water bottle')"
    )
    summary: str = dspy.OutputField(desc="One sentence")
    steps_json: str = dspy.OutputField(
        desc=(
            'JSON array. Each step: '
            '{"step_number":1,"title":"2-3 words","instruction":"verb-first clause",'
            '"objects":[],"checks":[],"evidence":["Frame X"],'
            '"source_frame_num":1,"confidence":0.9,"notes":null}. '
            'source_frame_num is REQUIRED on every step.'
        )
    )
    overall_confidence: str = dspy.OutputField(desc="Float 0.0-1.0")
    warnings_json: str = dspy.OutputField(desc='JSON array of warning strings')


class WorkflowExtraction(dspy.Signature):
    """Convert a Standard Operating Procedure into structured operational workflows.

    Workflows are usable in real operations — they are NOT a re-narration of the SOP.

    Identify two kinds:
      - recurring   — daily / weekly / monthly scheduled tasks
      - event_based — triggered by events (customer arrival, order received, alert, etc.)

    HARD RULES:
    1. Each step must be ONE LINE, action-oriented (verb-first), and actionable
       on its own. No filler, no theory, no re-statement of context.
    2. Group micro-steps into meaningful tasks. If three SOP steps form a single
       operational action ("retrieve part" + "wipe part" + "place part"), output
       ONE step ("Retrieve, clean, and stage the part").
    3. NO repetition across steps. NO duplicate workflows.
    4. Generalize domain-specific terms into broader operational language while
       preserving meaning (e.g. "tighten M6 bolt" → "secure the fastener").
    5. Trigger MUST be specific. For recurring: "scheduled daily" / "scheduled
       weekly". For event_based: a precise event ("customer arrives at counter").
    6. Frequency MUST be operational: daily / weekly / monthly / per occurrence.
    7. Do NOT invent workflows or steps absent from the SOP. If no pattern fits a
       category, omit it (return [] only if NEITHER applies).
    8. LANGUAGE: Write workflow_name, trigger, frequency, and every step action
       in the language named by `target_language`. When target_language is
       "Hindi", use Devanagari script. Brand names, model numbers, file names,
       URLs, and numeric measurements stay verbatim in their original script.
    """

    sop_json: str = dspy.InputField(
        desc="The full SOP as JSON — title, description, steps with their tools/checks"
    )
    target_language: str = dspy.InputField(
        desc='Output language label, e.g. "English" or "Hindi". When "Hindi" every workflow_name / trigger / frequency / step action MUST be written in Devanagari.'
    )

    workflows_json: str = dspy.OutputField(
        desc=(
            'JSON array. Each workflow: '
            '{"workflow_name":"...","type":"recurring" or "event_based",'
            '"trigger":"specific event or scheduled cadence",'
            '"frequency":"daily|weekly|monthly|per occurrence",'
            '"steps":[{"step_id":1,"action":"verb-first one-line action"}]}'
        )
    )


class ChecklistExtraction(dspy.Signature):
    """Convert a Standard Operating Procedure into operationally usable checklists.

    Checklists are for VERIFICATION on the floor — every item must be answerable
    yes/no without judgement. They are NOT a paraphrase of SOP steps.

    Group into three contexts:
      - opening   — pre-execution readiness (workspace, tools, materials)
      - execution — quality / correctness checks during the procedure
      - closing   — post-execution sign-off (cleanup, verification, handover)

    HARD RULES:
    1. Every item is short (one clause), unambiguous, verifiable yes/no.
       Wrong: "Check the equipment carefully" (vague, not yes/no answerable).
       Right: "Pressure gauge reads between 80–100 psi".
       Wrong: "Make sure everything is ready" (can't be answered yes/no).
       Right: "All required tools are on the workbench".
    2. NO duplicate items across contexts.
    3. NO inferred steps (don't add "ensure safety glasses" if the SOP never
       mentions them).
    4. NO long sentences. If you need "and" between clauses, split the item.
    5. Required=true for safety/compliance/blocking checks; required=false for
       advisory checks. Default to true when unsure.
    6. If a context has no real checks, OMIT that checklist (do not invent items
       to fill it).
    7. LANGUAGE: Write checklist_name and every item string in the language
       named by `target_language`. When target_language is "Hindi", use
       Devanagari script. Brand names, model numbers, file names, URLs, and
       numeric measurements stay verbatim in their original script. The
       `context` value stays as the literal "opening" / "execution" / "closing"
       — it is a machine identifier, not user-facing text.
    """

    sop_json: str = dspy.InputField(
        desc="The full SOP as JSON — title, description, steps with their tools/checks"
    )
    target_language: str = dspy.InputField(
        desc='Output language label, e.g. "English" or "Hindi". When "Hindi" every checklist_name and item MUST be written in Devanagari.'
    )

    checklists_json: str = dspy.OutputField(
        desc=(
            'JSON array. Each checklist: '
            '{"checklist_name":"...",'
            '"context":"opening" or "execution" or "closing",'
            '"items":[{"item":"short verifiable check","required":true}]}'
        )
    )


class FrameWindowTransition(dspy.Signature):
    """Describe what physically changed across a sliding window of frames.

    Used for atomic_simple tasks. Reads the prior + current + next frame text
    descriptions and emits the transition between them — NOT a re-summary of
    the current frame.

    HARD RULES:
    - Look at the prior, current, and next frame descriptions side-by-side.
    - Describe ONLY what physically changed: hand position, object position,
      object state, interaction transitions.
    - Use literal action descriptions ("the person lifts the bottle toward
      the mouth"). Do NOT infer goals or intentions ("the person prepares
      to drink water").
    - Do NOT summarise multiple distinct changes into one — list each
      change separately.
    - If nothing changed (same hand, same object, same state), output
      'no change' for primary_action and confidence ≤ 0.3.
    - When prior_frame is empty, treat current_frame as the first transition.
    """

    frame_num: int = dspy.InputField()
    total_frames: int = dspy.InputField()
    prior_frame: str = dspy.InputField(desc="Description of frame N-1, or '' if first")
    current_frame: str = dspy.InputField(desc="Description of frame N")
    next_frame: str = dspy.InputField(desc="Description of frame N+1, or '' if last")

    transition: str = dspy.OutputField(
        desc="What physically changed between the frames — literal, not interpretive"
    )
    primary_action: str = dspy.OutputField(
        desc="Single verb-first action visible in this transition (e.g. 'rotate bottle cap counterclockwise'). 'no change' if static."
    )
    objects: str = dspy.OutputField(
        desc="Comma-separated list of objects involved in the change. Primary manipulated object first. Empty if none."
    )
    actor: str = dspy.OutputField(
        desc="Body part performing the action when visible (e.g. 'right hand', 'left index finger'). Empty if not identifiable."
    )
    motion: str = dspy.OutputField(
        desc=(
            "Motion direction when visible. EXACTLY one of: 'rotate clockwise', "
            "'rotate counterclockwise', 'lift upward', 'lower downward', "
            "'slide left', 'slide right', 'push forward', 'pull backward', "
            "'press down', 'pour', 'wipe', 'twist', 'none'. Use 'none' if unclear."
        )
    )
    contact_event: str = dspy.OutputField(
        desc=(
            "Whether a contact event happens in this transition. EXACTLY one of: "
            "'contact_start', 'contact_end', 'contact_continuous', 'none'. "
            "A contact_start = hand newly touches an object; contact_end = hand "
            "releases an object."
        )
    )
    state_change: str = dspy.OutputField(
        desc=(
            "Visible object state change as 'previous→new' (e.g. 'cap attached→removed', "
            "'tap off→on', 'bottle empty→filling'). Empty if no state change."
        )
    )
    confidence: str = dspy.OutputField(desc="Float 0.0-1.0 — how clearly the transition is visible")


class ActionTimelineSynthesis(dspy.Signature):
    """Build a temporally-ordered action timeline from window observations.

    HARD RULES:
    1. Each visible state change becomes its OWN timeline entry. Do NOT merge
       distinct micro-actions even if they feel obvious.
    2. Maintain strict chronological order (by frame_num).
    3. Use verb-first phrases (pick up, rotate, place, open, close, pour, etc.).
    4. Preserve every transition — DO NOT abstract upward (don't write
       'fill bottle'; keep 'pick up bottle', 'open cap', 'place under tap',
       'turn on tap', 'wait for fill', 'turn off tap', 'close cap').
    5. Drop window entries whose primary_action is 'no change' or whose
       confidence is below 0.25.
    6. Estimate a timestamp string from the frame_num: assume even spacing
       across the video. If total runtime is unknown, use 'frame N' format.
    """

    window_observations: str = dspy.InputField(
        desc="JSON array: [{frame_num, transition, primary_action, objects, confidence}]"
    )
    total_frames: int = dspy.InputField(
        desc="Total frame count (used to derive relative timestamps)"
    )

    timeline_json: str = dspy.OutputField(
        desc=(
            'JSON array of timeline entries. Each entry MUST preserve the source '
            'window\'s motion / contact_event / state_change fields when present: '
            '{"timestamp":"00:06","action":"rotate bottle cap counterclockwise",'
            '"objects":["bottle","bottle_cap"],"motion":"rotate counterclockwise",'
            '"contact_event":"contact_continuous","state_change":"cap attached→loosening",'
            '"confidence":0.93,"frame_num":6}'
        )
    )


class ObjectStateChanges(dspy.Signature):
    """Extract explicit object state-change records from an action timeline.

    HARD RULES:
    - Output ONE record per visible state change.
    - State words include: attached/removed, on/off, open/closed,
      empty/filling/full, stationary/moving, in/out, dirty/clean, raised/lowered.
    - Do NOT infer state changes that are not implied by the timeline actions.
    - Anchor each record to its source frame_num.
    - If no object state changes are evident, return [].
    """

    timeline_json: str = dspy.InputField(
        desc="JSON array of timeline entries (frame_num, action, objects, ...)"
    )

    state_changes_json: str = dspy.OutputField(
        desc=(
            'JSON array. Each record: '
            '{"object":"bottle_cap","previous_state":"attached",'
            '"new_state":"removed","timestamp":"00:04","frame_num":4}'
        )
    )


class TimelineToSOP(dspy.Signature):
    """Format an action timeline + state changes into an SOP. FORMATTER, not synthesiser.

    This stage MUST NOT invent. It cleans wording, derives titles, and renders
    the timeline as SOPSchema-compatible steps. It does NOT merge, skip, or
    introduce new actions.

    HARD RULES:
    1. Output ONE SOP step per timeline entry. Do NOT merge or skip entries.
    2. Use the timeline's action wording verbatim (only minor grammar cleanup).
    3. Each step's evidence cites the source frame_num as 'Frame X'.
    4. Each step's objects come from the timeline entry — no additions.
    5. Preserve chronological order by frame_num.
    6. Each step's instruction is a verb-first imperative clause derived from
       the action ("Pick up the bottle.", "Open the cap.").
    7. The 'title' field is a short imperative (e.g. 'Fill a water bottle').
    8. SOURCE FRAME (mandatory): every step MUST include a `source_frame_num`
       integer equal to the timeline entry's frame_num — this is the picture
       the operator will see for the step. Values must be STRICTLY INCREASING
       because we emit one step per timeline entry; no two steps share a
       frame.
    """

    timeline_json: str = dspy.InputField(desc="JSON array of timeline entries")
    state_changes_json: str = dspy.InputField(
        desc="JSON array of object state-change records (informational; do not invent)"
    )

    title: str = dspy.OutputField(desc="Short imperative title")
    summary: str = dspy.OutputField(desc="One sentence")
    steps_json: str = dspy.OutputField(
        desc=(
            'JSON array. Each step: '
            '{"step_number":1,"title":"2-3 words","instruction":"verb-first clause",'
            '"objects":[],"checks":[],"evidence":["Frame X"],'
            '"source_frame_num":1,"confidence":0.9,"notes":null}. '
            'source_frame_num MUST equal the timeline entry frame_num.'
        )
    )
    overall_confidence: str = dspy.OutputField(desc="Float 0.0-1.0")
    warnings_json: str = dspy.OutputField(desc='JSON array of warning strings')


class TextOnlySOPSynthesis(dspy.Signature):
    """Generate a Standard Operating Procedure from a transcript alone (no video frames).

    RULES (enforce strictly):
    1. Use ONLY what is explicitly stated or operationally described in the
       transcript.
    2. Treat narrated processes, explanations, meetings, planning sessions,
       reviews, customer-service work, documentation tasks, and software work
       as valid SOP material when they describe repeatable actions.
    3. Do NOT invent steps, tools, or actions not mentioned.
    4. If a detail is unclear but the work is process-like, create the safest
       supported step and mark notes='partially supported'. Omit only details
       that have no transcript support.
    5. Maintain the temporal order described in the transcript.
    """

    transcript: str = dspy.InputField(
        desc="Full transcript/narration from the video"
    )

    title: str = dspy.OutputField(
        desc="Specific title matching the procedure described"
    )
    summary: str = dspy.OutputField(
        desc="One sentence describing what this procedure accomplishes"
    )
    steps_json: str = dspy.OutputField(
        desc=(
            'JSON array of steps. Each step: '
            '{"step_number":1,"title":"2-4 words","instruction":"precise action",'
            '"objects":["tool"],"checks":["verification"],'
            '"evidence":["transcript"],"confidence":0.9,"notes":"or null"}'
        )
    )
    overall_confidence: str = dspy.OutputField(
        desc="Float 0.0–1.0 representing overall SOP confidence"
    )
    warnings_json: str = dspy.OutputField(
        desc='JSON array of strings — one per missing, unclear, or low-confidence step'
    )
