// Shared formatting helpers — one copy, not re-typed per component
// (mirrors test_ui.html's own single-file formatMoney()/formatPct()).

export const formatMoney = (n: number | null | undefined) =>
  n == null || Number.isNaN(n) ? '—' : `₹${Math.round(n).toLocaleString('en-IN')}`;

export const formatPct = (numerator: number | null | undefined, denominator: number | null | undefined) =>
  denominator && denominator > 0 && numerator != null ? `${((numerator / denominator) * 100).toFixed(2)}%` : '—';

export const formatMonthShortYear = (iso: string) => {
  const [y, m] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString('en-US', { month: 'short', year: '2-digit', timeZone: 'UTC' });
};

// "2026-07" -> "July 2026"
export const formatMonthLong = (yyyyMm: string) => {
  const [y, m] = yyyyMm.split('-').map(Number);
  if (!y || !m) return yyyyMm;
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' });
};
