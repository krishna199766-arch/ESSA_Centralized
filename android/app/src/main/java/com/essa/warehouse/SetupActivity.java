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
     * "http://192.168.1.5:8000".
     *
     * People type what they read off a screen, which is an address and rarely a
     * URL — and if they have seen the app in a browser they will include the
     * `/m` that was in the address bar. Keeping that path would send every
     * request to the wrong place: the check would ask for `/m/api/status`, get a
     * 404, and report a working server as unreachable. Only the host and port
     * mean anything here, so everything after them is dropped.
     */
    static String normalise(String raw) {
        String t = raw == null ? "" : raw.trim();
        if (t.isEmpty()) return "";

        String scheme = "http://";
        if (t.startsWith("http://")) {
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

        // A bare host gets the port the warehouse actually listens on.
        if (!t.contains(":")) t = t + ":8000";
        return scheme + t;
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
