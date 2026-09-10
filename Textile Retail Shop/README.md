# Taqua Silks — End-to-end retail management app

A complete Flask-based retail management system for Taqua Silks with GST-compliant billing, inventory, CRM, loyalty, purchase orders, staff & reports. Responsive UI works on desktop browsers, tablets at the counter, and mobile phones.

## Features

- **POS / Billing** — cart-based sales, SKU search/scan, GST split (CGST/SGST/IGST auto-detected by state), printable tax invoice with HSN codes and GSTIN.
- **Delivery** — the collection desk. Scan the bill (QR or number), scan every garment as it goes in the bag, and the handover is recorded against the bills it covers. Part collection is a first-class answer, so the balance stays owed and shows on a "still to collect" list; a piece tag can only go out once; and a piece nobody can scan needs a manager and a reason, both printed on the delivery note. Moves no stock and no money — the sale already did both.
- **Inventory** — products with categories, fabric/color/size, HSN codes, GST rate, cost/selling price, stock, reorder levels, low-stock highlighting, full stock-movement audit log.
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
│   ├── routes/           # blueprints: auth/main/inventory/pos/customers/staff/
│   │                     #   reports/returns/promotions/floor/delivery
│   ├── templates/        # Jinja templates (Bootstrap 5)
│   └── static/           # css, manifest
```

## Checks

No test framework and no test dependency — each file is a script that builds what
it needs, asserts in plain prose, and runs against a throwaway database.

```bash
python test_promotions.py      # the promotion engine, end to end
python test_delivery.py        # goods leave only when handed over
python test_warehouse_sync.py  # needs a warehouse database beside the shop
```

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
