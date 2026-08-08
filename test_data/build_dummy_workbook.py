"""Builds test_data/dummy_vendors_45.xlsx from dataset_spec.VENDORS, matching
the real master sheet's column layout (backend/ingestion/column_mapping.py)
so it ingests through the REAL pipeline (load()/commit_upload()), not a DB-row
shortcut. Single header row (row 1), data from row 2 — one of the two real
sheet shapes column_mapping.py's module docstring documents supporting.

Column layout (1-indexed):
  1 ERP Code            8-13 Payment Mar-26..Aug-26
  2 Entity              14 Total Payment
  3 Vendor Name          15 Closing Balance
  4 Op. Balance          16 Notes (blank spacer -> assigned_week_order_col
  5-10 Payable Mar-26..Aug-26   position, deliberately empty so no
  11 Total Payable              "unrecognized legacy value" noise)
                          17 Category
                          18 Commitment Months
                          19 Assigned Week
                          20 Priority Tag

Run standalone: python -m test_data.build_dummy_workbook
"""
import os

import openpyxl

from test_data.dataset_spec import MONTH_LABELS, VENDORS

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "dummy_vendors_45.xlsx")

HEADERS = (
    ["ERP Code", "Entity", "Vendor Name", "Op. Balance"]
    + list(MONTH_LABELS)  # payable block
    + ["Total Payable"]
    + list(MONTH_LABELS)  # payment block
    + ["Total Payment", "Closing Balance", "Notes"]
    + ["Category", "Commitment Months", "Assigned Week", "Priority Tag"]
)


def build_workbook(vendors=VENDORS, output_path=OUTPUT_PATH):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Master"
    ws.append(HEADERS)

    for v in vendors:
        payable = [p for p, _ in v.monthly]
        payment = [pm for _, pm in v.monthly]
        total_payable = sum(payable)
        total_payment = sum(payment)
        closing = v.op_balance + total_payable - total_payment

        row = (
            [v.erp_code, v.entity, v.vendor_name, v.op_balance]
            + payable
            + [total_payable]
            + payment
            + [total_payment, closing, None]
            + [v.category_label, v.commitment_months, v.assigned_week, v.priority_tag]
        )
        ws.append(row)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    wb.save(output_path)
    return output_path


if __name__ == "__main__":
    path = build_workbook()
    print(f"Wrote {path} ({len(VENDORS)} vendors)")
