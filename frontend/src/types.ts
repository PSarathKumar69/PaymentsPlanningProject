export type NavItemKey = 'main' | 'planning' | 'analytics' | 'configuration';

// ---- Vendors (backend/api/schemas/vendors.py) ------------------------------

export interface Vendor {
  id: number;
  erp_code: string;
  entity: string;
  vendor_name: string;
  opening_balance: number;
  live_outstanding_balance: number;
  category: string; // must_pay | commitment | normal | inactive
  commitment_months: number | null;
  assigned_week: number | null;
  payment_status: string; // not_paid | partial | paid_in_full
  paid_so_far_this_month: number;
  score: number | null;
  current_aging_bucket: string | null;
  reconsider: boolean | null;
  override_amount: number | null;
  priority_tag: string | null;
}

export interface MonthlyBreakdownRow {
  month: string; // ISO date, first-of-month
  label: string;
  amount: number;
  payable: number;
}

export interface MinFundsTranche {
  month: string;
  remaining_amount: number;
  is_leftover: boolean;
}

export interface VendorAging {
  oldest_tranche_month: string | null;
  oldest_bucket: string | null;
  oldest_bucket_months_back: number | null;
  bucket_balances: Record<string, number>;
  total_outstanding: number;
  oldest_bucket_amount: number;
  oldest_bucket_amount_opening: number;
  monthly_breakdown: MonthlyBreakdownRow[];
  week_actual_paid: Record<string, number>;
  min_funds_required: number;
  min_funds_tranches: MinFundsTranche[];
}

export interface Payment {
  id: number;
  vendor_id: number;
  payment_date: string; // ISO date (YYYY-MM-DD)
  amount: number;
  week: number | null;
  note: string | null;
}

export interface VendorCategoryOption {
  value: string;
  label: string;
}

export interface VendorPaymentTracking {
  vendor_id: number;
  erp_code: string;
  vendor_name: string;
  category: string;
  outstanding: number;
  budget: number;
  actual_paid_this_month: number;
  balance: number;
  within_week_order: number | null;
  balance_outstanding: number;
  min_funds_required: number;
}

// ---- New Model 2 (backend/api/schemas/new_model_2.py) ----------------------

export interface NewModel2MinimumFundsRequired {
  total: number;
  breakdown: Array<{ vendor_id: number; required_amount: number; [k: string]: unknown }>;
  planning_month: string;
  as_of: string;
  planning_month_warning: string | null;
}

export interface SuggestedPlanningMonth {
  last_recorded_month: string | null;
  suggested_planning_month: string | null;
}

export interface CurrentPlanningMonth {
  planning_month: string | null;
}

export interface AllVendorMinFundsRequired {
  planning_month: string | null;
  as_of: string | null;
  breakdown: Array<{ vendor_id: number; required_amount: number }>;
}

export interface VendorMinFundsRequired {
  total: number;
  rule: string;
  category: string;
  current_month: string | null;
  current_amount: number | null;
  oldest_month: string | null;
  oldest_amount: number | null;
  second_month: string | null;
  second_amount: number | null;
  opening_balance: number | null;
  live_balance: number | null;
  commitment_months: number | null;
  planning_month: string;
  as_of: string;
}

// Loose — plan.allocations/weekly_view.detail are `list[dict[str, Any]]`
// server-side (pandas-record dumps), so these carry the fields this app
// actually reads and tolerate extra ones.
export interface PlanAllocationRow {
  vendor_id: number;
  erp_code?: string;
  vendor_name?: string;
  category?: string;
  outstanding_balance?: number;
  required_amount?: number;
  rule?: string;
  priority_tag?: string | null;
  allocated_amount: number;
  status?: string;
  [k: string]: unknown;
}

export interface WeeklyViewDetailRow {
  vendor_id: number;
  plan_allocation_id: number | null;
  assigned_week?: number;
  within_week_order: number | null;
  override_amount: number | null;
  effective_amount: number;
  week_distribution_plan: Record<string, number> | null;
  week_actual_paid: Record<string, number> | null;
  [k: string]: unknown;
}

export interface NewModel2Plan {
  available_funds: number;
  guaranteed_total: number;
  escalation: boolean;
  escalation_shortfall: number;
  bucket_ceiling: Record<string, number>;
  bucket_pct: Record<string, number>;
  exceptional_shortfall: boolean;
  // Real post-allocation "money left over" figure — what the "Funds Left"
  // card should show. NOT the same as funds_left_for_regeneration below
  // (a different, pre-allocation figure).
  leftover_remaining: number;
  allocations: PlanAllocationRow[];
}

export interface WeeklyView {
  weekly_summary: Array<Record<string, unknown>>;
  detail: WeeklyViewDetailRow[];
  plan_run_id: number | null;
}

export interface NewModel2PlanAndWeeklyView {
  plan: NewModel2Plan;
  weekly_view: WeeklyView;
  total_overridden: number;
  funds_left_for_regeneration: number;
}

export interface ResponsibleVendor {
  vendor_id: number;
  erp_code: string;
  vendor_name: string;
  suggested_amount: number;
  override_amount: number;
}

export interface FinalizeCheckResponse {
  ok: boolean;
  total_committed: number;
  available_funds: number;
  over_by: number;
  responsible_vendors: ResponsibleVendor[];
  vendor_count: number;
}

export interface ResetCycleResponse {
  planning_month: string;
  deleted_plan_runs: number;
  deleted_allocations: number;
  vendors_reset: number;
}

// ---- Plan-run history (backend/api/schemas/plan_runs.py) -------------------

export interface PlanRunAllocation {
  plan_allocation_id: number;
  vendor_id: number;
  assigned_week: number;
  within_week_order: number | null;
  allocated_amount: number;
  override_amount: number | null;
  required_amount_snapshot: number | null;
}

export interface PlanRun {
  plan_run_id: number;
  created_at: string;
  month: string;
  model_used: string;
  funds_figure: number | null;
  min_funds_required: number | null;
  leftover_remaining: number | null;
  allocations: PlanRunAllocation[];
}

export interface PlanRunHistory {
  plan_runs: PlanRun[];
  vendor_week_distribution_plans: Record<string, Record<string, number> | null>;
}

// ---- Plan allocations (backend/api/schemas/plan_allocations.py) -----------

export interface FundsWarning {
  total_committed: number;
  available_funds: number;
  over_by: number;
}

export interface OverrideResult {
  plan_allocation_id: number;
  vendor_id: number;
  allocated_amount: number;
  override_amount: number | null;
  effective_amount: number;
  funds_warning: FundsWarning | null;
}

export interface WeekDistributionResult {
  plan_allocation_id: number;
  vendor_id: number;
  week_distribution_plan: Record<string, number>;
}

// ---- Master data (backend/api/schemas/master_data.py) ----------------------

export interface MasterDataCommitResult {
  vendor_count: number;
  ledger_row_count: number;
  allocation_count: number;
  data_quality_notes: string[];
  unmapped_columns: string[];
  new_vendor_count: number;
  new_vendors: string[];
  existing_vendor_count: number;
  missing_vendor_count: number;
  missing_vendors: string[];
  vendors_with_changed_ledger_count: number;
  vendors_with_changed_ledger: string[];
  changed_fields_by_vendor: Record<string, string[]>;
  vendors_changed: number;
  ai_column_mapping_messages: string[];
  // Merge-rollover-into-upload task: whether this upload's own re-ingestion
  // landed a genuinely new month and reset every vendor's payment-cycle
  // state — false/0 for the ordinary same-month-correction and
  // first-upload-ever cases, never an error either way.
  cycle_reset: boolean;
  vendors_reset: number;
}

export interface MasterDataRevertResult {
  vendor_count: number;
  ledger_row_count: number;
  allocation_count: number;
  data_quality_notes: string[];
  unmapped_columns: string[];
  warning: string;
}

export interface ExtraField {
  column_name: string;
  widget: 'toggle' | 'dropdown' | 'text';
  options: string[] | null;
  values_by_vendor_id: Record<number, string | null>;
}

export interface GridColumn {
  header: string;
  kind: string; // "known" | "extra"
  editable: boolean;
  month?: string | null;
  week_number?: number | null;
  field?: string | null;
  column_name?: string | null;
}

export interface GridVendorRow {
  vendor_id: number;
  erp_code: string;
  values: Record<string, unknown>;
}

export interface DuplicateDroppedRow {
  erp_code: string;
  vendor_name: unknown;
  row: number;
}

export interface MasterGrid {
  columns: GridColumn[];
  vendors: GridVendorRow[];
  extra_field_widgets: Record<string, { widget: string; options: string[] | null }>;
  // False before any vendor has ever been ingested, or if the master file
  // has since gone missing — columns/vendors are then both empty, but
  // that's a consequence of this flag, not the signal itself.
  has_data: boolean;
  // Go-live "show every row" task: ERP codes the sheet had duplicated —
  // the live (winning) vendor row for each is flagged red in the grid.
  duplicate_erp_codes: string[];
  // Every row that did NOT win (i.e. was silently dropped by last-row-wins).
  duplicate_dropped_rows: DuplicateDroppedRow[];
}

// ---- Configuration (backend/api/schemas/configuration.py) ------------------

export interface PriorityBucket {
  bucket_key: string;
  display_label: string;
  category_name: string;
  ceiling_pct: number;
  floor_pct: number;
  rotation_position: number;
  // Pinned-role task (2026-08-07): false only for the Must Pay/Commitment
  // guaranteed-funding rows, however Finance has renamed them — key off
  // this, never a hardcoded "P0"/"P1" bucket_key check.
  deletable: boolean;
}

// ---- Audit log (backend/api/routers/audit_log.py) --------------------------

export interface AuditLogEntry {
  id: number;
  timestamp: string;
  vendor_id: number | null; // null for system-level entries (e.g. priority-bucket edits, column-mapping decisions)
  vendor_name: string | null; // server-side joined against vendors — null whenever vendor_id is null
  erp_code: string | null;
  field_name: string;
  old_value: string | null;
  new_value: string | null;
  source: string;
  changed_by: string | null;
}

export interface AuditLogListResult {
  items: AuditLogEntry[];
  total: number;
}

export interface AuditLogQuery {
  vendor_id?: number;
  search?: string;
  source?: string;
  date_from?: string; // ISO date (YYYY-MM-DD)
  date_to?: string;
  limit?: number;
  offset?: number;
}

// ---- Analytics (backend/api/schemas/analytics.py) --------------------------

export interface AnalyticsHistoryPoint {
  month: string; // ISO date, first-of-month
  payable: number;
  payment: number;
}

export interface AnalyticsMonthlyBreakdownRow {
  months_back: number;
  month: string; // ISO date, first-of-month
  label: string;
  amount: number; // still-owed balance (post-FIFO)
  payable: number;
  payment: number;
}

export interface AnalyticsVendor {
  vendor_id: number;
  erp_code: string;
  vendor_name: string;
  outstanding_balance: number;
  aging_buckets: Record<string, number>;
  oldest_bucket: string | null;
  oldest_bucket_months_back: number | null;
  oldest_bucket_amount: number;
  // Aligned 1:1 with AnalyticsDashboard.months — same length, same order.
  history: AnalyticsHistoryPoint[];
  // Oldest -> newest, every calendar month incl. zero-balance ones —
  // drives the vendor detail modal's per-month list.
  monthly_breakdown: AnalyticsMonthlyBreakdownRow[];
}

export interface AnalyticsMonthAggregate {
  month: string;
  total_payable: number;
  total_payment: number;
  cumulative_debt: number;
  cumulative_paid: number;
}

export interface AnalyticsDashboard {
  vendors: AnalyticsVendor[];
  // Dynamic length — sheet_start_month through the latest recorded ledger
  // month, never a fixed count (CLAUDE.md rule 7).
  months: string[];
  aggregates: AnalyticsMonthAggregate[];
  aging_totals: Record<string, number>;
  // KPI-card revamp (this task) — replaces the old Overall Debt/Overall
  // Paid to Date cards. Keyed by category: must_pay/commitment/normal/
  // inactive — normal already sums P2/P3/P4 vendors together.
  total_outstanding: number;
  outstanding_by_category: Record<string, number>;
}

export interface FundsTrendPoint {
  month: string;
  available_funds: number;
  min_funds_required: number;
}

export interface FundsTrend {
  trend: FundsTrendPoint[];
}

// ---- AI companion (backend/api/schemas/ai_layer.py) ------------------------

export interface VendorTalkingPointsResult {
  vendor_id: number;
  erp_code: string;
  vendor_name: string;
  category: string;
  priority_tag: string | null;
  status: string;
  required_amount: number;
  allocated_amount: number;
  cut_from_full: boolean;
  aging_bucket: string | null;
  script_text: string;
}

export interface TalkingScript {
  vendor_id: number;
  erp_code: string;
  vendor_name: string;
  script_text: string;
}
