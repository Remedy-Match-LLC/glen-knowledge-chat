# Pure Powder Storefront Crosswalk

- Established FMP Pure Powder SKUs: **14**
- Active powdered raw materials with recorded inventory: **501**
- Missing or specification-mismatched catalog records: **1**

## Decision gates

- exclude: **26**
- formula_only: **12**
- manual_review: **460**
- publish_or_reconcile: **14**
- rename_or_split_catalog_record: **1**
- verify_retail_configuration: **2**

## Priority reconciliation

| Ingredient | Status | Current catalog record | Action |
|---|---|---|---|
| Gingerols 20% (Zingiber officinalis) | specification_mismatch | Gingerol | rename_or_split_catalog_record |

## Publishing rule

Inventory alone never authorizes a retail listing. Every raw material needs an approved serving size, package size, price, labeling name, and safety disposition. Tiny-dose ingredients remain formula-only.

Full row-level crosswalk: `pure-powder-storefront-crosswalk.csv`
