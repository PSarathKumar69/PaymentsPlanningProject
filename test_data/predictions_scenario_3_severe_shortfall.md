# Prediction — Scenario 3 — Severe shortfall

Available Funds: Rs 4,000,000.00

- guaranteed_total (Must Pay + Commitment): Rs 5,290,000.00
- escalation: True (escalation_shortfall: Rs 1,290,000.00)
- bucket_pct: {'P2': 0.25, 'P3': 0.25, 'P4': 0.25, 'P5': 0.1}
- exceptional_shortfall: True
- leftover_topup_total: Rs 0.00
- leftover_remaining: Rs -798,000.00

| ERP Code | Vendor | Category | Tag | Rule | Required | Allocated | Status |
|---|---|---|---|---|---|---|---|
| MP001 | Apex Steel Works | Must Pay | P0 | only_current | 120,000.00 | 120,000.00 | guaranteed |
| MP002 | Bharat Logistics | Must Pay | P0 | current_zero_oldest_only | 200,000.00 | 200,000.00 | guaranteed |
| MP003 | Crescent Fuels | Must Pay | P0 | oldest_and_current | 1,250,000.00 | 1,250,000.00 | guaranteed |
| MP004 | Delta Cement | Must Pay | P0 | oldest_and_second | 230,000.00 | 230,000.00 | guaranteed |
| MP005 | Everest Traders | Must Pay | P0 | oldest_only | 600,000.00 | 600,000.00 | guaranteed |
| MP006 | Falcon Freight | Must Pay | P0 | no_outstanding | 0.00 | (excluded — zero outstanding balance) | n/a |
| MP007 | Ganges Textiles | Must Pay | P0 | oldest_and_current | 1,250,000.00 | 1,250,000.00 | guaranteed |
| NM2P2-01 | Horizon Pumps | Normal | P2 | only_current | 200,000.00 | 50,000.00 | partial |
| NM2P2-02 | Indus Valves | Normal | P2 | current_zero_oldest_only | 150,000.00 | 37,500.00 | partial |
| NM2P2-03 | Jupiter Cables | Normal | P2 | oldest_and_current | 380,000.00 | 95,000.00 | partial |
| NM2P2-04 | Kavya Chemicals | Normal | P2 | oldest_and_second | 100,000.00 | 25,000.00 | partial |
| NM2P2-05 | Lotus Paints | Normal | P2 | oldest_only | 350,000.00 | 87,500.00 | partial |
| NM2P2-06 | Mahalaxmi Foods | Normal | P2 | no_outstanding | 0.00 | (excluded — zero outstanding balance) | n/a |
| NM2P2-07 | Nandi Agro | Normal | P2 | only_current | 90,000.00 | 22,500.00 | partial |
| NM2P3-01 | Omega Plastics | Normal | P3 | current_zero_oldest_only | 120,000.00 | 30,000.00 | partial |
| NM2P3-02 | Pioneer Glass | Normal | P3 | oldest_and_current | 250,000.00 | 62,500.00 | partial |
| NM2P3-03 | Quantum Electricals | Normal | P3 | oldest_and_second | 100,000.00 | 25,000.00 | partial |
| NM2P3-04 | Ravi Timber | Normal | P3 | oldest_only | 300,000.00 | 75,000.00 | partial |
| NM2P3-05 | Sapphire Foods | Normal | P3 | no_outstanding | 0.00 | (excluded — zero outstanding balance) | n/a |
| NM2P3-06 | Trident Hardware | Normal | P3 | only_current | 250,000.00 | 62,500.00 | partial |
| NM2P4-01 | Unity Motors | Normal | P4 | only_current | 180,000.00 | 45,000.00 | partial |
| NM2P4-02 | Vishal Plywood | Normal | P4 | current_zero_oldest_only | 90,000.00 | 22,500.00 | partial |
| NM2P4-03 | Wave Enterprises | Normal | P4 | oldest_and_current | 190,000.00 | 47,500.00 | partial |
| NM2P4-04 | Xanadu Sports | Normal | P4 | oldest_and_second | 70,000.00 | 17,500.00 | partial |
| NM2P4-05 | Yamuna Dairy | Normal | P4 | oldest_only | 220,000.00 | 55,000.00 | partial |
| NM2P4-06 | Zenith Tools | Normal | P4 | no_outstanding | 0.00 | (excluded — zero outstanding balance) | n/a |
| NM2P5-01 | AeroTech Salvage | Inactive | P5 | current_zero_oldest_only | 60,000.00 | 6,000.00 | partial |
| NM2P5-02 | Bygone Traders | Inactive | P5 | oldest_and_current | 130,000.00 | 13,000.00 | partial |
| NM2P5-03 | Closing Chapter Textiles | Inactive | P5 | oldest_and_second | 40,000.00 | 4,000.00 | partial |
| NM2P5-04 | Discontinued Devices | Inactive | P5 | oldest_only | 150,000.00 | 15,000.00 | partial |
| NM2P5-05 | Exit Logistics Co | Inactive | P5 | no_outstanding | 0.00 | (excluded — zero outstanding balance) | n/a |
| CM-01 | Continental Insurance | Commitment | P1 | commitment | 100,000.00 | 100,000.00 | guaranteed |
| CM-02 | Diamond Realty Lease | Commitment | P1 | commitment | 120,000.00 | 120,000.00 | guaranteed |
| CM-03 | Everflow Water Corp | Commitment | P1 | commitment | 150,000.00 | 150,000.00 | guaranteed |
| CM-04 | Falcon Aviation Svc | Commitment | P1 | commitment | 200,000.00 | 200,000.00 | guaranteed |
| CM-05 | Grand Hotel Group | Commitment | P1 | commitment | 60,000.00 | 60,000.00 | guaranteed |
| CM-06 | Harbor Shipping Co | Commitment | P1 | commitment | 175,000.00 | 175,000.00 | guaranteed |
| CM-07 | Infinity Telecom | Commitment | P1 | commitment | 110,000.00 | 110,000.00 | guaranteed |
| CM-08 | Jindal Power Lease | Commitment | P1 | commitment | 150,000.00 | 150,000.00 | guaranteed |
| CM-09 | Kohinoor Jewellers Rent | Commitment | P1 | commitment | 90,000.00 | 90,000.00 | guaranteed |
| CM-10 | Lakeside Resorts | Commitment | P1 | commitment | 100,000.00 | 100,000.00 | guaranteed |
| CM-11 | Metro Rail Contract | Commitment | P1 | commitment | 120,000.00 | 120,000.00 | guaranteed |
| CM-12 | National Broadband | Commitment | P1 | commitment | 90,000.00 | 90,000.00 | guaranteed |
| CM-13 | Orion Security Svc | Commitment | P1 | commitment | 65,000.00 | 65,000.00 | guaranteed |
| CM-14 | Prestige Builders Lease | Commitment | P1 | commitment | 110,000.00 | 110,000.00 | guaranteed |
