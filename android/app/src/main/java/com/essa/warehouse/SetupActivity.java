package com.essa.warehouse;

import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ProgressBar;
import android.widget.TextView;

import androidx.appcompat.app.AppCompatActivity;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Where the phone is told which machine the warehouse runs on.
 *
 * Asked once and remembered, rather than baked into the APK, because the PC's
 * address is a DHCP lease — it changes when the router reboots, and one APK has
 * to serve every phone in the shop. The address is checked before it is saved:
 * a typo caught here is a message on this screen, not an app that opens to a
 * blank page tomorrow morning.
 */
public class SetupActivity extends AppCompatActivity {

    private EditText input;
    private TextView status;
    private ProgressBar spinner;
    private Button save;

    private final Handler ui = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_setup);

        input = findViewById(R.id.host);
        status = findViewById(R.id.status);
        spinner = findViewById(R.id.spinner);
        save = findViewById(R.id.save);

        SharedPreferences p = getSharedPreferences(MainActivity.PREFS, MODE_PRIVATE);
        input.setText(p.getString(MainActivity.KEY_BASE_URL, ""));

        save.setOnClickListener(v -> check(normalise(input.getText().toString())));
    }

    /**
     * "192.168.1.5", "192.168.1.5:8000/m", "http://192.168.1.5/m/" all become
     * "http://192.168.1.5:8000", and "https://essa.example.com/m" becomes
     * "https://essa.example.com".
     *
     * People type what they read off a screen, which is an address and rarely a
     * URL — and if they have seen the app in a browser they will include the
     * `/m` that was in the address bar. Keeping that path would send every
     * request to the wrong place: the check would ask for `/m/api/status`, get a
     * 404, and report a working server as unreachable. Only the host and port
     * mean anything here, so everything after them is dropped.
     *
     * The default port follows the scheme, and that distinction is the whole
     * difference between the two ways this app is run. On the warehouse LAN the
     * address is a bare IP and the server listens on 8000, so a bare host means
     * :8000. A deployed server is https and answers on 443, so appending 8000
     * there would point the app at a port nothing is listening on and report a
     * perfectly good server as unreachable — which is exactly what happened
     * before this distinction existed.
     */
    static String normalise(String raw) {
        String t = raw == null ? "" : raw.trim();
        if (t.isEmpty()) return "";

        String scheme = "";                      // "" = not stated, decide below
        if (t.startsWith("http://")) {
            scheme = "http://";
            t = t.substring(7);
        } else if (t.startsWith("https://")) {
            scheme = "https://";
            t = t.substring(8);
        }

        // Anything after the host:port — a path, a query, a fragment — is noise.
        int cut = t.length();
        for (String mark : new String[]{"/", "?", "#"}) {
            int at = t.indexOf(mark);
            if (at >= 0 && at < cut) cut = at;
        }
        t = t.substring(0, cut).trim();
        if (t.isEmpty()) return "";

        // Nobody types a scheme. What they type is either the LAN address of the
        // PC in the office — an IP, or a machine name — or the address of a
        // deployed server, which is a domain. Guessing from that is the whole
        // job here, because the two want opposite defaults and getting it wrong
        // reports a working server as unreachable.
        String host = t.contains(":") ? t.substring(0, t.indexOf(':')) : t;
        if (scheme.isEmpty()) scheme = isLanHost(host) ? "http://" : "https://";

        // Default port follows the scheme: 8000 is what the warehouse server
        // listens on, while https answers on 443 and so needs nothing added.
        if (!t.contains(":") && scheme.equals("http://")) t = t + ":8000";
        return scheme + t;
    }

    /**
     * Is this the PC in the office rather than a server on the internet?
     *
     * An IPv4 address or a bare machine name is the LAN; anything with a dot and
     * a non-numeric tail is a domain. It is a guess, but a recoverable one —
     * both forms are checked against /api/status before being saved, so a wrong
     * guess shows "not reachable" on this screen rather than failing later, and
     * typing the scheme explicitly always overrides it.
     */
    static boolean isLanHost(String host) {
        if (host.isEmpty()) return true;
        if (host.equals("localhost")) return true;
        if (!host.contains(".")) return true;             // a bare machine name
        // A dotted-quad IP is the office; a domain name is not.
        return host.matches("\\d{1,3}(\\.\\d{1,3}){3}");
    }

    private void check(String base) {
        if (base.isEmpty()) {
            status.setText(R.string.setup_empty);
            return;
        }
        busy(true);
        new Thread(() -> {
            String problem = probe(base);
            ui.post(() -> {
                busy(false);
                if (problem != null) {
                    status.setText(getString(R.string.setup_failed, problem));
                    return;
                }
                getSharedPreferences(MainActivity.PREFS, MODE_PRIVATE)
                    .edit().putString(MainActivity.KEY_BASE_URL, base).apply();
                startActivity(new Intent(this, MainActivity.class));
                finish();
            });
        }).start();
    }

    /** null when the warehouse answered; otherwise why it didn't. */
    private String probe(String base) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(base + "/api/status").openConnection();
            c.setConnectTimeout(4000);
            c.setReadTimeout(4000);
            c.setRequestMethod("GET");
            int code = c.getResponseCode();
            if (code != 200) return "HTTP " + code;
            try (InputStream in = c.getInputStream()) {
                //noinspection ResultOfMethodCallIgnored
                in.read();
            }
            return null;
        } catch (Exception e) {
            return e.getClass().getSimpleName();
        } finally {
            if (c != null) c.disconnect();
        }
    }

    private void busy(boolean on) {
        spinner.setVisibility(on ? View.VISIBLE : View.GONE);
        save.setEnabled(!on);
        if (on) status.setText(R.string.setup_checking);
    }
}
