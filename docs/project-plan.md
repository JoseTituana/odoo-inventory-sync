# Project plan

This document defines where `odoo-inventory-sync` is going. It is a living plan: decisions and open questions are updated as the project grows.

## Vision

A toolkit for a footwear business running Odoo. It manages the catalog as **model × color × size**, prints **labels that match real stock**, and lets the point of sale show at a glance which sizes and colors of a model are available.

## Business context

- **One general inventory**, distributed across three channels:
  - **Store warehouse (own):** has the point of sale (POS) and sells to the public. POS sales reduce stock automatically (standard Odoo behavior).
  - **Wholesale buyers:** buy in bulk. *(Assumption: they are customers in Odoo, not vendors.)*
  - **Warehouse 2:** not owned by the business, buys in batches. *(Assumption: a customer in Odoo; "batch" means a large quantity, not lot/serial tracking.)*
- **Odoo is the single source of truth.** Every receipt, sale and transfer is a stock move in Odoo. No separate database.

## Product model

**Decision: one product template per shoe model, with variants by color and size.**

```
MOC001 · Leather Loafer              <- product.template (one model)
 ├── Black / 40   -> on hand: 3      <- product.product (one variant each,
 ├── Black / 41   -> on hand: 1         own SKU, own barcode, own stock)
 ├── Brown / 40   -> on hand: 0
 └── Brown / 41   -> on hand: 5
```

Why:

- Stock is still tracked per color and size, down to a single pair.
- Name, price and description are managed once per model.
- The POS stock grid (M3) and wholesale grid ordering depend on this structure.
- Serial numbers per pair are **not** used: identical pairs only need a quantity.

### Codes

| Element | Format | Example |
|---|---|---|
| Model code | Letters + number | `MOC001` |
| Variant SKU (`default_code`) | `MODEL-COLOR-SIZE` | `MOC001-BLK-40` |
| Barcode | EAN-13, internal prefix `2`, generated per variant | `2000010000401` |

**Why EAN-13 and not the SKU as a Code 128 barcode:** a 13-character SKU in Code 128 needs about 198 modules including quiet zones. At 0.25 mm per module (2 dots at 203 dpi, the minimum that cheap scanners read reliably) that is about 49.5 mm, which does not fit a 50 mm label. An internal EAN-13 is about 28–31 mm wide, is read by every retail scanner, needs no GS1 registration (prefix `2` is reserved for in-store use) and is supported by Odoo. The SKU is still printed as text.

## Modules

### M0. Development environment

- Odoo Community in Docker on the developer machine.
- A seed script that configures the store location, the Size and Color attributes, and sample shoes with stock.
- **Done when:** one command gives a working Odoo with sample shoes ready to test.

### M1. Matrix catalog import

- Import a CSV such as:

  ```csv
  model_code,name,color,size,price,qty
  MOC001,Leather Loafer,Black,40,45.00,3
  MOC001,Leather Loafer,Black,41,45.00,0
  MOC001,Leather Loafer,Brown,40,45.00,5
  ```

- Creates the template, attributes and variants, and generates SKU and barcode for each variant. Keeps `--dry-run` and the current validation.
- **Done when:** the sample CSV produces one model with the right variants in Odoo, and importing it again creates no duplicates.

### M2. Stock-based labels

- `odoo-sync labels --location Store --profile <profile>`
- **One label per pair in stock. Zero stock prints nothing.**
- Label content: barcode, model name, model code, size, color and sale price. Size and price are the most visible elements.

  ```
  ┌─────────────────────────────────┐
  │ LEATHER LOAFER          $45.00  │
  │ MOC001 · Black                  │
  │ ║│║║│║│║║│║║║│║║│║│║   SIZE 40  │
  │ MOC001-BLK-40                   │
  └─────────────────────────────────┘
  ```

- Filters: by model (`--model MOC001`) and preview (`--dry-run`, e.g. "47 labels for 12 variants").
- Only the store warehouse for now.
- **Done when:** with 3 pairs of `MOC001-BLK-40` and 0 of `MOC001-BLK-41`, exactly 3 labels are produced and none for size 41, in every supported format.

#### Printer profiles

Label size depends on the printer, so layouts are defined as **profiles** in a config file. Each profile sets: type (roll or sheet), page size, label size, rows, columns, margins, gaps, resolution and output format. Built-in presets cover common standards; users can add their own.

**Thermal roll printers**

| Max print width | Example printers | Common labels for footwear |
|---|---|---|
| 2" (~54–56 mm) | Zebra ZD411 2", Xprinter XP-365B | 50×25, 50×30, 40×30 mm |
| 4" (~104–108 mm) | Zebra ZD421, GK420d, TSC TE200, Xprinter XP-420B | 60×40, 100×50, 100×150 mm |

- Resolution: 203 dpi (8 dots/mm) or 300 dpi (12 dots/mm).
- Command languages differ: Zebra uses **ZPL**; TSC and most Xprinter models use **TSPL**.
- 4" printers can use multi-across rolls (e.g. two 50×25 mm labels per row).

**Sheet labels for office printers** (A4 and US Letter are both common in Ecuador)

| Sheet | Layout | Labels per sheet | Label size |
|---|---|---|---|
| A4 | Avery L7160 or compatible | 21 (3×7) | 63.5 × 38.1 mm |
| A4 | Avery L7159 or compatible | 24 (3×8) | 63.5 × 33.9 mm |
| A4 | Avery L7651 or compatible | 65 (5×13) | 38.1 × 21.2 mm |
| A4 | Avery L7163 or compatible | 14 (2×7) | 99.1 × 38.1 mm |
| Letter | Avery 5160 or compatible | 30 (3×10) | 66.7 × 25.4 mm |

- Pre-cut sheets: positions are fixed. `--start-at N` reuses a partly used sheet.
- Blank adhesive sheets: the grid is computed to fit as many labels as possible at the minimum readable size.

#### Output formats

1. **PDF sized to the label or sheet** (first). Works with any printer that has a Windows driver, thermal or office.
2. **ZPL** (Zebra) and **TSPL** (TSC, Xprinter) for direct raw printing. Faster and sharper. Added after PDF.

#### Adaptive layout

- Large labels show every field.
- Small labels keep barcode, size and price, and shorten the name.
- If a label is too small for a readable barcode, the tool **warns instead of printing** a label that cannot be scanned.

#### Printer detection

Windows can list installed printers but does not reliably report the loaded label size. `odoo-sync printers` lists printers and lets the user assign a profile once; later prints reuse it.

### M3. POS stock lookup

- In the point of sale, typing a model code (e.g. `MOC001`) shows a grid of store stock:

  ```
  MOC001 · Leather Loafer        Store
           38   39   40   41   42
  Black     2    0    3    0    1
  Brown     0    4    5    2    0
  ```

- Zero cells are dimmed so the seller sees at once what can be offered.
- **Done when:** searching a model in the POS shows its grid and the numbers match real stock.

### M4. Channels and reports

- Distribute stock across the three channels with internal transfers.
- Reports per channel: stock by size and color, POS sales, wholesale sales.
- Depends on the open questions below.

## Architecture

The repository has two parts, because M3 cannot be built from outside Odoo:

```
odoo-inventory-sync/
├── src/odoo_inventory_sync/   -> command line tool (M1, M2, M4)
│                                 talks to Odoo from outside over XML-RPC
└── addons/pos_stock_matrix/   -> module installed INSIDE Odoo (M3)
                                  Python + JavaScript (Odoo OWL framework)
```

The POS is an Odoo screen; extending it requires an Odoo module, not an external program.

## Decisions

| # | Decision | Status |
|---|---|---|
| 1 | One template per model, variants by color and size, quantity per variant | Decided |
| 2 | Barcode: internal EAN-13 (prefix `2`); SKU printed as text | Decided |
| 3 | Odoo is the single source of truth, no separate database | Decided |
| 4 | Labels: printer profiles, PDF first, then ZPL and TSPL | Decided |
| 5 | Odoo version: latest stable Community | To confirm in M0 |

## Open questions

- Which printers are used? Thermal printer brand and model, and A4 or Letter sheets.
- Wholesale buyers: confirm they are customers, not vendors.
- Warehouse 2: confirm it is a customer and what "not our shoe" means.
- Batches: bulk quantities only, or lot numbers too?
- Stock per channel: physically separated, or one shared pool with an allocation per channel?
- Is there an existing production Odoo whose version must be matched?

## Out of scope for now

- Labels for channels other than the store warehouse.
- Lot or serial number tracking.
- Purchasing from real vendors.

## Way of working

Each module is split into small GitHub issues. Each issue gets its own branch, change and tests, and is merged through a reviewed pull request.

**Order:** M0 → M1 → M2 → M3 → M4. The three known CSV parsing bugs are fixed along the way as practice.
