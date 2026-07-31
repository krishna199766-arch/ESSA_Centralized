# ESSA Warehouse — mobile app (React Native / Expo)

A phone app for the warehouse team to **physically detail each product** that has
arrived into inventory from a vendor invoice. The employee picks a product (search
or barcode), records what they see — Color, Size, Pattern, Fit, Type, Material,
Design No, MRP, Sale price, Margin % — and saves. It writes straight into the
**same ESSA database** over your WiFi (via the existing backend API).

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
   sign in (default `admin` / `essa@123`) → start detailing products.

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

## How it works

- **Products come from invoices.** They're created when you post a GRN in the
  desktop app. This mobile app never creates products — it enriches the existing
  ones, so the employee always selects from what the vendor invoices produced.
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
├─ App.js            the whole app (server config, login, list, detail form)
├─ package.json      Expo + React Native deps
├─ app.json          Expo project config
└─ babel.config.js
```
