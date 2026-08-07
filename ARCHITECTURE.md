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
to add up to **what was received**, since that would silently gain or lose units.
(Received, not billed — see §7b.)

Identity is the **whole attribute tuple** (`SPLIT_ATTRS` — size, colour, material,
pattern, fit, type, design no), compared exactly including blanks, so an exact
re-buy merges and re-averages while anything different is created. Matching on
only the populated attributes was rejected: it folds *L* into *L / Red* for
whichever arrived first, which silently corrupts valuation across two GRNs. The
attribute set is deliberately the same one the phone detail form and `qr_payload`
carry, so a variant created at GRN is already the record the warehouse, the label
and the scanner expect. Variants get their SKU at post
(`barcode_svc.assign_identifiers`) because no supplier code exists for one variant.

**The bundle is marked split, and says so** (`is_split` on the line payload). It is
what the supplier billed and it never becomes stock — the rows below it do, and
`post_grn` walks them instead. Every client is told outright rather than inferring
it from a non-empty list, because "this row does not move stock" is exactly the
thing a receiving screen must not get wrong; the phone shows `split · 4` and a line
saying the rows below carry the stock.

**A code per piece, under a SKU that has many** (`models.ProductUnit`,
`services/units.py`). Receiving 8 of ESSA-00008 makes **one** inventory record with
a stock of 8 — master, valuation and ledger all stay at SKU level, because that is
what a weighted-average cost and a stock report are about. Underneath it,
`post_grn` mints one identity per garment: `ESSA-00008-001 … -008`, each with its
own QR, all pointing at the same SKU.

Printing the SKU label eight times would be cheaper and is what most systems do. It
cannot answer "which of these came back", "which one is missing", or "where did
this particular piece go" — eight identical tags are eight copies of an answer, not
eight questions. A serial per piece is the difference between counting stock and
tracking it.

Two limits are deliberate. **Only countable units of measure**: 43.5 MTR of fabric
is not 43 or 44 of anything, so nothing is generated and the screen is told *why*
rather than left showing an unexplained blank. And **a ceiling per receipt**
(`MAX_PER_RECEIPT`): serialising is for goods a human will physically tag, and
quietly writing 5,000 rows and offering 5,000 labels is the wrong answer to a
5,000-piece receipt.

Piece numbers continue across receipts (`next_seq`), so a re-buy adds -009, -010
rather than starting again at -001 and colliding. `resolve()` accepts a piece code
and returns its **product**, which is what lets every existing scan point — GRN
linking, lookup, outward — take a label off an individual garment without knowing
units exist. Unposting deletes the pieces that receipt created, exactly as it does
its cartons.

What is *not* wired yet: nothing consumes a unit on dispatch. `status` exists and
receipt sets it, so until outward-by-piece is built the honest reading of the table
is "the pieces this SKU received", not "the pieces still on the shelf".

That makes three label layers, each answering a different question:

| | Carton (`EB1`) | SKU (`E1`) | Piece (`EU1`) |
|---|---|---|---|
| identifies | the box on the rack | the product record | one physical garment |
| example | `ESSA-B-00001` | `ESSA-00008` | `ESSA-00008-003` |
| count | one per GRN line | one per stock item | one per unit received |

**Two labels, because there are two moments** (`models.Bundle`, `services/bundles.py`).
A carton and a garment are different things to a warehouse, and one code cannot be
both:

| | Carton label (`ESSA-B-00001`, tag `EB1`) | Garment tag (`ESSA-00004`, tag `E1`) |
|---|---|---|
| printed | the moment the GRN posts | later, when the box is opened and tagged |
| answers | which box is this, what's inside, where does it live | which garment is this, what size, what price |
| size | 99mm, 34mm QR — read across a rack | 58mm, 26mm QR — read in the hand |
| carries | qty, the size mix, GRN/invoice, a LOCATION line | attributes, category, SKU, MRP |

A `Bundle` is a **handling** unit, never a stock row: the 50 pieces inside are
already counted as the items they became, and counting the carton too would double
the warehouse — verified, 50 t-shirts + 20 dhoti still reads 70 units with two
bundles on file. What it owns is a code to scan when putting it away, the receipt
it came from, and where it is now.

The order is the point. Printing garment tags at GRN means tagging fifty loose
items before anyone has looked at them and before they sit in a carton for a
fortnight; printing only garment tags leaves the box itself anonymous on the rack.
So `post_grn` creates the bundles and the phone's post screen prints *carton*
labels, while `bundles.tag()` prints the garment tags — and **refuses** while any
item is still undetailed, because the whole reason that step is separate is that by
then the information exists to put on the tag.

`EB1` versus `E1` is what keeps the two apart at the scanner. `barcode_svc.resolve`
returns products and rejects a bundle payload outright, so scanning a carton where
a garment is expected fails loudly instead of dispatching a box as if it were one
shirt. Unposting a GRN deletes its bundles: they describe a receipt that no longer
happened, and their labels point at products the unpost may have just removed.

**One code per item, issued at post, filled out at detailing.** A variant gets its
SKU and QR when the GRN posts, not when someone gets round to inspecting it —
otherwise stock exists with no way to scan it, which is the gap labels are for.
Detailing never mints a second code: `assign_identifiers` is idempotent, so the SKU
a label was printed with stays valid forever. What changes is the QR's *payload* —
`qr_payload` reads the live record, so an item detailed after posting goes from
`L · Red` to carrying fit, pattern, material and pricing too. That is why the phone
suggests detailing before printing: the sticker is a snapshot, and the fuller one
reads the whole item with no network. Either way a scan resolves by SKU to the
live row, so an early print is never wrong, only thinner.

**The receipt becomes the worklist.** `items_pending_detail` on the GRN and
`product_detailed` on each row let the posted screen say "4 of 4 still to detail"
and tick over as they are done — so the items a receipt created are detailed from
that receipt, with the goods in hand, instead of being hunted down individually in
the Products list afterwards.

**The breakdown is entered where the cartons are** (`mobile-app/grn.js`). Only the
person opening them knows the mix, so the phone carries the whole receiving path —
GRN list, per-line breakdown, post — against the same endpoints the desktop calls,
and stock is identical whichever end does it. The phone screen is not a smaller
copy of the desktop grid: a seven-attribute row doesn't fit a phone, so size and
quantity stay on the surface (tap a size chip, tap *rest* to take the remaining
balance) and the other five attributes plus pricing fold away per row. What does
*not* move to the phone is unposting, because it has to weigh payments, debit notes
and dispatches first — a desk decision made with the ledger in front of you.

QR previews are served as PNG (`/api/inventory/products/{id}/qr.png`) alongside the
SVG the web app uses: React Native's `<Image>` cannot rasterise SVG without a native
renderer, and the alternative — a dependency whose only job is drawing a code the
server already knows how to draw — buys nothing. Same payload, same 'M' error
correction, same code.

**One code, not two.** A product carries a single identifier of ours — the SKU —
and that is what its QR resolves to and what prints beneath it. An internal EAN-13
used to be minted alongside it so the label could carry a 1D stripe; once the
label went QR-only that second number bought nothing and gave two answers to
"what is this product's code". `Product.barcode` survives, but only to hold a code
the SUPPLIER printed: `inventory.match_product` keys a re-buy on it, which is what
keeps one item's weighted-average cost in one record instead of splitting across
two when the supplier's description wording drifts between invoices. Nothing
generates it, and it is never shown as our code.

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

## 7a. Product classification: many descriptions, one master

Every supplier writes the same garment differently — *Women's T-Shirt*, *Ladies
Tee*, *Female T-Shirt*, *LADIES TSHIRT* — and if each wording became its own
product the master would fill with near-duplicates: stock split across four
records, four QR codes for one item, and reports that add up to nothing. So a
description is mapped onto the **Product Category master** (~690 names, from GRN
PRODUCT DETAILS.xlsx) before it becomes stock (`services/categorize.py`).

The engine is **rule-based, not a model call**: no network, no key, no per-line
cost, and the same description always gives the same answer — which is what lets a
mis-mapping be reasoned about and fixed rather than re-prompted. Three steps:

1. **What we were told.** A wording a human has already mapped wins outright
   (below).
2. **Section.** Gender/age words are canonicalised to the master's own vocabulary
   (`women's`/`womens`/`female`/`ladies` → LADIES), which is what makes *Women's
   T-shirt* reach LADIES-T-SHIRT rather than the bare OVERALL T-SHIRT. A category
   asserting a *different* gender is excluded outright, however well it scores.
3. **Garment.** The canonical text is scored against every candidate name, blending
   `token_set_ratio` with `token_sort_ratio` — set-ratio alone returns 100 whenever
   one string's tokens are a subset of the other's, so "LADIES T SHIRT" ties with
   the bare "T-SHIRT".

**Confidence takes three signals, not one.** The score must clear `AUTO_THRESHOLD`,
beat the runner-up by `MIN_MARGIN`, *and* share a whole word with the category name.
The word test was added after a measured failure: "GENTS TEE" and "MENS-TIE" differ
by one letter, scored 87.5, and auto-filed t-shirts into MENS-TIE. Character
similarity across a whole string is not evidence — a shared word is. Measured over
the entire master it sends **no** extra name to review, because a real match always
shares a word; it only bites on wordings that match nothing, which is exactly where
a human should be asked. Near-spellings still count as shared (CHUDIDHAR/CHUDITHAR
at 89, PANT/PANTS at 89) while genuinely different words do not (SHIRT/SKIRT at 80,
TEE/TIE at 67).

**It learns from corrections (`CategoryAlias`).** No hand-written synonym list ever
finishes — suppliers keep inventing wordings. So the moment someone sets a category
on a GRN line by hand, that wording is remembered and maps itself on the next
invoice. The key is the *canonical* form, not the raw text, so one correction covers
every spelling that canonicalises the same way: teach it "Ladies Tee" and "Women's
Tee", "LADIES TEE 3PC" and "Tee Ladies" are taught too. Re-teaching overwrites, and
clearing the category forgets — a wrong alias is corrected exactly the way it was
created, so no admin screen is needed to undo one. An alias is never stored for a
name the master doesn't have, so learning cannot invent categories.

**What the rules cost, measured.** Feeding all 473 distinct master names back in as
descriptions, 438 map to themselves. Of the rest, most are a name mapping to its own
sectioned twin (BABY ITEMS → KIDS-BABY ITEMS), which is the standardisation working.
Two decisions came out of that sweep and are worth knowing:

- **`SET` is not a stopword.** It used to be, and that made 17 master names
  unreachable — six of them differ from a twin by that word alone (DHOTI SET vs
  DHOTI), so every "DHOTI SET" line filed silently as DHOTI. A *counted* set
  ("2 SET", "3PCS") is a pack quantity and is stripped; a bare one is part of the name.
- **BOYS/GIRLS survive section canonicalisation** (`QUALIFIERS`), because the master
  uses them to separate KIDS-BOYS PANT, KIDS-GIRLS PANT and KIDS-PANT — stripping
  them left one search text for three categories and the highest scorer took all
  three. BABY deliberately does *not* survive: the master has the catch-all
  KIDS-BABY ITEMS, and a preserved BABY drags every unrecognised "BABY something"
  into it. Fixing one name is not worth a bucket that swallows the unknown.

## 7b. Shortage entry: billed is not received (`services/shortages.py`)

Every quantity in §7 assumed the supplier sent what they invoiced. They often
don't. The bundle says 50 and 40 come out of the cartons, and before this existed
the receiving screen offered two answers, both false:

* **invent the ten** so the breakdown balances — inventory then carries phantom
  stock permanently, with a SKU, a QR, a valuation and piece codes, and it can
  never be dispatched because it does not exist; or
* **leave it unpostable** — the forty real garments stay unbooked, so the receipt
  is stuck behind a lie either way.

`GrnShortage` is the third answer. Recording it changes exactly one number:

```
PurchaseLine.received_qty = billed − short − damaged + excess
```

and `received_qty` then replaces `qty` everywhere the *physical* count is meant:
the target `split_status` balances against, what `post_grn` receives into stock
and serialises into `ProductUnit`s, and what goes on the carton label. `qty` is
untouched, for exactly the reason the breakdown never rewrites its line either —
it is the supplier's own figure, and invoice arithmetic, the reconciliation layer
and the payables side all reconcile against their document, not against our count.
The two are allowed to disagree; the set of shortage rows *is* the disagreement,
stated rather than absorbed.

**Why it lives in the Receive flow.** Not in Inventory, and this is the whole
design argument. The only person who can know what was in the box is the one
opening it, and the knowledge has a deadline: the instant the GRN posts, the
difference is gone. Stock reads 40, the invoice reads 50, and nothing in the
system records that they ever disagreed — a stock adjustment afterwards can
restore the *number* but not the *fact*, and a supplier claim needs the fact.
So shortages are keyed on the phone at the dock, they are frozen at post along
with everything else on a GRN, and correcting one goes through unpost.

**Three kinds, one axis.** `short` (never arrived) and `damaged` (arrived, rejected
at the dock) both stay out of stock and are claimable; `excess` is the mirror and
*does* become stock, because the goods are on the floor whatever the invoice says.
Damage discovered *later*, in stock already accepted, is deliberately not this — it
is a purchase return, and it reverses stock because there is stock to reverse.
Taking rejected goods into inventory only to write them off again would put units
we refused into the valuation for as long as the paperwork took.

**No money on the row.** A shortage is a fact about a count; what it is worth is a
fact about the GRN, derived from the line rate on demand (`unit_cost`) — the same
basis `services/returns.py` values a debit note at, because a claim can only carry
what the supplier charged. A frozen rate would be the same number in two places
free to drift, and a posted GRN's rates cannot change anyway.

**The payoff is that the claim writes itself.** `returns.build_from_purchase` lists
received goods at qty 0 — how many go back is still a decision — and shortages at
the counted quantity, because that one is already settled. Those lines carry
`shortage_id`, and `post()` reads it to value the claim **without a StockMovement**:
the units it debits never entered the ledger, so reversing them would remove the
same goods twice. `sync_shortage_lines` re-runs whenever a draft is opened, so a
shortage recorded or waived after the note was raised is picked up without anyone
rebuilding it, and `returnable()` caps each claim at its own shortage so two debit
notes cannot bill the same ten pieces.

Status is derived, not stored: `claimed` is computed from the posted debit notes
that reference the row, so the two can never disagree. `waived` is the one thing a
human asserts — the supplier is re-sending, or it is too small to chase — and the
row stays on the record either way. The **GRN Shortage Register** reports the lot
with unclaimed value separated out, draft GRNs included, since a shortage is worth
chasing before the receipt posts.

## 7c. Labels: a symbol that decodes (`services/barcode_svc.py`)

A label is the one part of this system that leaves the database and gets stuck on
a garment. It either scans or it does not, and when it does not, nothing upstream
matters. Three properties decide it, and all three were wrong.

**The symbol has to be all there.** segno emits SVG with `width`/`height` in pixels
and **no `viewBox`**. Without one there is no mapping from user units to the
viewport, so a stylesheet asking for `width: 26mm` resized the box and left the
drawing at its intrinsic size — and because the outer `<svg>` clips by default,
the overflow was cut off rather than scaled. A 41-module symbol drawn at scale 3
is 135px inside a 98px box: **the outer 27% of the code, one finder pattern
included, simply was not printed.** It looked like a QR and could not be decoded,
which is the worst way for this to fail. `_scalable()` injects the viewBox.

**The quiet zone is part of the symbol.** ISO/IEC 18004 requires four clear
modules on every side; a decoder uses that margin to locate the symbol's edge at
all, so printing less is not a slightly worse code but one a scanner may never
find. It was 2. `QUIET_ZONE = 4` now lives beside the renderer rather than in each
stylesheet, so it travels with the symbol into the sheet, the screen and the phone
PNG alike.

**The modules have to be big enough.** Around 0.33mm is the floor for a phone
camera in warehouse light. A garment tag's QR is 32mm, which holds a typical
payload at 0.65mm per module and the worst realistic one — a long supplier
description with every attribute filled, 53 modules — at 0.52mm, leaving room for
thermal bleed and a scuffed sticker instead of sitting on the limit. Carton labels
get 40mm because they are read across a rack. `qr_module_size_mm()` exposes the
figure so a test asserts it rather than a comment claiming it.

Layout follows from those. The QR is a column of its own that the details never
share; **every** text row is ellipsis-clipped, because one un-clipped row (the old
`.meta` had no overflow rule) is enough to push a long "S · Orange · Cotton" into
the code; rows carry fixed minimum heights so a product with no category and one
with a category produce the same card and a sheet stays a cuttable grid. The
payload is unchanged, so codes already printed still scan.

## 7d. Dates: one stored form (`services/dates.py`)

Every date field is a calendar picker, and every date is stored as ISO
`YYYY-MM-DD`. The picker is the visible half. The half that mattered is that
dates were previously stored exactly as they arrived — 31/07/2026 off a printed
invoice, 31-7-26 off a register page, 2026-07-31 off an e-invoice — and three
things were quietly wrong as a result:

* **Range search.** `GET /api/lr/search?date_from=` compares in SQL, which on text
  is alphabetical. With `31/07/2026` in the column, `01/08/2026` sorts *before*
  it, so a July-to-August filter returned the wrong rows. No error — just a
  shorter list than the truth, which is the hardest kind of wrong to notice.
* **Conflict detection.** `lr_link` flags a register row whose invoice date
  disagrees with the linked invoice. Two spellings of the same day compared as
  text disagree, so correct pairings were reported as mismatches.
* **Ageing.** `payments._parse_date` knew three formats; anything else became
  None and the bill silently lost its days outstanding.

So dates are normalised where they are written — one funnel per entry point:
`_coerce` in the LR router covers save, create and patch together;
`normalise_dates` covers the canonical invoice on confirm; payments, returns and
outward normalise on create. Anything readable becomes ISO. **Anything unreadable
is kept exactly as it arrived** — a date the parser cannot read is still what the
page said, and blanking a supplier's own figure is worse than storing it oddly.

`03/04/2026` is the 3rd of April. Day-first is assumed because every supplier and
register in this business writes it that way; month-first is tried only when
day-first cannot hold (`04/13/2026`). That assumption is stated in one place
rather than implied by the order of a format list.

The rule is duplicated in the UI, because `<input type="date">` renders a non-ISO
value as **blank** — pointing one at legacy data would look exactly like the date
had been lost, and the first save would make it true. `DateField` normalises on
the way in, and when a value cannot be read it shows the field empty *and prints
the original text beneath it*, leaving the stored value untouched until someone
picks a replacement. The two parsers are held to agreeing: 36 cases are run
through both and compared, because a screen and a server that disagree about
whether something is a date is worse than either rule alone.

## 7e. Three controls, one behaviour (`SearchBox` · `FilterChips` · `Section`)

Every list screen carries the same three controls, with the same icons, in the
same place. That is the whole design: this is a warehouse tool, and people are
trained on it by the person at the next desk rather than from a manual. Someone
who learns the controls on GRN already knows them on Inventory, Purchase Return,
Stock Outward, Stock Inward, Documents, Suppliers, LR Entry and Reports.

| | Icon | Scope | Rules |
|---|---|---|---|
| **Search** | `⌕` | filters what is *already on screen* | Esc clears it; a clear button appears once there is something to clear |
| **Filter** | `⛭` | decides what is on screen *at all* | always shows how many filters are active, and clears them all in one click |
| **Minimize** | `−` / `+` | collapses a panel | remembered per screen across navigation and reloads; a collapsed panel still says what it holds |

Two of those rules exist because of specific ways this screen can mislead:

* **A filtered list must never look unfiltered.** The chip row shows a count
  beside every scope and Inventory prints "Showing 9 of 9" under the table, with
  a one-click reset when those differ. Without it, someone lands on a filtered
  screen, sees four rows where there should be forty, and concludes stock has
  gone missing. That is the one misreading a stock screen cannot afford.
* **A minimized panel must still describe itself**, via `summary`. Collapse "Lines
  → inventory match" and the header still reads *5 line(s) · 4 new product(s)* —
  otherwise a minimized panel is indistinguishable from a missing one, and the
  next person reopens every panel to find what they wanted.

Icons never travel alone: each control carries a `title` naming what it does,
because an icon on its own teaches nobody the first time. The two structural
filters differ on purpose — mutually exclusive scopes (draft / posted / short /
all) stay **open** as chips, since they are the filter people reach for
constantly; anything richer (category, supplier, date range) lives behind the
`⛭` disclosure so the toolbar stays one line.

Collapsed panels and the open tab are kept in `localStorage`. A warehouse screen
is set up once for how someone works and then left alone; losing that on every
refresh is a small daily tax on the people who use it most.

## 7f. Theme: #5A3428 on a light surface (`frontend/src/styles.css`)

The brand colour is **#5A3428**, a deep warm brown. That choice decided the rest
of the theme, because a colour this dark only functions as a *primary* on a light
surface. Measured against the old dark navy chrome it was **1.4–1.7:1** — not a
poor accent, an invisible one. On white it is **10.7:1**, and white text on it is
the same. So the application is light, which is also how the reference ERP this
mirrors looks, and how an all-day back-office screen is usually built.

Neutrals are warmed — a little red in the greys — so they sit with the brown
rather than beside it; pure-grey chrome next to a brown accent reads as two
palettes that happen to share a screen. Semantic colours are deliberately kept
off the brand hue: nothing meaning "warning" may be mistakable for something that
merely means "Essa".

Every pair was measured rather than eyeballed. All text meets WCAG AA on its own
background (body 4.85:1 at the lightest, brand 10.73:1, each semantic colour ≥4.5
on both white and its own tint). Interactive borders are the one thing that
needed a deliberate darkening: dividers may be soft, but an input's edge is a UI
boundary under WCAG 1.4.11 and must reach 3:1, so `--line-strong` is #96867C
(3.50:1) rather than the softer grey that looked tidier.

Sizes and spaces come from the scales in `:root` — a one-off pixel value in a
component is how a layout drifts. Layout rules that came out of the rework:

* **Navigation gets its own row.** Eleven modules plus five account controls never
  fitted on one line; at 1600px the last button was clipped. Squeezing them was
  the wrong trade — navigation is the most-used thing on the screen.
* **Actions never shrink; prose does.** In every header the explanatory sentence
  is the flexible item and the buttons are fixed. A truncated sentence is a
  shorter sentence; a truncated button is a control nobody can reach.
* **Figures right, tabular, text left** — one rule for every table in the app.
* **Negative stock is styled as wrong**, not merely printed with a minus. A `-10`
  sitting in a column of ordinary figures is read straight past.

The phone app carries the same tokens for the same reason the desktop does: the
brand colour fails on its old dark background too, and one product should not
have two palettes.

## 8. LR Entry: two routes into one record

The reference system's **Transport Entry** screen is a single-consignment data
entry form. This app's LR Entry started as the opposite — photograph the register
page, vision reads every row — because that is dramatically faster for the ten or
twenty consignments a page holds. Both now exist and write the same `LREntry`,
distinguished by `entry_source`:

- **import** (`POST /api/lr/extract` → `/save`) — the fast path, and still the
  default. Rows are duplicate-checked (`lr_link`) before they land.
- **manual** (`POST /api/lr`) — one consignment keyed in, for goods that arrive
  with no page to photograph, and for correcting one that did.

Three consequences worth knowing:

**The columns are a union, not a copy.** The register page carries what the
transporter printed; the form also carries office decisions (purchase manager,
stock holding period, additional margin, auto-transfer location) that appear on
no page anywhere. So `lr.LR_PROMPT` asks vision only for the
printed subset — asking for the rest would invite invention — and
`lr_link.LR_ALL_FIELDS` compares only that subset when deciding whether a
re-imported row is a duplicate. Comparing the office columns would make every row
someone had since edited come back "doubtful" against its own twin.

**Required fields are enforced on the manual route only** (`REQUIRED_MANUAL`).
A register page names no agent and no box count, so holding an import to the
form's asterisks would reject rows read off a perfectly good page.

**`MasterOption` instead of a table per list.** A purchase manager, an LR mode or
an attachment type is a name and nothing else. Supplier / Agent / Transport /
Category keep their own tables because they carry structure (GSTIN, phone,
section); these do not. Fixed vocabularies (LR mode, attachment type, transfer
location) are seeded and reject additions so a typo cannot become a new mode of
transport; the open ones learn from what is typed, exactly as agents and
transporters already do. `RETIRED_OPTIONS` purges lists whose field has since
been dropped, so an install that ran an earlier build doesn't keep offering a
dropdown nothing reads.

`LRAttachment` is deliberately not a `Document`: a Document is fed to the
extraction engine and trains a supplier profile, whereas an LR copy or a photo of
a torn bundle is evidence filed against a consignment and must never enter that
pipeline.

**Then fifteen columns came back out.** The reference screen carries Company,
Bundle Rack, Section, Remark, Due Date, Pay Mode, PackageSlip No/Date, Actual &
Charged Weight, From/Receiving City, Loading Charge and Cash/Cheque. Built and
shipped, every one of them was empty on every consignment — Essa's registers
simply do not record them, and the warehouse does not put bundles away by rack
from this screen. They were dropped rather than left optional: a column that is
always blank still costs a prompt line (worse extraction, more to hallucinate),
a form box, a review cell and a register column, and it teaches whoever reads
the screen that blank is normal. `_migrate` issues `ALTER TABLE … DROP COLUMN`
(SQLite ≥ 3.35), falling back to leaving the column unmapped if that fails,
which is what the older `place` and `purchaser` columns already do. The `city`,
`pay_mode`, `company`, `bundle_rack` and `section` option lists went with them.

Note `cash_cheque` was among them and was *not* new — it had been part of the
freight settlement since the first version of this module. Freight now settles as
Paid/ToPay plus an amount.

Company is worth calling out separately: the deployment still has one, in
`config.COMPANY_NAME` / `COMPANY_GSTIN`, which is what identifies the buyer
during extraction. What was removed is a *per-consignment* company, which only
earns its place if Essa receives goods for more than one billing entity.

The general rule this leaves: mirror the reference screen to learn what the
business records, then delete what it turns out never to fill. Matching a form
field-for-field is a starting hypothesis, not the specification.

## 9. The remaining modules

The same canonical shape and the inventory ledger extend to the rest of the
recordings:

- **Code generation** — suppliers who don't pre-barcode (AMS, Matoshree, Mehak)
  get an `ESSA-#####` SKU on GRN post; the QR label prints from it.
- **Stock Outward / Stock Inward & Warehouse reports** — outward is a negative
  `StockMovement`; the ledger already supports it and is the reporting spine. The
  two screens are two ends of ONE document: the warehouse packs and posts it,
  the destination accepts it (`accepted_qty` per line), and a short delivery is
  the difference between two columns rather than a second record to reconcile.
  Both render the same product projection (`services/stock_view.py`) — QR, name,
  attribute tuple, and the receipt the stock came in on — because a dispatch and
  an acceptance are both someone matching a row against a garment in their hand.
- **Payments (debit / discount / TDS)** — the taxes block already models TDS
  (GH Enterprises) and discounts (Mehak); a payables ledger keys off supplier +
  invoice number + grand total, all captured on the `Purchase`.
- **Purchase Return** — a return references a posted `Purchase` and reverses the
  relevant movements. Its lines are the rows of that GRN which actually *became
  stock* (a line broken down into variants is returned as its variants, never as
  the bundle that never existed), and each is valued at the **received price** —
  the variant's GRN rate, else the invoice line rate, else the weighted-average
  cost. The rate is re-derived from the GRN at post time and never accepted from
  a client, so a debit note cannot be raised at a selling price: it settles a
  supplier account and has to reconcile against that supplier's own invoice.
- **Analytical reports** — built on structured, reconciled data instead of
  re-keyed spreadsheets. 33 of them (`services/reports.py`), in the seven groups
  the reference app uses, every one a projection of the ledger or the documents
  rather than a second copy of the figures. Each declares the filters it accepts
  and `run()` passes only those, so a new report honours a date range without a
  route change and one that takes none is never handed an argument it would choke
  on. The CSV export runs the same call with the same filters, because an export
  that quietly returns a different set of rows than the screen is worse than no
  export.

### What the reference catalogue has and this does not

Six reports are **absent rather than empty**, and the distinction matters: an
empty report reads as "nothing happened" when the truth is "nothing is recorded".
Each needs a data model before it can carry a number.

| Report | What is missing |
|---|---|
| Stock - Depreciation | No depreciation at all — no rate, method or asset register. Stock is held at weighted-average cost. |
| Job Work Outward / Inward | No job-work concept: goods sent to a processor and returned are not modelled. |
| Invoice Vs Purchase Order | No purchase orders. Intake begins at the supplier's invoice. |
| Retail Stock Analysis | One warehouse. A dispatch reduces our stock; what the receiving store then holds is its own book. |
| Purchase Return (Cancelled) | A return is draft or posted. Nothing is cancelled. |
| Transport Payment Report | Freight owed is on the consignment; transport *payments* have no ledger. |

Two of the reports that ARE present carry a `note` for the same reason — they
answer a narrower question than their name implies. **Transport Pending Bills**
shows freight incurred on TOPAY consignments, not an outstanding balance, because
nothing can be marked settled. **Stock Movement - Locationwise** reports what each
destination was *sent*, not what it holds. Saying so on the report is better than
letting someone infer it from a total that will not tie out.

Recommended remaining order: (1) purchase orders (unlocks Invoice vs PO and turns
receiving into three-way matching) → (2) destination stock (unlocks Retail Stock
Analysis and closes the transfer loop) → (3) a transport payables ledger. Each
consumes the canonical shape and the ledger these modules already guarantee.

## 10. Production hardening checklist

The foundation is production-minded (real relational model, versioned learning,
audit trail, pluggable providers, DB-agnostic). Before going live, add: user
authentication and per-role access; move uploads to object storage (S3) and the
DB to Postgres; a background queue for extraction so large PDFs don't block
requests; PDF multi-page handling (poppler is already a dependency); an
accuracy dashboard comparing machine drafts to human corrections; and rate/cost
controls on the vision provider.
