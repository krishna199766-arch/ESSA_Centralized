# ESSA Warehouse — mobile app (React Native / Expo)

A phone app for the warehouse team, in the order the goods move. Two tabs:

**Receive** — open a goods receipt, **break a billed bundle into the sizes that
actually arrived**, and post it. A supplier bills "WOMEN'S T-SHIRT, 50 PCS" and
never prints the mix; the person opening the cartons enters 20 M and 30 L, and
posting turns each row into **its own inventory record with its own SKU and QR
code**, its own inward stock movement and its own weighted-average cost. The QRs
appear on screen straight after posting, ready to print.

**Bundles** — the cartons. Every GRN line gets a **carton label** the moment it
posts (`ESSA-B-00001`): scan it, say which rack it went on, and later find it
again. When the box is opened, detail each item from this screen and then **Tag &
print** — that is when the individual garment tags come out.

**Products** — **physically detail each product**: pick one (search or barcode),
record what you see — Color, Size, Pattern, Fit, Type, Material, Design No, MRP,
Sale price, Discount % — and save.

Both write straight into the **same ESSA database** over your WiFi (via the
existing backend API), so a GRN received on the floor and one received at a desk
produce identical stock.

It talks to the desktop/server app you already run (`run.sh` / `run.bat`). No new
backend — the server just needs to be running and reachable on the network.

## Run it on your phone in 2 minutes (Expo Go — no Android Studio/Xcode)

1. On the **computer running the ESSA server**, start it as usual
   (`./run.sh` or `run.bat`). Note that machine's LAN IP (e.g. `192.168.1.20`)
   — `ipconfig` on Windows, `ifconfig`/System Settings on Mac. The server must be
   reachable at `http://<that-ip>:8000` (it already binds all interfaces).
2. On any computer, in this folder:
   ```bash
   npm install
   npx expo start
   ```
3. Install **Expo Go** from the App Store / Play Store on the phone.
4. Scan the QR code shown by `expo start` with Expo Go (phone on the **same WiFi**).
5. In the app: enter the server address `http://<that-ip>:8000` → **Connect** →
   sign in (default `admin` / `essa@123`) → **Receive** to break down and post a
   GRN, or **Products** to detail what's already in stock.

> If Expo Go says the SDK is too old/new, run `npx expo install expo@latest`
> then `npx expo install --fix` in this folder, and `npx expo start` again.

### "The request timed out" / QR won't connect?

That's Expo Go failing to reach the Metro bundler (port 8081) over your local
network — a firewall or same-WiFi issue, not the app. Start in **tunnel mode**
(routes over the internet, ignores firewalls/subnets):

```bash
npm run tunnel        # = expo start --tunnel
```

Scan the new QR. If it still fails, also check:
- Phone and computer on the **same WiFi** (not a "Guest" network; disable AP/client isolation on the router).
- **Firewall**: allow Node.js / ports 8081 (bundler) and 8000 (the ESSA server).
- Turn off any **VPN** on the phone or computer.
- `npx expo start -c` to clear the cache and retry.

Note: port **8081** is only the Expo dev bundler that loads the app into Expo Go.
It's separate from port **8000**, which is the ESSA server the app talks to for
data — you still enter `http://<computer-ip>:8000` inside the app.

## Build a real installable app (APK / IPA) — later

Expo Go is perfect for daily use on the local network. For a standalone app you
install without Expo Go:

```bash
npm install -g eas-cli
eas login
eas build -p android --profile preview     # produces an .apk you can sideload
# eas build -p ios                          # needs an Apple developer account
```

## Receive: bundle → sizes → stock

A GRN appears in the **Receive** tab once its invoice has been read and confirmed
on the desktop app. Open it and, on any line:

1. **Break down into sizes.** Tap a size chip to add a row (S, M, L, … from the
   shared option lists), type the quantity, or tap **rest** to give a row
   everything not yet assigned. The header tracks *20 of 50 assigned · 30 left*.
   Open **▸ colour, material, category, pricing** on a row for the rest —
   colour, material, pattern, fit, type, design no, category, and per-size rate /
   MRP / sale price / discount.
2. **Save.** A breakdown that doesn't add up to the billed quantity still saves —
   it's normal to come back to it — but the line is flagged and **Post is
   disabled** until it balances, because posting a half-finished breakdown would
   silently lose or invent units.
3. **Post to inventory.** Every row becomes a product with its own **SKU + QR**,
   an inward stock movement, and a weighted-average cost. The bundle line is
   marked **split** and never becomes stock itself — the rows below it do, and
   each is tracked separately from that moment on.
4. **Detail each item, from the same screen.** The receipt turns into a worklist —
   *"4 of 4 still to detail"* — and tapping an item opens its detail form with the
   QR shown, so you can check the label in your hand against the record. Fill in
   what the invoice couldn't say (fit, pattern, material, MRP, sale price), save,
   and the row ticks over to **detailed**. Its QR is confirmed and now carries
   those attributes, so a scan reads the full item even with no network — which is
   why it is worth detailing *before* printing the labels.

Two details worth knowing:

- **A row's identity is its whole attribute set.** Re-buying exactly *L / Red /
  Cotton* merges into that product and re-averages its cost; anything different
  becomes a new one. So fill in what actually differs, and nothing else.
- **⌗ QR** on a line (or a variant row) links it to a product that already
  exists, by scanning its QR or typing its SKU — for goods you already carry.
- **The QR is issued at post, not at detailing.** Nothing sits in stock without a
  code, which is what would happen if the label waited for someone to inspect the
  item. Detailing doesn't mint a second code — the SKU never changes — it *fills
  the existing one out*: the payload grows from `L · Red` to include fit, pattern,
  material and pricing. Print after detailing and the sticker carries the lot.

## Two labels, and when each is printed

| | Carton label | Garment tag |
|---|---|---|
| code | `ESSA-B-00001` | `ESSA-00004-003` — one per piece |
| printed | **at GRN**, from the receipt's *Carton labels* button | **later**, from **Bundles → Tag & print** |
| used for | putting the box on a rack, finding it, moving it | packing, tagging, retail sale |
| shows | 50 PCS, the size mix, GRN/invoice, and a LOCATION line to write on | size, colour, category, SKU, MRP |

The two codes are deliberately different kinds. Scanning a carton where a garment
is expected fails with "that is a bundle code" rather than quietly dispatching a
whole box as one shirt.

**Tag & print is refused while any item in the carton is still undetailed** — the
entire reason the garment tags wait is that by then someone has looked at the goods
and the tag can carry fit, material and price. Detail the items first (tap them in
the bundle), then tag.

**Garment tags are per piece, not per SKU.** A carton holding 8 of one size prints
8 *distinguishable* tags — `ESSA-00008-001 … -008` — rather than 8 copies of one
code, so a returned or missing garment can be identified individually. Fabric sold
by the metre has no pieces to number and falls back to one tag per SKU.
- **Correcting a posted GRN** is a desk job: unpost it in the desktop app, fix
  the line, post again. The phone deliberately can't, because unposting has to
  check payments, debit notes and dispatches first.

## How it works

- **Products come from invoices.** They're created when a GRN is posted — from
  this app's Receive tab or from the desktop. The Products tab never creates
  them, it enriches what the invoices produced.
- **Pending vs detailed.** The list defaults to *pending* (not yet detailed).
  Saving marks a product **detailed** (with who/when), and it drops off the
  pending list. Toggle to *all* / *detailed* anytime.
- **Dropdowns + custom.** Color/Size/Pattern/Fit/Type/Material are dropdowns
  seeded with common garment values; you can also type a custom value. The lists
  grow automatically as new values are used.
- **The desktop Inventory tab** shows each product's detailed attributes and a
  "detailed by / on" stamp, so the office sees the warehouse's work.

## Config

- Server URL and login are stored on the phone (AsyncStorage); change the server
  from the login screen's "Change server".
- Credentials are the same as the desktop app (`ESSA_USER` / `ESSA_PASSWORD`).

## Files
```
mobile-app/
├─ App.js            root: server config, login, the two tabs, product list + detail form
├─ grn.js            Receive tab: GRN list, lines, the size breakdown editor, post + QRs
├─ api.js            every server call the phone makes
├─ ui.js             shared controls (searchable dropdown, badges, buttons, code prompt)
├─ theme.js          colours + stylesheet
├─ package.json      Expo + React Native deps
├─ app.json          Expo project config
└─ babel.config.js
```

No extra dependencies: the breakdown editor and the QR previews are built from
stock React Native. The QRs come from the server as PNG
(`/api/inventory/products/{id}/qr.png`) because `<Image>` can't render the SVG
the web app uses — same payload, same error correction, same code.
