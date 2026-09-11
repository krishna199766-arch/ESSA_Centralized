# Taqua Silks — End-to-end retail management app

A complete Flask-based retail management system for Taqua Silks with GST-compliant billing, inventory, CRM, loyalty, purchase orders, staff & reports. Responsive UI works on desktop browsers, tablets at the counter, and mobile phones.

## Features

- **POS / Billing** — cart-based sales, SKU search/scan, GST split (CGST/SGST/IGST auto-detected by state), printable tax invoice with HSN codes and GSTIN. Each till is mapped to the storey it stands on, and its bills are numbered from that floor's own series — `TG26-001` on the ground floor, `TF26-001` on the first — automatically, by the backend, with the number shown on screen before the sale is taken. See **Floor-wise bill numbers** below.
- **Delivery** — the collection desk. Scan the bill (QR or number), scan every garment as it goes in the bag, and the handover is recorded against the bills it covers. Part collection is a first-class answer, so the balance stays owed and shows on a "still to collect" list; a piece tag can only go out once; and a piece nobody can scan needs a manager and a reason, both printed on the delivery note. Moves no stock and no money — the sale already did both.
- **Inventory** — products with categories, fabric/color/size, HSN codes, GST rate, cost/selling price, stock, reorder levels, low-stock highlighting, full stock-movement audit log, and which floor each item is held on.
- **Physical stock audit** — count a floor item by item, by QR scan or search, against what the books say. Shows purchase and sales quantities so the system figure explains itself, records shortage and excess per line, and — only once approved — corrects stock through real movements rather than by overwriting a number. See **Physical stock audit** below.
- **Promotions** — a configurable offer engine, not a hard-coded offer. An admin writes a scheme (*buy 3 from LADIES-CHUDITHAR → 1 LEGGINGS free*) and the till applies it by itself: the free item appears on the billing screen as the cart is built, is billed at ₹0, comes off stock with its own movement against the same bill, and is recorded for reporting and audit. Quantity, product, category and mixed schemes are all the same rows in a different arrangement, so a new offer is data rather than code. See **Promotions** below.
- **Customers (CRM)** — profiles, purchase history, GSTIN support for B2B, loyalty points ledger with configurable earn rate + redemption.
- **Suppliers & Purchase Orders** — supplier records, create POs, one-click "Receive" that adds to stock and updates cost price.
- **Staff & Roles** — admin / manager / cashier roles, auto attendance check-in/out on login/logout, salary & commission tracking.
- **Reports** — daily/monthly sales trend, top products, top customers, payment-method breakdown, GST summary (CGST/SGST/IGST), low-stock report; date range picker.
- **Responsive UI** — Bootstrap 5 layout, works web/desktop/tablet/phone. PWA manifest included for "install on home screen" on mobile.

## Setup

```bash
pip install -r requirements.txt
python run.py init          # creates DB + seeds sample data
python run.py               # starts dev server at http://localhost:8000
```

## Default logins

| Username  | Password    | Role    |
| --------- | ----------- | ------- |
| admin     | admin123    | admin   |
| manager   | manager123  | manager |
| ravi      | cashier123  | cashier |
| meena     | cashier123  | cashier |

## Configuration

Set environment variables (or edit `config.py`) for your shop:

- `SHOP_NAME`, `SHOP_ADDRESS`, `SHOP_PHONE`, `SHOP_GSTIN`, `SHOP_STATE_CODE`
- `FY_START_MONTH` (default 4 — April, which decides the `26` in `TG26-001`)
- `LOYALTY_EARN_RATE` (default 1% of bill), `LOYALTY_POINT_VALUE` (₹ per point)
- `SECRET_KEY` (set this in production)
- `DATABASE_URL` (SQLite by default, PostgreSQL supported)

## Project structure

```
Textile Retail Shop/
├── run.py                # entry point + DB init
├── config.py             # settings
├── requirements.txt
├── app/
│   ├── __init__.py       # app factory, blueprints, filters
│   ├── models.py         # SQLAlchemy models
│   ├── seed.py           # sample data
│   ├── utils.py          # role decorator, number generator
│   ├── promotions.py     # the promotion engine: what a cart earned, and in stock
│   ├── billing_numbers.py# floor → prefix → financial year → next bill number
│   ├── audits.py         # counting a floor, and what may then move stock
│   ├── places.py         # company / store / floor / till, and what a till bills as
│   ├── routes/           # blueprints: auth/main/inventory/pos/customers/staff/
│   │                     #   reports/returns/promotions/stores/floor/delivery
│   ├── templates/        # Jinja templates (Bootstrap 5)
│   └── static/           # css, manifest
```

## Checks

No test framework and no test dependency — each file is a script that builds what
it needs, asserts in plain prose, and runs against a throwaway database.

```bash
python test_mounted.py         # every screen still builds when `app` isn't ours
python test_stock_audit.py     # counting a floor, and what it refuses to adjust
python test_bill_numbers.py    # floor series, and 20 tills billing at once
python test_promotions.py      # the promotion engine, end to end
python test_delivery.py        # goods leave only when handed over
python test_warehouse_sync.py  # needs a warehouse database beside the shop
```

**Run `test_mounted.py` after touching any route.** The shop is deployed inside
the Essa warehouse, which owns the name `app` by the time a request arrives — so
an `import` written *inside* a view reaches into the wrong package and 500s that
screen for everyone, while every other suite passes because they all run the shop
standalone. That shipped once. This one swaps the package away exactly as the
mount does and then asks for every screen, so it cannot ship again. Every
`from app…` in this codebase belongs at module level; see `app/places.py` and
`backend/app/pos_mount.py` for the long version.

## Where prices come from

The shop does **not** own its prices. `selling_price` and `cost_price` are copied
down from the warehouse on every sync (`app/warehouse_items._apply`), so a price
typed in here would be silently undone the next time the warehouse database
changed — which is on almost every request. The Inventory form therefore shows
those two boxes filled and read-only on any item that came from the warehouse,
with a pointer to where a change actually sticks: the warehouse's **Price
Changer**, which records every change and can put a whole batch back.

A product the shop created itself has no warehouse behind it, no sync touches it,
and its prices stay fully editable here.

## Physical stock audit

Count a floor against the books:

```
select floor → scan each tag → the system says what it believes →
enter what is there → complete → review → approve → apply to stock
```

**A count is not an adjustment, and that separation is the whole module.**
Scanning a rack records what the shelf held against what the books said, and
changes no stock at all. Deciding to believe the count is a *later, approved*
act that writes one **stock movement per product** against the audit's number —
never a silently rewritten figure. If a count corrected the books as it went,
there would be no independent record of what was ever actually on the floor,
which is the only thing an audit exists to produce.

**It shows its working.** Every line carries *purchase → sales → system stock →
physical → difference*, and the first two come off the movement ledger, so
`purchase − sales` **is** the system figure. Nothing has to be taken on trust
during the one job that exists not to.

**Scan-driven, with everything a scan needs to show.** A tag resolves through the
same entry point the till uses — SKU, printed barcode, warehouse QR or a
per-piece code — and answers with the product, its category and every attribute
(size, colour, material, pattern, fit, type, design no, MRP, cost, selling
price), what the books say, and how they got there. Scanning something already
counted says so rather than quietly doubling the shelf. A camera works for a
phone walking the floor; a search box covers a tag that will not scan.

**What a floor's stock means here.** The shop keeps **one quantity per product**
— the figure the till sells against — and this does not invent a per-floor one:
splitting the number across storeys would need the till to know which floor each
sale came off, and it does not. What a floor has is the set of products *held* on
it (`Product.floor_id`, a place), so a floor's count is a count of the garments
that live there. **The count is what builds that mapping**: scanning a garment on
the second floor is the act that records it there.

That follows through into one rule worth knowing. A garment found on a floor it
does **not** belong to is counted — it is where the piece is — and flagged, but
its variance is **not** applied to stock. That count found *where some of them
were*, not how many the shop has; writing it into the figure would silently write
off every piece still standing on the floor they belong to. Its own floor's count
settles the quantity. The screen says which lines that affects before anybody
presses Apply.

Each floor shows its status (not started · in progress · completed · reviewed ·
approved), every step is stamped with who and when, and the whole count is
exportable as CSV, printable with signature lines, and reportable — *Physical
stock audits* and *Stock audit detail* both answer from the ask bar.

## Floor-wise bill numbers

```
POS → floor → prefix → financial year → next running number → bill number
Ground Floor POS →  TG  →      26      →       001          →  TG26-001
```

Each floor of a store keeps **its own running series**, so the ground floor
counts `TG26-001, TG26-002, …` while the first floor is independently on
`TF26-001`. A cashier never types a bill number and there is no field on the
screen that could change one.

**The mapping is data, and it is created in the warehouse.** The chain

```
Business → Warehouse → Store → Floor → POS terminal
```

is one master, kept in the Essa warehouse's **Locations** screen — add a floor
under a store, give it a prefix, put a till on it — and this shop **mirrors** it,
exactly as it mirrors the stores themselves and the category master. A fifth
floor, a second store, or a different set of letters is typing, not deploying;
nothing in the billing path knows the words "Ground" or "TG".

The shop's **Floors & tills** screen is the window onto that: rows from upstairs
are marked `warehouse` and are read-only here, because a prefix changed on this
side would be silently put back by the next sync, and a change that un-happens is
worse than one that is refused. What the screen *does* own is a shop running
**alone**, with no warehouse to read — floors created there are marked `local`, a
sync leaves them alone, and a store with none gets a one-click **Add the four
standard floors** (Ground TG · First TF · Second TS · Third TT).

Mirrored rows are matched on the warehouse's own row id, not on their name, so a
floor renamed upstairs is renamed here rather than duplicated with the tills left
pointing at the old one. A row the warehouse stops listing is switched off, never
deleted — its prefix is on bills that are still read back — and a till this shop
created for itself is never retired by a sync at all.

**The financial year is the year it started in** — April to March, so a bill rung
in September 2026 reads `26` and still does the following March. On 1 April it
becomes `TG27-001` with nothing having to run overnight: the year is part of the
series' key, so the first bill of the new year opens a new counter on its own.
`FY_START_MONTH` moves the boundary for a business that closes its books
elsewhere.

**The series is keyed on (prefix, financial year), not on the floor.** The bill
number *is* prefix + year + number, so anything sharing those two must share one
counter — otherwise two floors configured with the same prefix would each hand
out `TG26-001`. Two floors on one prefix therefore share one register, which the
Floors screen warns about, because it is almost always a typo.

**No duplicates, and no gaps.** The number comes from a row in `bill_sequences`,
handed out by an atomic `UPDATE … SET last_number = last_number + 1` — never by
reading the last number and adding one in Python, which is how two tills both
write `TG26-008`. It is allocated inside the same transaction as the sale, so a
bill that fails half-way gives its number back rather than leaving a hole in a
statutory series. `test_bill_numbers.py` runs twenty simultaneous checkouts
through the real HTTP path and asserts twenty distinct numbers, no gaps, and no
failed sales.

Making that hold on SQLite needed two settings on the shop's own database (see
`app/__init__._enable_concurrent_writes`): write-ahead logging, and **billing
requests only** beginning their transaction as writers. A transaction that reads
and then writes has to upgrade its lock, and SQLite refuses that upgrade
*immediately* rather than waiting — measured at one failed sale in five under
contention, and unfixable by retrying, because the retry re-reads the same stale
snapshot. Only the two billing endpoints take the lock up front, so a manager's
report — or the ask bar's wait on an external model — never blocks a till.
Postgres needs none of it; it has real row locking.

**Nothing is rewritten.** Every bill keeps its own copy of the prefix, financial
year and sequence it was built from, so "show me the TG series this year, in
order, and prove none is missing" survives a floor being renamed or its prefix
changed. The invoice register filters on that, and **Bill series** lists every
series and where it has got to.

**Tills that aren't mapped keep working.** A counter on no floor bills the shop's
plain `INV-000001` series exactly as it always has, and the billing screen says
so rather than refusing a customer over an incomplete master. Floor *sales* — the
phone cart, which is not a till and has no counter — stay on that series too.

## Promotions

An offer is **six kinds of row**, not six columns on one:

```
PromotionScheme          the offer, its dates, its limits, its rules
  PromotionCondition     one group that must be bought   "any 3 of…"
    PromotionConditionItem   a product, or a whole category
  PromotionReward        one thing then given            "1 of…, free"
    PromotionRewardItem      a product, or a whole category
  PromotionPlace         where it runs (no rows = everywhere)
```

Two conditions mean **both** — *2 chudithars **and** 1 dupatta*. Several items
inside one condition mean **any** of them. That grammar covers every shape in
the brief — quantity, product, category, mixed, multiple rewards, store-specific
— without the billing code learning about any particular offer, which is the
whole point: `app/promotions.py` has never heard of a chudithar.

What happened is recorded apart from what was configured. **PromotionApplication**
is this scheme on this bill, carrying a *copy* of the scheme's name and code so a
March bill still says what the customer was actually given after the offer is
renamed or switched off. **PromotionAudit** is every event, including the ones
that gave nothing away — a scheme that qualifies on twenty bills and hands over
nothing because its reward has been out of stock all week looks like a scheme
nobody uses until the audit register says otherwise.

**A free item is stock.** It is a real invoice line at ₹0 that reduces the
product's quantity and writes a movement under the reason `promo`, against the
same bill as the sold lines. It can be scanned at the delivery desk, returned,
and reported on. The one rule it does *not* get is a stricter stock test than a
sold line: the engine checks `Product.stock_qty`, the same figure the till
refuses a sale on, because holding free goods to the branch split the till
ignores would mean offers silently never firing at branches stocked before that
table existed.

**Tax.** The reward line is billed at zero, so its taxable value and GST are
zero and the tax on the bill is the tax on what was charged — the ordinary
treatment of goods given away with a purchase, where the consideration sits in
the qualifying items' price.

**The till never decides.** The counter POSTs the cart to `/pos/api/promotions`
on every change so the cashier can see the free item and, just as importantly,
see an offer that qualified and *cannot* be honoured. That is advisory: `checkout`
runs the same evaluation again from the products it is about to bill, and a free
line sent by the page is discarded. A modified page cannot bill itself a garment.

**Priority and stacking.** Schemes run lowest-priority-first, and each takes the
garments it uses out of the cart so the next sees only what is left — that is
what stops one purchase earning two rewards. `stackable` is the deliberate
exception and has to be ticked per scheme.

**Returns re-check the offer.** Hand back a qualifying garment and the free item
is no longer earned. Each scheme says what happens: take its value off the refund
(the default), require it back before the return is accepted, or let the customer
keep it. The returns screen says which *before* a refund figure is agreed, and the
application is reduced so the reports net down instead of counting a reward the
customer no longer qualifies for.

Five reports — scheme performance, free items issued (a stock report as much as a
promotion one), by store and counter, by date, and the audit register — and all
five are reachable from the Reports ask bar.

## GST notes

- Products carry HSN code + GST rate (0/5/12/18/28).
- If the customer's `state_code` matches the shop's, tax is split as CGST + SGST. Otherwise it's IGST (interstate).
- The invoice PDF-style page shows GSTIN, HSN codes and a GST breakup, ready to print on any thermal or A4 printer.

## Notes for packaging

The app is a plain Flask web app — it runs the same on Windows, Mac and Linux. To ship as a "desktop" app you can wrap `run.py` with PyInstaller or pywebview; for mobile, the PWA manifest lets users add it to home screen from any browser.
