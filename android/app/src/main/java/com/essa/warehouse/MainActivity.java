package com.essa.warehouse;

import android.annotation.SuppressLint;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.view.KeyEvent;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceError;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import androidx.activity.result.ActivityResultLauncher;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;

import com.journeyapps.barcodescanner.ScanContract;
import com.journeyapps.barcodescanner.ScanOptions;

import org.json.JSONObject;

/**
 * The warehouse phone app: a WebView onto the server's own /m screens, with one
 * thing done natively — scanning.
 *
 * Why scanning cannot be left to the page. The web app scans with
 * `navigator.mediaDevices.getUserMedia` and `BarcodeDetector`, and Chromium only
 * exposes those on a secure origin. `http://192.168.x.x:8000` is not one, so in
 * a WebView `mediaDevices` is simply undefined and the camera button does
 * nothing. The usual answers are worse than this one: serving the warehouse over
 * HTTPS means a self-signed certificate and an app taught to accept it, and
 * bundling the pages into the APK means rebuilding it every time the mobile UI
 * changes. Instead the page keeps being served live from the server, and
 * INJECTED_SCANNER_SHIM redirects its scanner to a native one.
 */
public class MainActivity extends AppCompatActivity {

    static final String PREFS = "essa";
    static final String KEY_BASE_URL = "base_url";

    private WebView web;
    private ActivityResultLauncher<ScanOptions> scanLauncher;

    /**
     * Replaces the page's camera scanner with the native one.
     *
     * Two guards in the page read `'BarcodeDetector' in window` and fall back to
     * a text prompt when it is missing, so a stub has to exist for them to take
     * the scanner path at all. `openScanner` is then replaced wholesale. Its
     * no-callback behaviour — resolve the code and open that product — is
     * repeated here against the page's own globals, so both ways of scanning
     * behave exactly as they do in a browser.
     */
    private static final String INJECTED_SCANNER_SHIM =
        "(function(){" +
        "  if (window.__essaNativeScan) return;" +
        "  window.__essaNativeScan = true;" +
        "  if (!('BarcodeDetector' in window)) { window.BarcodeDetector = function(){}; }" +
        "  var pending = null;" +
        "  window.openScanner = function(onCode){ pending = onCode || null; AndroidHost.scan(); };" +
        "  window.closeScanner = function(){};" +
        "  window.__essaScanResult = async function(code){" +
        "    var cb = pending; pending = null;" +
        "    if (!code) return;" +
        "    if (cb) return cb(code);" +
        "    var p = await resolveCode(code);" +
        "    if (p) { S.selected = p; S.detailBack = 'list'; S.screen = 'detail'; render(); }" +
        "    else { toast('Scanned, but no product matches that code'); }" +
        "  };" +
        "})();";

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        String base = baseUrl();
        if (base == null || base.isEmpty()) {
            startActivity(new Intent(this, SetupActivity.class));
            finish();
            return;
        }

        setContentView(R.layout.activity_main);
        web = findViewById(R.id.web);

        scanLauncher = registerForActivityResult(new ScanContract(), result -> {
            String code = result == null ? null : result.getContents();
            if (code == null) return;                       // cancelled
            web.evaluateJavascript(
                "window.__essaScanResult(" + JSONObject.quote(code) + ")", null);
        });

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);                       // the app keeps state in localStorage
        s.setDatabaseEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setMediaPlaybackRequiresUserGesture(false);

        web.addJavascriptInterface(new Bridge(), "AndroidHost");

        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                view.evaluateJavascript(INJECTED_SCANNER_SHIM, null);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                // Only the page itself is worth interrupting for; a failed icon
                // or API poll should not throw up a dialog mid-shift.
                if (req == null || !req.isForMainFrame()) return;
                showUnreachable();
            }
        });

        web.loadUrl(base + "/m");
    }

    /** Exposed to the page as `AndroidHost`. */
    private class Bridge {
        @JavascriptInterface
        public void scan() {
            ScanOptions o = new ScanOptions();
            // ZXing BarcodeFormat names, spelled out rather than taken from
            // ScanOptions' constants: the same five the web app asks for in
            // SCAN_FORMATS, so the phone reads exactly what a browser would.
            o.setDesiredBarcodeFormats(
                "QR_CODE", "CODE_128", "EAN_13", "EAN_8", "CODE_39");
            o.setPrompt(getString(R.string.scan_prompt));
            o.setBeepEnabled(true);
            o.setOrientationLocked(true);
            runOnUiThread(() -> scanLauncher.launch(o));
        }
    }

    private String baseUrl() {
        SharedPreferences p = getSharedPreferences(PREFS, MODE_PRIVATE);
        return p.getString(KEY_BASE_URL, "");
    }

    private void showUnreachable() {
        new AlertDialog.Builder(this)
            .setTitle(R.string.unreachable_title)
            .setMessage(getString(R.string.unreachable_body, baseUrl()))
            .setPositiveButton(R.string.retry, (d, w) -> web.reload())
            .setNegativeButton(R.string.change_server, (d, w) -> {
                startActivity(new Intent(this, SetupActivity.class));
                finish();
            })
            .setCancelable(false)
            .show();
    }

    /** Back walks the page's history before it leaves the app. */
    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK && web != null && web.canGoBack()) {
            web.goBack();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }
}
