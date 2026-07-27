// Single shared enum/constants module (CLAUDE.md rule 7) — status/category
// vocabulary defined once here, imported everywhere it's needed, never
// re-typed inline in a component. Mirrors backend/shared/enums.py's actual
// values and test_ui.html's CATEGORY_LABEL/STATUS_LABEL/badge-cat-* maps.
//
// Category list itself (must_pay/commitment/normal/inactive) has no backend
// CRUD endpoint today — flagged gap, see the report handed back to Sarath.
// Priority-bucket tags (P2-P5) DO have one (GET /config/priority-buckets)
// and must never be hardcoded here — see api/configuration.ts.

export type VendorCategory = 'must_pay' | 'commitment' | 'normal' | 'inactive';

// Named single-value constants — so a component comparing against one
// specific category (e.g. a metric-card filter) references a name, not a
// re-typed string literal (CLAUDE.md rule 7).
export const VENDOR_CATEGORY = {
  MUST_PAY: 'must_pay',
  COMMITMENT: 'commitment',
  NORMAL: 'normal',
  INACTIVE: 'inactive',
} as const satisfies Record<string, VendorCategory>;

export const CATEGORY_OPTIONS: VendorCategory[] = ['must_pay', 'commitment', 'normal', 'inactive'];

export const CATEGORY_LABEL: Record<VendorCategory, string> = {
  must_pay: 'Must pay',
  commitment: 'Commitment',
  normal: 'Normal',
  inactive: 'Inactive',
};

// test_ui.html's exact badge-cat-* colors.
export const CATEGORY_BADGE_CLASS: Record<VendorCategory, string> = {
  must_pay: 'bg-[#fdeaea] text-[#b42318]',
  commitment: 'bg-[#f2eafd] text-[#6b3fa0]',
  normal: 'bg-blue-50 text-[#0c447c]',
  inactive: 'bg-[#eef1f4] text-[#4a5568]',
};

// P0 (Must Pay)/P1 (Commitment) are auto-derived, never manually picked —
// only Normal/Inactive vendors get a real, Finance-assignable tag (from
// GET /config/priority-buckets, not this list).
export const TAGGABLE_CATEGORIES: VendorCategory[] = ['normal', 'inactive'];
export const AUTO_PRIORITY_TAG: Partial<Record<VendorCategory, string>> = { must_pay: 'P0', commitment: 'P1' };

// Fixed structural convention (backend/shared/aging.py's AGING_BUCKETS) —
// not Configuration-driven like the bucket ceiling percentages, so listing
// it here is the single source, not a re-typed literal per caller.
export const AGING_BUCKET_OPTIONS = ['0-30', '31-60', '61-90', '91-120', '120+'] as const;
export type AgingBucket = (typeof AGING_BUCKET_OPTIONS)[number];

// Freshest -> oldest severity ramp, same green-to-red convention as
// STATUS_BADGE_CLASS below.
export const AGING_BUCKET_BADGE_CLASS: Record<AgingBucket, string> = {
  '0-30': 'bg-emerald-50 text-emerald-700',
  '31-60': 'bg-lime-50 text-lime-700',
  '61-90': 'bg-amber-50 text-amber-700',
  '91-120': 'bg-orange-50 text-orange-700',
  '120+': 'bg-red-50 text-red-700',
};

export type PaymentStatus = 'not_paid' | 'partial' | 'paid_in_full';

export const STATUS_LABEL: Record<PaymentStatus, string> = {
  not_paid: 'Not paid',
  partial: 'Partial',
  paid_in_full: 'Paid in full',
};

export const STATUS_BADGE_CLASS: Record<PaymentStatus, string> = {
  not_paid: 'bg-[#fdeaea] text-[#b42318]',
  partial: 'bg-[#fff4e5] text-[#b56a00]',
  paid_in_full: 'bg-[#e6f6ec] text-[#1a7f4e]',
};

export type AllocationStatus = 'guaranteed' | 'full' | 'partial' | 'zero' | 'override_locked';

export const ALLOCATION_STATUS_LABEL: Partial<Record<AllocationStatus, string>> = {
  override_locked: 'Locked',
};

// A vendor whose latest allocation is this status gets the AI talking-script
// panel in the vendor detail modal — named here so callers don't compare
// against a bare 'zero' string literal.
export const ALLOCATION_STATUS_ZERO: AllocationStatus = 'zero';
