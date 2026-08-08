// Shared formatting helpers — one copy, not re-typed per component
// (mirrors test_ui.html's own single-file formatMoney()/formatPct()).

// Bug fix: a value like -0.0044997 (float noise around zero, not a real
// shortfall) rounds to JS's signed -0, and (-0).toLocaleString('en-IN')
// renders the literal string "-0" — Math.round alone doesn't strip the sign
// bit. Normalizing a zero-rounded result to plain 0 (0 === -0 in JS) drops
// the stray minus sign while leaving every real negative amount untouched.
export const formatMoney = (n: number | null | undefined) => {
  if (n == null || Number.isNaN(n)) return '—';
  const rounded = Math.round(n);
  return `₹${(rounded === 0 ? 0 : rounded).toLocaleString('en-IN')}`;
};

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

// "2026-07-22" -> "22 Jul 26" — day-level (payment history, audit log),
// distinct from the month-level formatters above.
export const formatDateShort = (iso: string) => {
  const [y, m, d] = iso.split('-').map(Number);
  if (!y || !m || !d) return iso;
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: '2-digit', timeZone: 'UTC' });
};

// Full timestamp (audit log) — "2026-07-22T14:03:00" -> "22 Jul 26, 14:03".
export const formatTimestamp = (iso: string) => {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: '2-digit' })}, ${d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}`;
};

// Audit log old_value/new_value formatting — only field_name values known
// to actually hold money/structured data get special treatment; everything
// else (category/priority_tag are already clean Finance-facing text from
// the backend, free-text fields, descriptive sentences) is shown as-is.
// Never guess "amount" formatting from a field's name — verify against the
// real field_name (docs/11 Configuration-module task).
export const formatAuditValue = (fieldName: string, value: string | null): string => {
  if (value == null) return '—';
  if (fieldName === 'vendor.override_amount') {
    const n = Number(value);
    return Number.isNaN(n) ? value : formatMoney(n);
  }
  if (fieldName === 'plan_allocation.week_distribution_plan') {
    try {
      const weeks = JSON.parse(value) as Record<string, number>;
      return Object.entries(weeks).map(([week, amount]) => `W${week}: ${formatMoney(amount)}`).join(', ');
    } catch {
      return value;
    }
  }
  return value;
};
