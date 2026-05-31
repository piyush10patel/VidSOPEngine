export type Tone = 'neutral' | 'blue' | 'green' | 'yellow' | 'red' | 'purple';

export const statusTone = {
  ready: 'green',
  draft: 'yellow',
  archived: 'neutral',
  uploaded: 'blue',
  transcribing: 'yellow',
  sop_generating: 'purple',
  completed: 'green',
  failed: 'red',
  not_started: 'neutral',
  in_progress: 'blue',
  overdue: 'red',
  low: 'neutral',
  medium: 'yellow',
  high: 'red',
} as const;

// Single time zone for every operator-facing timestamp in the UI.
// Backend stores UTC; frontend renders IST so a shift planned for 9am
// IST never shifts based on the viewer's device clock.
export const IST_TIMEZONE = 'Asia/Kolkata';

function istLocale(): string {
  // Pick the localized formatting (en-IN / hi-IN) but anchor TZ to IST.
  if (typeof navigator !== 'undefined' && navigator.language?.startsWith('hi')) {
    return 'hi-IN';
  }
  return 'en-IN';
}

function istToday(): string {
  return new Date().toLocaleDateString('en-IN', { timeZone: IST_TIMEZONE });
}

function istDateString(date: Date): string {
  return date.toLocaleDateString('en-IN', { timeZone: IST_TIMEZONE });
}

export function formatShortDate(value?: string | null) {
  if (!value) return 'Not set';
  return new Date(value).toLocaleDateString(istLocale(), {
    month: 'short',
    day: 'numeric',
    timeZone: IST_TIMEZONE,
  });
}

export function formatShortTime(value?: string | null) {
  if (!value) return 'No time';
  return new Date(value).toLocaleTimeString(istLocale(), {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: IST_TIMEZONE,
  });
}

export function formatDateTime(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  const datePart = date.toLocaleDateString(istLocale(), {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    timeZone: IST_TIMEZONE,
  });
  const timePart = date.toLocaleTimeString(istLocale(), {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: IST_TIMEZONE,
  });
  return `${datePart}, ${timePart} IST`;
}

export function formatDue(value?: string | null) {
  if (!value) return 'No due time';
  const date = new Date(value);
  const today = istToday();
  const tomorrow = istDateString(new Date(Date.now() + 24 * 60 * 60 * 1000));
  const dateKey = istDateString(date);
  const day =
    dateKey === today
      ? 'Today'
      : dateKey === tomorrow
        ? 'Tomorrow'
        : date.toLocaleDateString(istLocale(), { month: 'short', day: 'numeric', timeZone: IST_TIMEZONE });
  return `${day}, ${date.toLocaleTimeString(istLocale(), { hour: '2-digit', minute: '2-digit', timeZone: IST_TIMEZONE })} IST`;
}

