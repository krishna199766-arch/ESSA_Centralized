# Architecture & Roadmap

This document explains how the document-intake engine is built, why it is built
that way, and how it becomes the foundation for the full Essa system seen in the
reference recordings (LR entry, invoice entry, inventory, barcode generation,
stock inward/outward, supplier management, purchase returns, payments, and
reports).

## 1. The problem, precisely

Essa Garments buys from many suppliers across states, and each supplier issues
invoices on its own layout and its own tax treatment. The five reference bills
alone span: a barcoded GRN with intra-state CGST+SGST (Minister White), three
different inter-state IGST layouts at 5% and 18% (AMS, GH/Krishna, Matoshree),
a handwritten TDS deduction (GH), fabric priced per metre (Mehak), an e-invoice
with IRN (GH), and handwritten annotations on almost all of them. Today a person
retypes each of these into the ERP. The goal is to eliminate that keying, be
robust to the layout differences, and let the business teach the system a new
supplier's format once rather than waiting on a developer.

## 2. Core principle: one canonical shape, many providers

Everything in the system speaks a single **canonical invoice** structure
(`app/schemas.py`): parties (supplier/buyer with GSTIN, state, bank), invoice
header (number, dates, e-way/LR/IRN), line items (barcode, description, HSN,
qty, uom, mrp, rate, discount, taxable value, amount), taxes (CGST/SGST/IGST/
TDS/charges/round-off) and totals.

An **extraction provider** is anything that can turn a page into that shape:

- `claude_vision` — a vision-capable LLM reads the image directly. This is the
  recommended production path: it handles skew, handwriting, and per-supplier
  layout without per-template coding, and it can be *guided* by a supplier's
  confirmed example as a few-shot reference.
- `tesseract` — fully offline OCR + regex header parsing. No API key, no
  network. Lower accuracy on messy photos, used as a fallback and for header
  detection.
- `seeded` — returns the human-verified extraction for a known sample, matched
  by file content hash. It gives a deterministic demo and a regression-fixture
  set; on real new uploads it simply never matches and the engine falls through.

Because every provider returns the same shape, they are swappable without any
change to the database, API, or UI. Adding a future provider (a fine-tuned
layout model, a cloud OCR service, a per-region template engine) is a new class
implementing `extract()` and nothing else moves.

## 3. The pipeline (`app/extraction/engine.py`)

```
upload ─▶ OCR header (cheap) ─▶ detect supplier by GSTIN / fuzzy name
                                     │
                    load that supplier's trained profile (if any)
                                     │
        choose provider ─▶ extract (guided by profile) ─▶ canonical draft
                                     │
             normalise + reconcile (validate.py) ─▶ warnings, field flags, confidence
                                     │
                     draft returned for human review
```

Supplier detection keys on GSTIN first (exact, reliable) and falls back to a
fuzzy match of the supplier name against the OCR'd header. Once a supplier is
known, their profile is handed to the provider as guidance and to the validator
as defaults.

## 4. Reconciliation: trust, then verify (`app/extraction/validate.py`)

An invoice is a system of arithmetic identities, and those identities are the
cheapest, most reliable quality signal available — no ground truth required. The
validator:

- derives any missing value where two of three are known (`amount = qty × rate`,
  tax amount from taxable × rate, or rate back-solved from amount),
- checks each identity within a rounding tolerance and, on failure, records a
  human-readable warning **and** flags the specific field path (e.g.
  `taxes.igst_amount`) so the UI can highlight exactly that input,
- sanity-checks the tax *mode*: supplier and buyer GSTIN state codes must agree
  for CGST+SGST and differ for IGST — catching a whole class of mistakes,
- scores confidence starting from 1.0 and penalising each warning and flag.

The result is that a single misread digit almost always trips a total and
surfaces precisely where to look, rather than flowing silently into the ERP.

## 5. Train once per format (`learn_from_correction`)

When a human confirms a correction with *Train* enabled, the system derives a
**SupplierProfile** from that confirmed invoice: the detection GSTIN, the tax
mode and default rates (inferred from the two GSTINs and the tax block), whether
TDS applies, the dominant unit of measure, and the confirmed invoice itself as a
reference example for the vision provider. Profiles are **versioned** — a later
retrain deactivates the old version and adds a new one, so the learning history
is auditable and reversible. This is the mechanism behind "define the format
once, reuse it forever": no per-supplier code, and the people who know the
invoices (not developers) do the teaching.

## 6. Data model (`app/models.py`)

`Supplier` (one per vendor) ─┬─ `SupplierProfile` (versioned learned format)
                             └─ `Document` (an uploaded scan + lifecycle status)
`Document` ─┬─ `Extraction` (each engine run or human correction — full audit
            │                 trail: raw draft → corrected)
            └─ `LineItem` (denormalised confirmed rows for reporting/queries)

Documents move `uploaded → needs_review → confirmed → posted`. Every extraction
is retained, so you can always see what the machine proposed versus what a human
confirmed — valuable for measuring accuracy and for training data later.

SQLite by default for zero-config local running; set `ESSA_DATABASE_URL` to a
Postgres URL for production with no code change (SQLAlchemy handles both).

## 7. Purchase / GRN + Inventory (implemented)

The intake layer's canonical invoice is the exact input the rest of the ERP
needs, and the **first consumer is now built**: Purchase / GRN + Inventory
(`app/services/inventory.py`, `routers/purchases.py`, `routers/inventory.py`).

**Build phase (`build_grn_from_document`).** A confirmed document becomes a draft
`Purchase` (GRN). Each invoice line is matched against the inventory master —
by barcode when present, otherwise by fuzzy description + HSN, preferring the
same supplier — and tagged *matched* or *new*. Nothing moves yet; a human
reviews the draft. (Minister White's handwritten `GRN No 15082` flows straight
through as the GRN number.)

**Post phase (`post_grn`).** Posting creates the new `Product` rows (auto SKU
`ESSA-#####`), appends one inward `StockMovement` per line, and updates each
product's `stock_qty` and **weighted-average cost**
(`new_avg = (old_qty·old_avg + in_qty·in_rate) / (old_qty + in_qty)`). It is
idempotent — a GRN posts once — and marks the source document `posted`.

**Attribute breakdown (`PurchaseLineSplit`).** A supplier bills a bundle and never
prints the mix, so one billed line has to become several stock items. The breakdown
lives on its own table hanging off `PurchaseLine`, deliberately *not* as extra
purchase lines: the invoice line stays exactly what the supplier billed, so invoice
arithmetic, the reconciliation layer and the payables side keep matching the
document, while the rows describe what physically arrived. `post_grn` walks the
rows when present (one product, one inward movement, one weighted-average update
each) and the plain line when not — and refuses to post while any breakdown fails
to add up to the billed quantity, since that would silently gain or lose units.

Identity is the **whole attribute tuple** (`SPLIT_ATTRS` — size, colour, material,
pattern, fit, type, design no), compared exactly including blanks, so an exact
re-buy merges and re-averages while anything different is created. Matching on
only the populated attributes was rejected: it folds *L* into *L / Red* for
whichever arrived first, which silently corrupts valuation across two GRNs. The
attribute set is deliberately the same one the phone detail form and `qr_payload`
carry, so a variant created at GRN is already the record the warehouse, the label
and the scanner expect. Variants get their SKU *and* an internal EAN-13 at post
(`barcode_svc.assign_identifiers`) because no supplier code exists for one variant.

**Unpost (`unpost_grn`).** Correcting a posted GRN means reversing it, not editing
history. The ledger is what makes that safe: `_replay_stock` recomputes a product's
quantity and weighted-average cost from its remaining movements, because a weighted
average is path-dependent and cannot be un-mixed arithmetically — subtracting one
purchase back out of `avg_cost` is only right when nothing else has happened since.

A GRN's footprint is every row it ever wrote — inwards *and* the reversals of
earlier unposts (`GRN_REF_TYPES`) — and the whole set leaves the replay together.
Excluding only the inwards leaves a stale reversal to be counted twice: verified
before the fix as a phantom shortfall of −55 where the honest figure was −25, plus
an average cost left inflated by an inward whose compensating row was ignored. One
reversal row per product carries the **net** still outstanding, so a GRN posted,
unposted and re-posted reverses exactly once.

Unpost refuses while anything depends on the GRN — a settled payment, a debit note,
or stock already dispatched (the replayed balance would go negative). Products the
GRN created and nothing else touched are deleted rather than left as zero-stock
ghosts with SKUs burnt; anything carrying its own history survives at zero stock.

**Why a movement ledger.** `StockMovement` is append-only with a running
`balance_after`, so inventory is always reconstructable and auditable, and the
same table is the spine for stock-outward, adjustments and reporting later.
Re-buying a barcoded item matches the existing product and re-averages its cost
instead of duplicating — verified: a second purchase of `MWC541674` (8 @ ₹300
on top of 2 @ ₹250) moves stock 2→10 and avg cost 250→290.

## 8. The remaining modules

The same canonical shape and the inventory ledger extend to the rest of the
recordings:

- **Barcode generation** — suppliers who don't pre-barcode (AMS, Matoshree,
  Mehak) get an `ESSA-#####` SKU on GRN post; the barcode module prints from it.
- **Stock Outward & Warehouse reports** — outward is a negative `StockMovement`;
  the ledger already supports it and is the reporting spine.
- **Payments (debit / discount / TDS)** — the taxes block already models TDS
  (GH Enterprises) and discounts (Mehak); a payables ledger keys off supplier +
  invoice number + grand total, all captured on the `Purchase`.
- **Purchase Return** — a return references a posted `Purchase` and reverses the
  relevant movements.
- **Analytical reports** — built on structured, reconciled data instead of
  re-keyed spreadsheets.

Recommended remaining order: (1) stock outward + payments ledger → (2) returns →
(3) reporting → (4) barcode printing. Each consumes the canonical shape and the
ledger these two modules now guarantee.

## 9. Production hardening checklist

The foundation is production-minded (real relational model, versioned learning,
audit trail, pluggable providers, DB-agnostic). Before going live, add: user
authentication and per-role access; move uploads to object storage (S3) and the
DB to Postgres; a background queue for extraction so large PDFs don't block
requests; PDF multi-page handling (poppler is already a dependency); an
accuracy dashboard comparing machine drafts to human corrections; and rate/cost
controls on the vision provider.
