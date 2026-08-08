// Single shared enum/constants module (CLAUDE.md rule 7) — status/category
// vocabulary defined once here, imported everywhere it's needed, never
// re-typed inline in a component. Mirrors backend/shared/enums.py's actual
// values and test_ui.html's CATEGORY_LABEL/STATUS_LABEL/badge-cat-* maps.
//
// Category list is Must Pay/Commitment (fixed, backend/shared/enums.py::
// VendorCategory) plus whatever Finance has added through Configuration's
// unified Category/Priority-Tag table (Configuration-tab-rebuild task —
// categories are no longer a fixed 4-value enum; a custom one Finance types
// in, e.g. "Contractor", is a real, live value now). CATEGORY_OPTIONS/
// CATEGORY_LABEL below are used ONLY as PlanningView.tsx's same-session
// bootstrap default (P2 demo-polish task) — the real, live list comes from
// GET /vendors/categories (api/vendors.ts::getVendorCategories), per
// CLAUDE.md rule 7. Priority-bucket tags (P2-P5, extensible) fetch from
// GET /config/priority-buckets — see api/configuration.ts.

export type VendorCategory = string;

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

export const CATEGORY_LABEL: Record<string, string> = {
  must_pay: 'Must pay',
  commitment: 'Commitment',
  normal: 'Normal',
  inactive: 'Inactive',
};

// A custom category's name IS already its own display text (Finance types
// it directly, same reasoning backend/configuration/vendor_edits.py's
// _excel_cell_value() uses) — unlike Must Pay/Commitment/Normal/Inactive's
// historical value-vs-label split, so the fallback is just the value itself.
export const categoryLabel = (value: string): string => CATEGORY_LABEL[value] ?? value;

// On-theme palette (UI polish task) — the app's one real brand color is the
// forest green used everywhere else (#107c41 buttons/active sidebar state,
// #0e7a45 on Analytics), so these badges now live within that green +
// neutral-gray family instead of the old red/purple/blue/gray mix, while
// staying visually distinct: the two "wants attention" categories get green
// tints (deeper for Must Pay, lighter for Commitment), the two lower-urgency
// categories get neutral grays (darker for Normal, lightest for Inactive).
export const CATEGORY_BADGE_CLASS: Record<string, string> = {
  must_pay: 'bg-emerald-100 text-[#0d6535]',
  commitment: 'bg-emerald-50 text-[#1a7f4e]',
  normal: 'bg-gray-100 text-gray-600',
  inactive: 'bg-gray-50 text-gray-400',
};

// A category Finance adds via Configuration (Configuration-tab-rebuild
// task) has no fixed color above — picked deterministically (hash the
// name) from a small extra on-theme palette instead of a blank badge, so
// the same custom category always renders the same color across a session.
const CATEGORY_FALLBACK_BADGE_CLASSES = [
  'bg-blue-50 text-[#0c447c]',
  'bg-amber-50 text-amber-700',
  'bg-[#f2eafd] text-[#6b3fa0]',
  'bg-teal-50 text-teal-700',
  'bg-rose-50 text-rose-700',
];

export const categoryBadgeClass = (value: string): string => {
  if (CATEGORY_BADGE_CLASS[value]) return CATEGORY_BADGE_CLASS[value];
  let hash = 0;
  for (let i = 0; i < value.length; i++) hash = (hash * 31 + value.charCodeAt(i)) >>> 0;
  return CATEGORY_FALLBACK_BADGE_CLASSES[hash % CATEGORY_FALLBACK_BADGE_CLASSES.length];
};

// P0 (Must Pay)/P1 (Commitment) are auto-derived, never manually picked —
// every other category gets a real, Finance-assignable tag (from
// GET /config/priority-buckets, not a fixed list — Configuration-tab-
// rebuild task: was ['normal', 'inactive'], now any current/future
// category is taggable, so this fixed list is gone; check the LIVE
// priority-buckets list instead of this constant).
export const AUTO_PRIORITY_TAG: Record<'must_pay' | 'commitment', string> = { must_pay: 'P0', commitment: 'P1' };

// Fixed structural convention (backend/shared/aging.py's AGING_BUCKETS) —
// not Configuration-driven like the bucket ceiling percentages, so listing
// it here is the single source, not a re-typed literal per caller.
export const AGING_BUCKET_OPTIONS = ['0-30', '31-60', '61-90', '91-120', '120+'] as const;
export type AgingBucket = (typeof AGING_BUCKET_OPTIONS)[number];

// Freshest -> oldest severity ramp (UI polish task) — on-theme green/amber
// progression instead of the old lime/orange/red hues (which don't appear
// anywhere else in the app): light green -> deeper green -> amber ->
// deeper amber -> solid brand green for the most overdue bucket. Severity
// reads through increasing saturation/darkness, not a hue swap to red —
// matches VendorAnalyticsTab.tsx's own Aging Profile bars, which already
// use exactly this light-to-dark-green ramp.
export const AGING_BUCKET_BADGE_CLASS: Record<AgingBucket, string> = {
  '0-30': 'bg-emerald-50 text-emerald-700',
  '31-60': 'bg-emerald-100 text-emerald-800',
  '61-90': 'bg-amber-50 text-amber-700',
  '91-120': 'bg-amber-100 text-amber-900',
  '120+': 'bg-[#107c41] text-white',
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

export const ALLOCATION_STATUS_LABEL: Record<AllocationStatus, string> = {
  guaranteed: 'Guaranteed',
  full: 'Full',
  partial: 'Partial',
  zero: 'Zero',
  override_locked: 'Locked',
};

// Same green-to-red severity convention as STATUS_BADGE_CLASS — used by the
// AI companion card's allocation-status badge (docs: consolidated AI screen).
export const ALLOCATION_STATUS_BADGE_CLASS: Record<AllocationStatus, string> = {
  guaranteed: 'bg-[#f2eafd] text-[#6b3fa0]',
  full: 'bg-emerald-50 text-emerald-700',
  partial: 'bg-amber-50 text-amber-700',
  zero: 'bg-red-50 text-red-700',
  override_locked: 'bg-[#eef1f4] text-[#4a5568]',
};

// A vendor whose latest allocation is this status gets the AI talking-script
// panel in the vendor detail modal — named here so callers don't compare
// against a bare 'zero' string literal.
export const ALLOCATION_STATUS_ZERO: AllocationStatus = 'zero';

// ---- Audit log (docs/11 Configuration-module task) -------------------------
// AuditLog.source mirrors backend/shared/enums.py::ChangeSource exactly.

export type AuditSource =
  | 'ui_edit'
  | 'excel_upload'
  | 'ingestion'
  | 'ai_column_mapping'
  | 'ui_edit_derived'
  | 'generate_plan'
  | 'reset_cycle'
  | 'export'
  | 'funds_input'
  | 'min_funds_calc';

export const AUDIT_SOURCE_LABEL: Record<AuditSource, string> = {
  ui_edit: 'Manual edit',
  excel_upload: 'Excel re-upload',
  ingestion: 'Initial data load',
  ai_column_mapping: 'AI column mapping',
  ui_edit_derived: 'Auto-updated (linked field)',
  generate_plan: 'Generate Plan',
  reset_cycle: 'Reset',
  export: 'Export',
  funds_input: 'Funds Input',
  min_funds_calc: 'Min Funds Calc',
};

// Audit log revamp (Task B) — filter dropdown order: Finance-relevant
// planning actions first, then Excel/AI/system-level sources.
export const AUDIT_SOURCE_OPTIONS: AuditSource[] = [
  'excel_upload',
  'generate_plan',
  'reset_cycle',
  'export',
  'funds_input',
  'min_funds_calc',
  'ui_edit',
  'ingestion',
  'ai_column_mapping',
  'ui_edit_derived',
];

export const AUDIT_SOURCE_BADGE_CLASS: Record<AuditSource, string> = {
  ui_edit: 'bg-blue-50 text-[#0c447c]',
  excel_upload: 'bg-emerald-50 text-emerald-700',
  ingestion: 'bg-[#eef1f4] text-[#4a5568]',
  ai_column_mapping: 'bg-[#f2eafd] text-[#6b3fa0]',
  ui_edit_derived: 'bg-amber-50 text-amber-700',
  generate_plan: 'bg-teal-50 text-teal-700',
  reset_cycle: 'bg-orange-50 text-orange-700',
  export: 'bg-sky-50 text-sky-700',
  funds_input: 'bg-lime-50 text-lime-700',
  min_funds_calc: 'bg-violet-50 text-violet-700',
};

export const auditSourceLabel = (source: string): string => AUDIT_SOURCE_LABEL[source as AuditSource] ?? source;

export const auditSourceBadgeClass = (source: string): string =>
  AUDIT_SOURCE_BADGE_CLASS[source as AuditSource] ?? 'bg-gray-50 text-gray-600';

// AuditLog.field_name values this codebase actually writes — derived by
// grepping every `AuditLog(...)` construction call site (backend/
// configuration/vendor_edits.py, priority_bucket_edits.py, backend/api/
// routers/plan_allocations.py + plan_runs.py, backend/ingestion/
// upload.py + load_excel.py), not guessed at.
export const AUDIT_FIELD_LABEL: Record<string, string> = {
  category: 'Category',
  priority_tag: 'Priority Tag',
  commitment_months: 'Commitment Months',
  assigned_week: 'Assigned Week',
  category_priority_conflict: 'Category/Priority Tag Conflict',
  priority_bucket_order: 'Category/Priority Tag Cut Order',
  column_mapping_warning: 'AI Column Mapping Warning',
  'vendor.override_amount': 'Override Amount',
  'plan_allocation.week_distribution_plan': 'Weekly Distribution Plan',
  plan_run_deleted: 'Plan Run Deleted',
  excel_upload: 'Fields Changed (Excel Upload)',
};

// Two field_name shapes above are generated with a dynamic suffix, not a
// fixed string: "priority_bucket.<key>" (priority_bucket_edits.py) and
// "column_mapping:<field>" (upload.py's AI-mapping audit entries). Anything
// else unrecognized (e.g. a VendorExtraField's column_name — arbitrary,
// already Finance-facing Excel header text) is shown as-is.
export const auditFieldLabel = (fieldName: string): string => {
  if (fieldName in AUDIT_FIELD_LABEL) return AUDIT_FIELD_LABEL[fieldName];
  if (fieldName.startsWith('priority_bucket.')) {
    return `Priority Bucket ${fieldName.slice('priority_bucket.'.length)} Settings`;
  }
  if (fieldName.startsWith('column_mapping:')) {
    const inner = fieldName.slice('column_mapping:'.length);
    return `AI Column Mapping — ${AUDIT_FIELD_LABEL[inner] ?? inner}`;
  }
  return fieldName;
};
