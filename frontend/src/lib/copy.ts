/**
 * Operational terminology — single source of truth for user-facing strings.
 *
 * Per docs/ux.md: the product is NOT an AI tool. Every label here replaces
 * an internal/technical term with operational language a frontline worker
 * would recognize.
 *
 * Import these instead of hardcoding strings:
 *
 *   import { TERMS } from '@/lib/copy';
 *   <Heading>{TERMS.instructions}</Heading>          // not "SOP"
 *   <Button>{TERMS.actions.createInstructions}</Button>  // not "Generate SOP"
 *
 * When adding new strings: prefer existing entries; if you must add a new
 * one, follow the rule "would a delivery driver / shop owner recognize this?"
 */

export const TERMS = {
  // Entity labels (user-facing)
  instructions: 'Instructions',
  instructionsDocument: 'Instructions document',
  workflow: 'Workflow',
  checklist: 'Checklist',
  training: 'Training',
  staffTraining: 'Staff training',
  operation: 'Operation',
  operations: 'Operations',
  task: 'Task',
  tasks: 'Tasks',
  step: 'Step',
  videoSource: 'Source video',

  // Granularity / mode
  simpleTask: 'Simple task',
  stepByStepProcess: 'Step-by-step process',
  screenRecording: 'Screen recording',
  realWorldProcess: 'Real-world process',

  // Statuses
  status: {
    notStarted: 'Not started',
    inProgress: 'In progress',
    processing: 'Processing',
    done: 'Done',
    completed: 'Completed',
    overdue: 'Overdue',
    failed: 'Failed',
    needsReview: 'Needs review',
    archived: 'Archived',
    active: 'Active',
    draft: 'Draft',
  },

  // Actions (button labels)
  actions: {
    createInstructions: 'Create Instructions',
    createStaffTraining: 'Create Staff Training',
    processVideo: 'Process Video',
    buildWorkflow: 'Build Workflow from Instructions',
    runNow: 'Run now',
    startTask: 'Start task',
    continue: 'Continue',
    markComplete: 'Mark complete',
    nextStep: 'Next step',
    finishRun: 'Finish run',
    addEvidence: 'Add evidence',
    addNote: 'Add note',
    addPhoto: 'Add photo',
    upload: 'Upload',
    saveChanges: 'Save changes',
    finalize: 'Finalize',
    retry: 'Try again',
    cancel: 'Cancel',
    edit: 'Edit',
    archive: 'Archive',
    duplicate: 'Duplicate',
    flagAsWrong: 'Mark as wrong',
    sendCorrection: 'Send correction',
  },

  // Empty-state copy
  empty: {
    operations: {
      headline: 'No operations yet',
      helper:
        'Bundle a workflow + checklist + training into one runnable operation your team can execute.',
      cta: 'Create your first operation',
    },
    today: {
      headline: 'Quiet day',
      helper:
        'No tasks assigned to you right now. Browse operations or start a new run.',
      cta: 'Open operations',
    },
    workflows: {
      headline: 'No workflows yet',
      helper: 'Workflows are step-by-step routines your team runs on a schedule.',
      cta: 'Create a workflow',
    },
    checklists: {
      headline: 'No checklists yet',
      helper:
        'Checklists are quick yes/no verifications — opening checks, closing checks, audits.',
      cta: 'Create a checklist',
    },
    training: {
      headline: 'No training yet',
      helper:
        'Training modules teach new staff a procedure with sections, scenarios, and an assessment.',
      cta: 'Create training',
    },
    videos: {
      headline: 'No videos yet',
      helper:
        'Upload a phone video of any operational procedure and we\'ll generate the instructions, workflow, and training in under a minute.',
      cta: 'Upload your first video',
    },
    runs: {
      headline: 'No runs yet',
      helper: 'Run history will appear here as your team completes tasks.',
      cta: '',
    },
  },

  // Error messages (user-facing — never show stack traces)
  errors: {
    pipelineFailed:
      "We couldn't finish processing this video. Try again in a minute, or upload a different file.",
    videoNotFound: "This video isn't available anymore.",
    sopNotFound: "Instructions haven't been generated yet for this video.",
    invalidFormat:
      'We only support MP4, MOV, AVI, and MKV. Try a different file.',
    fileTooLarge: 'That file is too big — please keep it under 500 MB.',
    providerBusy:
      "Our AI service is busy right now. We've paused new processing for a moment. Try again shortly.",
    generic: 'Something went wrong. Try again — if it keeps happening, contact support.',
    networkError:
      "Couldn't reach the server. Check your connection and try again.",
    unauthorized: 'Please sign in again.',
  },

  // Reliability copy (loading / saving / processing states)
  reliability: {
    saving: 'Saving…',
    saved: 'Saved',
    processing: 'Processing…',
    stillWorking: 'Still working — this can take up to a minute',
    uploadingVideo: 'Uploading video…',
    watchingVideo: 'Watching the video…',
    writingSteps: 'Writing the steps…',
    almostThere: 'Almost there…',
    done: 'Done',
  },

  // Brand
  brand: {
    name: 'VidSOPEngine',
    tagline: 'Video to SOP, in minutes',
  },
} as const;

/**
 * Map a backend AppException error code to user-facing copy.
 * Falls back to a generic message for unknown codes.
 */
export function operationalErrorMessage(errorCode?: string): string {
  if (!errorCode) return TERMS.errors.generic;
  switch (errorCode) {
    case 'PIPELINE_FAILED':
    case 'SOP_GENERATION_FAILED':
    case 'TRANSCRIPTION_FAILED':
      return TERMS.errors.pipelineFailed;
    case 'VIDEO_NOT_FOUND':
      return TERMS.errors.videoNotFound;
    case 'SOP_NOT_FOUND':
      return TERMS.errors.sopNotFound;
    case 'INVALID_FILE_FORMAT':
      return TERMS.errors.invalidFormat;
    case 'FILE_TOO_LARGE':
      return TERMS.errors.fileTooLarge;
    case 'PROVIDER_UNAVAILABLE':
      return TERMS.errors.providerBusy;
    case 'NETWORK_ERROR':
      return TERMS.errors.networkError;
    default:
      return TERMS.errors.generic;
  }
}

/**
 * Map a video / run / job status string to operator-friendly copy.
 */
export function statusLabel(status?: string): string {
  if (!status) return '';
  switch (status) {
    case 'uploaded':
    case 'transcribing':
    case 'sop_generating':
      return TERMS.status.processing;
    case 'completed':
      return TERMS.status.done;
    case 'failed':
      return TERMS.status.failed;
    case 'in_progress':
      return TERMS.status.inProgress;
    case 'not_started':
      return TERMS.status.notStarted;
    default:
      // Fallback: title-case and replace underscores
      return status
        .replace(/_/g, ' ')
        .replace(/\b\w/g, (c) => c.toUpperCase());
  }
}
