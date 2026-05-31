export type FeatureFlag =
  | 'ops_mobile_navigation'
  | 'ops_command_center'
  | 'inline_step_corrections'
  | 'execution_focus_panel';

const defaults: Record<FeatureFlag, boolean> = {
  ops_mobile_navigation: true,
  ops_command_center: true,
  inline_step_corrections: true,
  execution_focus_panel: true,
};

export function isFeatureEnabled(flag: FeatureFlag) {
  if (typeof window === 'undefined') return defaults[flag];
  const override = window.localStorage.getItem(`vidsopengine.flag.${flag}`);
  if (override === 'true') return true;
  if (override === 'false') return false;
  return defaults[flag];
}

export const featureFlags = defaults;
