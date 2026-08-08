// PLACEHOLDER — no auth/user model exists yet (RBAC deferred, see
// docs/9-open-questions.md). Single source for the stand-in Finance-user
// identity shown in the UI chrome (Sidebar profile card, logout toast) —
// isolated here instead of scattered literal strings in each component, so
// it's unmistakably a placeholder and a one-line swap once real auth lands.
export const PLACEHOLDER_CURRENT_USER = {
  initials: 'P',
  name: 'Pankaj',
  role: 'Finance Admin',
};
