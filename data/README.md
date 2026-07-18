# data/

This folder holds the vendor master Excel sheet(s) the app reads from and
writes back to (`docs/07-data-pipeline-and-master-sheet.md`,
`docs/11-configuration-module.md`).

**The actual `.xlsx` files are intentionally not tracked in git** — they
contain real vendor payment data and are excluded via `.gitignore`
(`data/*.xlsx`). To run this project locally, place the master sheet here
yourself:

- `Vendor's Details.xlsx` — the real master sheet the backend is built and
  tested against.
- `Vendor's Details (Demo 15).xlsx` — a smaller demo dataset used for some
  test runs.

Any `.xlsx.bak_*` files the app generates on config write-backs are also
excluded and will accumulate here locally — safe to delete periodically.
