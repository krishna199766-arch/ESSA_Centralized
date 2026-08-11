# Taqua Silks — End-to-end retail management app

A complete Flask-based retail management system for Taqua Silks with GST-compliant billing, inventory, CRM, loyalty, purchase orders, staff & reports. Responsive UI works on desktop browsers, tablets at the counter, and mobile phones.

## Features

- **POS / Billing** — cart-based sales, SKU search/scan, GST split (CGST/SGST/IGST auto-detected by state), printable tax invoice with HSN codes and GSTIN.
- **Inventory** — products with categories, fabric/color/size, HSN codes, GST rate, cost/selling price, stock, reorder levels, low-stock highlighting, full stock-movement audit log.
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
│   ├── routes/           # blueprints: auth/main/inventory/pos/customers/suppliers/staff/reports
│   ├── templates/        # Jinja templates (Bootstrap 5)
│   └── static/           # css, manifest
```

## GST notes

- Products carry HSN code + GST rate (0/5/12/18/28).
- If the customer's `state_code` matches the shop's, tax is split as CGST + SGST. Otherwise it's IGST (interstate).
- The invoice PDF-style page shows GSTIN, HSN codes and a GST breakup, ready to print on any thermal or A4 printer.

## Notes for packaging

The app is a plain Flask web app — it runs the same on Windows, Mac and Linux. To ship as a "desktop" app you can wrap `run.py` with PyInstaller or pywebview; for mobile, the PWA manifest lets users add it to home screen from any browser.
