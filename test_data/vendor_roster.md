# Synthetic 45-vendor dataset — plain roster

Built for the New Model 2 end-to-end validation exercise (2026-08-03) — predictions computed BEFORE the real model ever ran, see predictions_scenario_*.md.

- Sheet months: Mar-26 .. Aug-26 (sheet_start_month = 2026-03-01)
- Planning month: 2026-09 -> as_of (aging '0-30' reference) = 2026-08-01
- 7 Must Pay (P0), 14 Commitment (P1), 19 Normal (7 P2 / 6 P3 / 6 P4), 5 Inactive (P5)
- Every Must Pay/Normal/Inactive vendor is built from one of 6 fixed aging shapes, each engineered to land on a specific required_amount_v2() rule branch (docs/14):
  - A = only_current, B = current_zero_oldest_only, C = oldest_and_current (adjacent), D = oldest_and_second (gap month), E = oldest_only, F = no_outstanding

| ERP Code | Vendor | Category | Tag | Branch | Aging shape | Op.Bal / commitment_months | Required (Rs) | Outstanding (Rs) | Assigned Week |
|---|---|---|---|---|---|---|---|---|---|
| MP001 | Apex Steel Works | Must Pay | P0 | A | Aug-26 billed Rs 120,000 (Rs 120,000 unpaid) | - | 120,000.00 | 120,000.00 | W1 |
| MP002 | Bharat Logistics | Must Pay | P0 | B | Mar-26 billed Rs 200,000 (Rs 200,000 unpaid) | - | 200,000.00 | 200,000.00 | W2 |
| MP003 | Crescent Fuels | Must Pay | P0 | C | Mar-26 billed Rs 250,000 (Rs 250,000 unpaid); Aug-26 billed Rs 1,000,000 (Rs 1,000,000 unpaid) | - | 1,250,000.00 | 1,250,000.00 | W3 |
| MP004 | Delta Cement | Must Pay | P0 | D | Mar-26 billed Rs 150,000 (Rs 150,000 unpaid); Jun-26 billed Rs 80,000 (Rs 80,000 unpaid); Aug-26 billed Rs 1,000,000 (Rs 1,000,000 unpaid) | - | 230,000.00 | 1,230,000.00 | W4 |
| MP005 | Everest Traders | Must Pay | P0 | E | Mar-26 billed Rs 600,000 (Rs 600,000 unpaid); Aug-26 billed Rs 1,000,000 (Rs 1,000,000 unpaid) | - | 600,000.00 | 1,600,000.00 | W5 |
| MP006 | Falcon Freight | Must Pay | P0 | F | Mar-26 billed Rs 90,000 (paid in full) | - | 0.00 | 0.00 | W1 |
| MP007 | Ganges Textiles | Must Pay | P0 | C | Mar-26 billed Rs 400,000 (Rs 400,000 unpaid); Aug-26 billed Rs 850,000 (Rs 850,000 unpaid) | - | 1,250,000.00 | 1,250,000.00 | W2 |
| NM2P2-01 | Horizon Pumps | Normal | P2 | A | Aug-26 billed Rs 200,000 (Rs 200,000 unpaid) | - | 200,000.00 | 200,000.00 | W3 |
| NM2P2-02 | Indus Valves | Normal | P2 | B | Mar-26 billed Rs 150,000 (Rs 150,000 unpaid) | - | 150,000.00 | 150,000.00 | W4 |
| NM2P2-03 | Jupiter Cables | Normal | P2 | C | Mar-26 billed Rs 80,000 (Rs 80,000 unpaid); Aug-26 billed Rs 300,000 (Rs 300,000 unpaid) | - | 380,000.00 | 380,000.00 | W5 |
| NM2P2-04 | Kavya Chemicals | Normal | P2 | D | Mar-26 billed Rs 60,000 (Rs 60,000 unpaid); Jun-26 billed Rs 40,000 (Rs 40,000 unpaid); Aug-26 billed Rs 500,000 (Rs 500,000 unpaid) | - | 100,000.00 | 600,000.00 | W1 |
| NM2P2-05 | Lotus Paints | Normal | P2 | E | Mar-26 billed Rs 350,000 (Rs 350,000 unpaid); Aug-26 billed Rs 500,000 (Rs 500,000 unpaid) | - | 350,000.00 | 850,000.00 | W2 |
| NM2P2-06 | Mahalaxmi Foods | Normal | P2 | F | Mar-26 billed Rs 110,000 (paid in full) | - | 0.00 | 0.00 | W3 |
| NM2P2-07 | Nandi Agro | Normal | P2 | A | Aug-26 billed Rs 90,000 (Rs 90,000 unpaid) | - | 90,000.00 | 90,000.00 | W4 |
| NM2P3-01 | Omega Plastics | Normal | P3 | B | Mar-26 billed Rs 120,000 (Rs 120,000 unpaid) | - | 120,000.00 | 120,000.00 | W5 |
| NM2P3-02 | Pioneer Glass | Normal | P3 | C | Mar-26 billed Rs 50,000 (Rs 50,000 unpaid); Aug-26 billed Rs 200,000 (Rs 200,000 unpaid) | - | 250,000.00 | 250,000.00 | W1 |
| NM2P3-03 | Quantum Electricals | Normal | P3 | D | Mar-26 billed Rs 70,000 (Rs 70,000 unpaid); Jun-26 billed Rs 30,000 (Rs 30,000 unpaid); Aug-26 billed Rs 400,000 (Rs 400,000 unpaid) | - | 100,000.00 | 500,000.00 | W2 |
| NM2P3-04 | Ravi Timber | Normal | P3 | E | Mar-26 billed Rs 300,000 (Rs 300,000 unpaid); Aug-26 billed Rs 400,000 (Rs 400,000 unpaid) | - | 300,000.00 | 700,000.00 | W3 |
| NM2P3-05 | Sapphire Foods | Normal | P3 | F | Mar-26 billed Rs 95,000 (paid in full) | - | 0.00 | 0.00 | W4 |
| NM2P3-06 | Trident Hardware | Normal | P3 | A | Aug-26 billed Rs 250,000 (Rs 250,000 unpaid) | - | 250,000.00 | 250,000.00 | W5 |
| NM2P4-01 | Unity Motors | Normal | P4 | A | Aug-26 billed Rs 180,000 (Rs 180,000 unpaid) | - | 180,000.00 | 180,000.00 | W1 |
| NM2P4-02 | Vishal Plywood | Normal | P4 | B | Mar-26 billed Rs 90,000 (Rs 90,000 unpaid) | - | 90,000.00 | 90,000.00 | W2 |
| NM2P4-03 | Wave Enterprises | Normal | P4 | C | Mar-26 billed Rs 40,000 (Rs 40,000 unpaid); Aug-26 billed Rs 150,000 (Rs 150,000 unpaid) | - | 190,000.00 | 190,000.00 | W3 |
| NM2P4-04 | Xanadu Sports | Normal | P4 | D | Mar-26 billed Rs 45,000 (Rs 45,000 unpaid); Jun-26 billed Rs 25,000 (Rs 25,000 unpaid); Aug-26 billed Rs 300,000 (Rs 300,000 unpaid) | - | 70,000.00 | 370,000.00 | W4 |
| NM2P4-05 | Yamuna Dairy | Normal | P4 | E | Mar-26 billed Rs 220,000 (Rs 220,000 unpaid); Aug-26 billed Rs 300,000 (Rs 300,000 unpaid) | - | 220,000.00 | 520,000.00 | W5 |
| NM2P4-06 | Zenith Tools | Normal | P4 | F | Mar-26 billed Rs 130,000 (paid in full) | - | 0.00 | 0.00 | W1 |
| NM2P5-01 | AeroTech Salvage | Inactive | P5 | B | Mar-26 billed Rs 60,000 (Rs 60,000 unpaid) | - | 60,000.00 | 60,000.00 | W2 |
| NM2P5-02 | Bygone Traders | Inactive | P5 | C | Mar-26 billed Rs 30,000 (Rs 30,000 unpaid); Aug-26 billed Rs 100,000 (Rs 100,000 unpaid) | - | 130,000.00 | 130,000.00 | W3 |
| NM2P5-03 | Closing Chapter Textiles | Inactive | P5 | D | Mar-26 billed Rs 25,000 (Rs 25,000 unpaid); Jun-26 billed Rs 15,000 (Rs 15,000 unpaid); Aug-26 billed Rs 200,000 (Rs 200,000 unpaid) | - | 40,000.00 | 240,000.00 | W4 |
| NM2P5-04 | Discontinued Devices | Inactive | P5 | E | Mar-26 billed Rs 150,000 (Rs 150,000 unpaid); Aug-26 billed Rs 200,000 (Rs 200,000 unpaid) | - | 150,000.00 | 350,000.00 | W5 |
| NM2P5-05 | Exit Logistics Co | Inactive | P5 | F | Mar-26 billed Rs 45,000 (paid in full) | - | 0.00 | 0.00 | W1 |
| CM-01 | Continental Insurance | Commitment | P1 | - | flat opening balance Rs 1,200,000, no monthly ledger activity | 1,200,000 / 12 | 100,000.00 | 1,200,000.00 | W2 |
| CM-02 | Diamond Realty Lease | Commitment | P1 | - | flat opening balance Rs 960,000, no monthly ledger activity | 960,000 / 8 | 120,000.00 | 960,000.00 | W3 |
| CM-03 | Everflow Water Corp | Commitment | P1 | - | flat opening balance Rs 450,000, no monthly ledger activity | 450,000 / 3 | 150,000.00 | 450,000.00 | W4 |
| CM-04 | Falcon Aviation Svc | Commitment | P1 | - | flat opening balance Rs 2,000,000, no monthly ledger activity | 2,000,000 / 10 | 200,000.00 | 2,000,000.00 | W5 |
| CM-05 | Grand Hotel Group | Commitment | P1 | - | flat opening balance Rs 360,000, no monthly ledger activity | 360,000 / 6 | 60,000.00 | 360,000.00 | W1 |
| CM-06 | Harbor Shipping Co | Commitment | P1 | - | flat opening balance Rs 875,000, no monthly ledger activity | 875,000 / 5 | 175,000.00 | 875,000.00 | W2 |
| CM-07 | Infinity Telecom | Commitment | P1 | - | flat opening balance Rs 1,540,000, no monthly ledger activity | 1,540,000 / 14 | 110,000.00 | 1,540,000.00 | W3 |
| CM-08 | Jindal Power Lease | Commitment | P1 | - | flat opening balance Rs 300,000, no monthly ledger activity | 300,000 / 2 | 150,000.00 | 300,000.00 | W4 |
| CM-09 | Kohinoor Jewellers Rent | Commitment | P1 | - | flat opening balance Rs 630,000, no monthly ledger activity | 630,000 / 7 | 90,000.00 | 630,000.00 | W5 |
| CM-10 | Lakeside Resorts | Commitment | P1 | - | flat opening balance Rs 1,100,000, no monthly ledger activity | 1,100,000 / 11 | 100,000.00 | 1,100,000.00 | W1 |
| CM-11 | Metro Rail Contract | Commitment | P1 | - | flat opening balance Rs 480,000, no monthly ledger activity | 480,000 / 4 | 120,000.00 | 480,000.00 | W2 |
| CM-12 | National Broadband | Commitment | P1 | - | flat opening balance Rs 810,000, no monthly ledger activity | 810,000 / 9 | 90,000.00 | 810,000.00 | W3 |
| CM-13 | Orion Security Svc | Commitment | P1 | - | flat opening balance Rs 195,000, no monthly ledger activity | 195,000 / 3 | 65,000.00 | 195,000.00 | W4 |
| CM-14 | Prestige Builders Lease | Commitment | P1 | - | flat opening balance Rs 1,320,000, no monthly ledger activity | 1,320,000 / 12 | 110,000.00 | 1,320,000.00 | W5 |
