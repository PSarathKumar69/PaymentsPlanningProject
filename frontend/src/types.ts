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

export interface MasterGrid {
  columns: GridColumn[];
  vendors: GridVendorRow[];
  extra_field_widgets: Record<string, { widget: string; options: string[] | null }>;
}

// ---- Configuration (backend/api/schemas/configuration.py) ------------------

export interface PriorityBucket {
  bucket_key: string;
  display_label: string;
  ceiling_pct: number;
  floor_pct: number;
  rotation_position: number;
}

// ---- AI companion (backend/api/schemas/ai_layer.py) ------------------------

export interface VendorTalkingPointsResult {
  vendor_id: number;
  erp_code: string;
  vendor_name: string;
  script_text: string;
}

export interface TalkingScript {
  vendor_id: number;
  erp_code: string;
  vendor_name: string;
  script_text: string;
}
