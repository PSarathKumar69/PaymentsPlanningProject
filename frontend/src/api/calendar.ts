import { api } from './client';

// Backs the Assigned-Week dropdown's option count — CLAUDE.md rule 7: the
// number of weeks in a month must be calendar-derived, never hardcoded to
// always be 5 (e.g. Feb has only 4).
export const getWeeksInMonth = (month: string) =>
  api.get<{ month: string; weeks: number }>(`/calendar/weeks-in-month?month=${month}`);
