"""Synthetic 45-vendor dataset for the New Model 2 end-to-end validation
exercise (predictions-before-running-the-model task, 2026-08-03).

NEVER imported by any real backend/frontend code path — this lives entirely
under test_data/, isolated from data/Vendor's Details.xlsx and
backend/db/app.db per the task's own non-negotiable isolation rule.

Design summary (see test_data/vendor_roster.md for the human-readable
version):
  - 7 Must Pay (P0), 14 Commitment (P1), 19 Normal (7 P2 / 6 P3 / 6 P4),
    5 Inactive (P5) = 45 vendors total.
  - Sheet months: Mar-26..Aug-26 (6 monthly Payable/Payment column pairs).
    Planning month = Sep-26, so as_of ("current"/0-30 bucket) = Aug-26.
  - Every Must Pay/Normal/Inactive vendor is built from one of 6 fixed
    aging "branch" shapes, each engineered to land on a specific
    required_amount_v2() rule (docs/14-new-model-2.md):
      A = v2_only_current            (no older leftover, only Aug's bill)
      B = v2_current_zero_oldest_only(Aug's bill is 0, an older month owed)
      C = v2_oldest_and_current      (oldest <=50% of Aug, adjacent, no gap)
      D = v2_oldest_and_second       (oldest <=50% of Aug, gap month between)
      E = v2_oldest_only             (oldest >50% of Aug, Aug excluded)
      F = v2_no_outstanding          (everything paid off, required = 0)
    Each branch is deliberately re-used across categories (Must Pay,
    P2/P3/P4, Inactive) with DIFFERENT rupee amounts each time — same
    rule, never the same figure twice — per the task's "don't reuse one
    template value everywhere" instruction.
  - Payments are only ever made in the SAME month as that month's own
    bill (never a later month paying down an earlier one) so each
    vendor's FIFO tranche math reduces to plain, hand-checkable
    per-month arithmetic — see build_vendor_tranches()
    (backend/shared/aging.py) for why this ordering matters.
  - Commitment vendors bypass all of that: flat opening_balance, zero
    ledger activity every month, required = opening_balance /
    commitment_months (backend/shared/min_funds.py's required_amount(),
    delegated to unchanged by required_amount_v2()).
"""
from dataclasses import dataclass, field


SHEET_START_MONTH_ISO = "2026-03-01"  # Mar-26
MONTH_LABELS = ["Mar-26", "Apr-26", "May-26", "Jun-26", "Jul-26", "Aug-26"]
PLANNING_MONTH = "2026-09"  # Finance plans for Sep-26
AS_OF_MONTH_ISO = "2026-08-01"  # planning month minus one calendar month = Aug-26


@dataclass
class VendorSpec:
    erp_code: str
    vendor_name: str
    entity: str
    category_label: str  # "Must Pay" / "Commitment" / "Normal" / "Inactive"
    priority_tag: str  # "P0".."P5"
    assigned_week: int
    op_balance: float = 0.0
    commitment_months: int | None = None
    # Six (payable, payment) pairs, Mar-26..Aug-26. None for Commitment
    # vendors (flat zero ledger, driven by op_balance/commitment_months
    # instead).
    monthly: list = field(default_factory=list)
    branch: str | None = None  # for the roster/predictions doc only
    note: str = ""


def _branch_A(current_amt):
    """v2_only_current: no bills Mar-Jul, Aug's own bill unpaid."""
    return [(0, 0)] * 5 + [(current_amt, 0)]


def _branch_B(oldest_amt):
    """v2_current_zero_oldest_only: Mar unpaid, Apr-Aug no bill at all."""
    return [(oldest_amt, 0)] + [(0, 0)] * 5


def _branch_C(oldest_amt, current_amt):
    """v2_oldest_and_current: Mar unpaid (oldest), Apr-Jul no bill, Aug
    unpaid (current) — adjacent, no gap month. Caller must ensure
    oldest_amt <= 0.5 * current_amt."""
    assert oldest_amt <= 0.5 * current_amt + 1e-9
    return [(oldest_amt, 0)] + [(0, 0)] * 4 + [(current_amt, 0)]


def _branch_D(oldest_amt, second_amt, current_amt):
    """v2_oldest_and_second: Mar unpaid (oldest), Apr/May no bill, Jun
    unpaid (the gap "second" month), Jul no bill, Aug unpaid (current,
    excluded from the total). Caller must ensure oldest_amt <= 0.5 *
    current_amt."""
    assert oldest_amt <= 0.5 * current_amt + 1e-9
    return [(oldest_amt, 0), (0, 0), (0, 0), (second_amt, 0), (0, 0), (current_amt, 0)]


def _branch_E(oldest_amt, current_amt):
    """v2_oldest_only: Mar unpaid (oldest), Apr-Jul no bill, Aug unpaid
    (current) but EXCLUDED from the total. Caller must ensure
    oldest_amt > 0.5 * current_amt."""
    assert oldest_amt > 0.5 * current_amt + 1e-9
    return [(oldest_amt, 0)] + [(0, 0)] * 4 + [(current_amt, 0)]


def _branch_F(paid_amt):
    """v2_no_outstanding: Mar billed and paid in full same month, nothing after."""
    return [(paid_amt, paid_amt)] + [(0, 0)] * 5


VENDORS = [
    # --- Must Pay (P0) — 7 vendors, all 6 branches covered -----------------
    VendorSpec("MP001", "Apex Steel Works", "Entity A", "Must Pay", "P0", 1,
               monthly=_branch_A(120_000), branch="A", note="only_current"),
    VendorSpec("MP002", "Bharat Logistics", "Entity B", "Must Pay", "P0", 2,
               monthly=_branch_B(200_000), branch="B", note="current_zero_oldest_only"),
    VendorSpec("MP003", "Crescent Fuels", "Entity A", "Must Pay", "P0", 3,
               monthly=_branch_C(250_000, 1_000_000), branch="C", note="oldest_and_current"),
    VendorSpec("MP004", "Delta Cement", "Entity B", "Must Pay", "P0", 4,
               monthly=_branch_D(150_000, 80_000, 1_000_000), branch="D", note="oldest_and_second (gap)"),
    VendorSpec("MP005", "Everest Traders", "Entity A", "Must Pay", "P0", 5,
               monthly=_branch_E(600_000, 1_000_000), branch="E", note="oldest_only"),
    VendorSpec("MP006", "Falcon Freight", "Entity B", "Must Pay", "P0", 1,
               monthly=_branch_F(90_000), branch="F", note="no_outstanding"),
    VendorSpec("MP007", "Ganges Textiles", "Entity A", "Must Pay", "P0", 2,
               monthly=_branch_C(400_000, 850_000), branch="C", note="oldest_and_current (2nd instance)"),

    # --- Normal / P2 — 7 vendors --------------------------------------------
    VendorSpec("NM2P2-01", "Horizon Pumps", "Entity A", "Normal", "P2", 3,
               monthly=_branch_A(200_000), branch="A"),
    VendorSpec("NM2P2-02", "Indus Valves", "Entity B", "Normal", "P2", 4,
               monthly=_branch_B(150_000), branch="B"),
    VendorSpec("NM2P2-03", "Jupiter Cables", "Entity A", "Normal", "P2", 5,
               monthly=_branch_C(80_000, 300_000), branch="C"),
    VendorSpec("NM2P2-04", "Kavya Chemicals", "Entity B", "Normal", "P2", 1,
               monthly=_branch_D(60_000, 40_000, 500_000), branch="D"),
    VendorSpec("NM2P2-05", "Lotus Paints", "Entity A", "Normal", "P2", 2,
               monthly=_branch_E(350_000, 500_000), branch="E"),
    VendorSpec("NM2P2-06", "Mahalaxmi Foods", "Entity B", "Normal", "P2", 3,
               monthly=_branch_F(110_000), branch="F"),
    VendorSpec("NM2P2-07", "Nandi Agro", "Entity A", "Normal", "P2", 4,
               monthly=_branch_A(90_000), branch="A"),

    # --- Normal / P3 — 6 vendors --------------------------------------------
    VendorSpec("NM2P3-01", "Omega Plastics", "Entity B", "Normal", "P3", 5,
               monthly=_branch_B(120_000), branch="B"),
    VendorSpec("NM2P3-02", "Pioneer Glass", "Entity A", "Normal", "P3", 1,
               monthly=_branch_C(50_000, 200_000), branch="C"),
    VendorSpec("NM2P3-03", "Quantum Electricals", "Entity B", "Normal", "P3", 2,
               monthly=_branch_D(70_000, 30_000, 400_000), branch="D"),
    VendorSpec("NM2P3-04", "Ravi Timber", "Entity A", "Normal", "P3", 3,
               monthly=_branch_E(300_000, 400_000), branch="E"),
    VendorSpec("NM2P3-05", "Sapphire Foods", "Entity B", "Normal", "P3", 4,
               monthly=_branch_F(95_000), branch="F"),
    VendorSpec("NM2P3-06", "Trident Hardware", "Entity A", "Normal", "P3", 5,
               monthly=_branch_A(250_000), branch="A"),

    # --- Normal / P4 — 6 vendors --------------------------------------------
    VendorSpec("NM2P4-01", "Unity Motors", "Entity B", "Normal", "P4", 1,
               monthly=_branch_A(180_000), branch="A"),
    VendorSpec("NM2P4-02", "Vishal Plywood", "Entity A", "Normal", "P4", 2,
               monthly=_branch_B(90_000), branch="B"),
    VendorSpec("NM2P4-03", "Wave Enterprises", "Entity B", "Normal", "P4", 3,
               monthly=_branch_C(40_000, 150_000), branch="C"),
    VendorSpec("NM2P4-04", "Xanadu Sports", "Entity A", "Normal", "P4", 4,
               monthly=_branch_D(45_000, 25_000, 300_000), branch="D"),
    VendorSpec("NM2P4-05", "Yamuna Dairy", "Entity B", "Normal", "P4", 5,
               monthly=_branch_E(220_000, 300_000), branch="E"),
    VendorSpec("NM2P4-06", "Zenith Tools", "Entity A", "Normal", "P4", 1,
               monthly=_branch_F(130_000), branch="F"),

    # --- Inactive / P5 — 5 vendors ------------------------------------------
    VendorSpec("NM2P5-01", "AeroTech Salvage", "Entity B", "Inactive", "P5", 2,
               monthly=_branch_B(60_000), branch="B"),
    VendorSpec("NM2P5-02", "Bygone Traders", "Entity A", "Inactive", "P5", 3,
               monthly=_branch_C(30_000, 100_000), branch="C"),
    VendorSpec("NM2P5-03", "Closing Chapter Textiles", "Entity B", "Inactive", "P5", 4,
               monthly=_branch_D(25_000, 15_000, 200_000), branch="D"),
    VendorSpec("NM2P5-04", "Discontinued Devices", "Entity A", "Inactive", "P5", 5,
               monthly=_branch_E(150_000, 200_000), branch="E"),
    VendorSpec("NM2P5-05", "Exit Logistics Co", "Entity B", "Inactive", "P5", 1,
               monthly=_branch_F(45_000), branch="F"),

    # --- Commitment (P1) — 14 vendors, varied commitment_months -------------
    VendorSpec("CM-01", "Continental Insurance", "Entity A", "Commitment", "P1", 2,
               op_balance=1_200_000, commitment_months=12, monthly=[(0, 0)] * 6),
    VendorSpec("CM-02", "Diamond Realty Lease", "Entity B", "Commitment", "P1", 3,
               op_balance=960_000, commitment_months=8, monthly=[(0, 0)] * 6),
    VendorSpec("CM-03", "Everflow Water Corp", "Entity A", "Commitment", "P1", 4,
               op_balance=450_000, commitment_months=3, monthly=[(0, 0)] * 6),
    VendorSpec("CM-04", "Falcon Aviation Svc", "Entity B", "Commitment", "P1", 5,
               op_balance=2_000_000, commitment_months=10, monthly=[(0, 0)] * 6),
    VendorSpec("CM-05", "Grand Hotel Group", "Entity A", "Commitment", "P1", 1,
               op_balance=360_000, commitment_months=6, monthly=[(0, 0)] * 6),
    VendorSpec("CM-06", "Harbor Shipping Co", "Entity B", "Commitment", "P1", 2,
               op_balance=875_000, commitment_months=5, monthly=[(0, 0)] * 6),
    VendorSpec("CM-07", "Infinity Telecom", "Entity A", "Commitment", "P1", 3,
               op_balance=1_540_000, commitment_months=14, monthly=[(0, 0)] * 6),
    VendorSpec("CM-08", "Jindal Power Lease", "Entity B", "Commitment", "P1", 4,
               op_balance=300_000, commitment_months=2, monthly=[(0, 0)] * 6),
    VendorSpec("CM-09", "Kohinoor Jewellers Rent", "Entity A", "Commitment", "P1", 5,
               op_balance=630_000, commitment_months=7, monthly=[(0, 0)] * 6),
    VendorSpec("CM-10", "Lakeside Resorts", "Entity B", "Commitment", "P1", 1,
               op_balance=1_100_000, commitment_months=11, monthly=[(0, 0)] * 6),
    VendorSpec("CM-11", "Metro Rail Contract", "Entity A", "Commitment", "P1", 2,
               op_balance=480_000, commitment_months=4, monthly=[(0, 0)] * 6),
    VendorSpec("CM-12", "National Broadband", "Entity B", "Commitment", "P1", 3,
               op_balance=810_000, commitment_months=9, monthly=[(0, 0)] * 6),
    VendorSpec("CM-13", "Orion Security Svc", "Entity A", "Commitment", "P1", 4,
               op_balance=195_000, commitment_months=3, monthly=[(0, 0)] * 6),
    VendorSpec("CM-14", "Prestige Builders Lease", "Entity B", "Commitment", "P1", 5,
               op_balance=1_320_000, commitment_months=12, monthly=[(0, 0)] * 6),
]

assert len(VENDORS) == 45
assert sum(1 for v in VENDORS if v.category_label == "Must Pay") == 7
assert sum(1 for v in VENDORS if v.category_label == "Commitment") == 14
assert sum(1 for v in VENDORS if v.category_label == "Normal") == 19
assert sum(1 for v in VENDORS if v.category_label == "Inactive") == 5
assert sum(1 for v in VENDORS if v.priority_tag == "P2") == 7
assert sum(1 for v in VENDORS if v.priority_tag == "P3") == 6
assert sum(1 for v in VENDORS if v.priority_tag == "P4") == 6
assert len({v.erp_code for v in VENDORS}) == 45
