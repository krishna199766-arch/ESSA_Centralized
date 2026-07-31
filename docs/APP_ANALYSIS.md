# Reference-app analysis (from the screen recordings)

The reference ERP is **Stone Soft Solution**'s warehouse product. Notes below
drive how our replica's modules should behave. Screens are grouped under
**Warehouse**, **Store**, and **Finance**.

## Warehouse menu (full module list, from the dashboard)
Dashboard · Courier · Transport Entry · Invoice · Inventory Entry · Barcode ·
Transport Issue · Transport Receipt · Direct Purchase · Receive Goods ·
Purchase Return · Stock Outward · Physical Stock · Stock Item · JobWork · Reports

## Inventory Entry (Warehouse / Inventory Entry)
Per-product master + purchase capture. Fields seen: Supplier, Product, Color,
Size, HSN Code, Pattern, Fit, Type, Material, Buying Price, Avg Margin, MRP,
Sale price, Margin %, Design No, Divis Value, P.O No, Last Price. Line grid with
per-row **edit / update / delete** actions (red/green/blue icons) — rows are
editable in place. A size/qty **breakup modal** captures quantities per size.
Totals row: Total, GST %, Taxable, Tax, Discount, Qty.
→ Implication: products carry rich attributes and **stock/line details are
editable after entry**. Our app must allow editing product details and adjusting
stock.

## Receive Goods / Direct Purchase (supplier inward)
Supplier purchase received into the warehouse — this is the GRN we already
built (extraction → GRN → inventory).

## Stock Outward (Warehouse / Stock Outward)
Dispatch / inter-location transfer. Header: Order code, Date, From Company
(ESSA), From Location (WAREHOUSE), Packed By, To (destination — a sale
location/store or customer, e.g. "TASJUE SILKS, TIRUPUR"). A **barcode scan**
field adds rows: Barcode, Name, Size, Supplier, Cost, Qty, Accepted.
→ Decrements warehouse stock. Sent (transferred) qty vs Accepted qty tracked.

## Stock Inward (Store / Stock Inward)
The **receiving** side of an outward transfer, at the destination store. Shows
the bundle (package code, From/To location, Packed By, Received By), a grid with
Transferred vs Inward qty, then "Successfully saved" and prints a **Goods
Transfer** note. So inward = accept a transferred bundle (partial accept
possible).
→ Two-location transfer model: Outward (warehouse) → Inward accept (store).

## Purchase Return (Warehouse / Purchase Return)
Type = **Return - Debit Note**. Header: Date, Mode, Stock Location, Company,
Warehouse, Supplier, Ship-to, Agent, Barcode. Grid: Barcode, HSN, Product,
Design, Qty, Date, Discount %, Tax %, Tax, Amount. Footer: Bundles, Amount,
Total Qty, Freight, Discount, Total Pieces, Total, Invoice No, Include Barcode.
→ Returns goods to a supplier against a reference invoice: reduces stock and
raises a **debit note** that reduces the supplier payable.

## Supplier Payment (Finance / Supplier Payment)
Accounts payable. Filter by Company, Supplier, Agent, Date/Invoice range →
**Search Pendings** lists unpaid invoices (S.No, Inv No, Type, Inv Date, Days,
Discount, Total). Select invoices; per-invoice/aggregate apply **Discount**,
**TDS** (checkbox + %), **Debit Note** adjustment; see **Payable**. Enter
Payment Mode (NEFT/RTGS, Cash, Cheque), Bank, Cheque No/Date, Ref No, Paid
Amount, Remarks → saves a **receipt (ESP####)**. A payment **register** lists
all payments (Amount, Discount, TDS, Paid, Created/Modified By, PDF export) and a
**Payment Summary Report** per receipt.
→ Four variants recorded: plain Creation, withDebit (debit-note adjust),
withDiscount, withTDS.

## Reports (Warehouse Reports) — catalogue to implement
- **Stock:** Stock Report · Stock Movement (location wise) · Warehouse Stock
  Analysis · Retail Stock Analysis · Stock (area wise) · Stock Audit
- **Outward:** Outward Report · Outward Details · Pending Inward/Outward · JobWork
- **Purchase:** Pending/Cancelled Purchase Request · Purchase Done · Purchase
  Order Detail · Purchase (barcode wise) · Section-wise Purchase · Supplier
  Pending Bills · Transport Pending/Payment
- **Purchase Return:** Purchase Return · Section-wise · Cancel · Audit
- **Masters:** Product Master · Supplier Master · Agent Master · Tax Master
- **Invoice:** Invoice · Invoice Detail · Invoice→PO

## Build order chosen
1. Product edit + manual stock adjustment (this iteration)
2. Stock Outward (this iteration)
3. Supplier Payments ledger w/ discount, TDS, debit note (this iteration)
4. Purchase Return (next)
5. Reports (next)
