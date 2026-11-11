package my.edu.purplebank;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowInsets;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.TextView;

import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Standalone measured PIN exercise. Never reads the banking session token. */
public final class PinDemoActivity extends Activity {
    private static final int PURPLE = 0xFF7047E8, INK = 0xFF242038, MUTED = 0xFF696174, BG = 0xFFF7F8FC;
    private final PinRhythmCapture capture = new PinRhythmCapture();
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final ArrayList<Button> digitButtons = new ArrayList<>();
    private ScrollView scroll;
    private LinearLayout root, result;
    private TextView progress, error;
    private Spinner models;
    private Button submit;
    private String baseUrl;
    private boolean busy, complete;
    private int generation;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        try {
            baseUrl = BackendUrl.normalise(getIntent().getStringExtra("backend_url"), BuildConfig.DEBUG);
        } catch (Exception e) { finish(); return; }
        buildScreen();
    }
    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private GradientDrawable background(int color) {
        GradientDrawable bg = new GradientDrawable(); bg.setColor(color); bg.setCornerRadius(dp(14)); return bg;
    }
    private LinearLayout column() { LinearLayout v = new LinearLayout(this); v.setOrientation(LinearLayout.VERTICAL); return v; }
    private TextView text(String value, int size, int color, boolean bold) {
        TextView v = new TextView(this); v.setText(value); v.setTextSize(size); v.setTextColor(color);
        v.setLineSpacing(dp(3), 1); if (bold) v.setTypeface(null, Typeface.BOLD); return v;
    }
    private void gap(LinearLayout parent, int height) { parent.addView(new View(this), new LinearLayout.LayoutParams(1, dp(height))); }
    private Button button(LinearLayout parent, String title, int color, View.OnClickListener listener) {
        Button b = new Button(this); b.setText(title); b.setTextSize(13); b.setAllCaps(false); b.setTextColor(Color.WHITE);
        b.setBackgroundTintList(android.content.res.ColorStateList.valueOf(color)); b.setOnClickListener(listener);
        parent.addView(b, new LinearLayout.LayoutParams(-1, dp(52))); gap(parent, 7); return b;
    }
    private void buildScreen() {
        scroll = new ScrollView(this); scroll.setBackgroundColor(BG); scroll.setFillViewport(true);
        root = column(); root.setPadding(dp(22), dp(24), dp(22), dp(28)); scroll.addView(root);
        if (Build.VERSION.SDK_INT >= 30) scroll.setOnApplyWindowInsetsListener((v, insets) -> {
            android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom); return insets;
        }); else scroll.setFitsSystemWindows(true);
        setContentView(scroll);
        root.addView(text("purple bank. / PIN lab", 24, PURPLE, true)); gap(root, 8);
        root.addView(text("PIN RHYTHM PRACTICE · GENERATED REFERENCE", 11, MUTED, true)); gap(root, 18);
        root.addView(text("Same PIN. Different rhythm.", 26, INK, true)); gap(root, 10);
        root.addView(text("Public practice PIN: 246810\nFirst person: use a normal rhythm. Second person: pause about one second between digits. Press and release each digit.", 14, INK, false)); gap(root, 15);
        models = new Spinner(this); models.setContentDescription("Reference model");
        models.setAdapter(new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item, new String[]{"Isolation Forest", "One-Class SVM"}));
        root.addView(models, new LinearLayout.LayoutParams(-1, dp(48))); gap(root, 10);
        progress = text("0 / 6 digits recorded", 20, INK, true); progress.setGravity(android.view.Gravity.CENTER);
        progress.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE); root.addView(progress); gap(root, 14);
        for (int row = 0; row < 4; row++) {
            LinearLayout line = new LinearLayout(this); root.addView(line);
            for (int col = 0; col < 3; col++) {
                int index = row * 3 + col;
                if (index < 9 || index == 10) {
                    char digit = index == 10 ? '0' : (char) ('1' + index);
                    TimingKey key = new TimingKey(digit); digitButtons.add(key); addKey(line, key);
                } else {
                    Button control = new Button(this); control.setAllCaps(false); control.setTextSize(22);
                    control.setTextColor(index == 11 ? Color.WHITE : PURPLE);
                    control.setBackgroundTintList(android.content.res.ColorStateList.valueOf(index == 11 ? PURPLE : 0xFFECE5FF));
                    if (index == 9) {
                        control.setText("↺"); control.setContentDescription("Clear PIN"); control.setOnClickListener(v -> reset(""));
                    } else {
                        control.setText("→"); control.setContentDescription("Check PIN rhythm"); control.setOnClickListener(v -> evaluate()); submit = control;
                    }
                    addKey(line, control);
                }
            }
        }
        error = text("", 12, 0xFFAB3049, false); error.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        root.addView(error); gap(root, 10);
        result = column(); result.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE); root.addView(result); gap(root, 15);
        root.addView(text("Shared generated reference: 700 training and 300 validation sessions; median press 110 ms and gap 200 ms. It is not an enrolled owner profile. A slower owner may be flagged, while another person with a similar rhythm may pass.\n\nOnly two measured timings are used. This practice does not approve sign-in, lock accounts or transfer funds. The app checks the practice PIN locally; the server receives relative timings without digits or identity. This module does not store these timings.", 12, MUTED, false));
        gap(root, 14); root.addView(text("Backend: " + baseUrl, 11, MUTED, false)); gap(root, 14);
        button(root, "Back", 0xFF80788E, v -> finish());
        models.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override public void onItemSelected(AdapterView<?> p, View view, int position, long id) { reset(""); }
            @Override public void onNothingSelected(AdapterView<?> p) { reset(""); }
        });
        updateControls();
    }
    private void addKey(LinearLayout line, Button key) {
        LinearLayout.LayoutParams layout = new LinearLayout.LayoutParams(0, dp(68), 1); layout.setMargins(dp(3), dp(3), dp(3), dp(3));
        line.addView(key, layout);
    }
    private void updateControls() {
        progress.setText(String.format(Locale.ROOT, "%d / 6 digits recorded", capture.count()));
        submit.setEnabled(!busy && !complete && capture.complete()); models.setEnabled(!busy);
        for (Button key : digitButtons) { key.setEnabled(!busy && !complete && !capture.complete()); key.setPressed(false); }
    }
    private void reset(String message) {
        generation++; busy = false; complete = false; capture.reset();
        if (error != null) { error.setText(message); result.removeAllViews(); result.setPadding(0, 0, 0, 0); result.setBackground(null); updateControls(); }
    }
    private void press(char digit, String source, long id, long time) {
        if (busy || complete) return;
        try { capture.press(digit, source, id, time); error.setText(""); }
        catch (IllegalArgumentException e) { reset(e.getMessage()); }
    }
    private void release(long id, long time) {
        if (busy || complete) return;
        try { capture.release(id, time); updateControls(); }
        catch (IllegalArgumentException e) { reset(e.getMessage()); }
    }
    private final class TimingKey extends Button {
        private final char digit;
        private boolean measuredClick;
        TimingKey(char digit) {
            super(PinDemoActivity.this); this.digit = digit; setText(String.valueOf(digit)); setTextSize(23); setTextColor(PURPLE);
            setBackgroundTintList(android.content.res.ColorStateList.valueOf(0xFFECE5FF));
        }
        @Override public boolean onTouchEvent(MotionEvent event) {
            if (!isEnabled()) return false;
            if (event.getPointerCount() != 1 || event.getActionMasked() == MotionEvent.ACTION_CANCEL) {
                reset("Touch was interrupted or overlapped. Enter the PIN again."); setPressed(false);
                getParent().requestDisallowInterceptTouchEvent(false); return true;
            }
            switch (event.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    getParent().requestDisallowInterceptTouchEvent(true);
                    press(digit, "pointer", event.getPointerId(0), event.getEventTime()); setPressed(capture.active()); return true;
                case MotionEvent.ACTION_UP:
                    getParent().requestDisallowInterceptTouchEvent(false);
                    if (event.getX() < 0 || event.getY() < 0 || event.getX() >= getWidth() || event.getY() >= getHeight())
                        reset("Release on the same digit. Enter the PIN again.");
                    else release(event.getPointerId(0), event.getEventTime());
                    setPressed(false); measuredClick = true;
                    try { performClick(); } finally { measuredClick = false; }
                    return true;
                default: return true;
            }
        }
        @Override public boolean performClick() {
            super.performClick();
            if (!measuredClick && !capture.active() && capture.count() == 0 && !busy && !complete)
                error.setText(R.string.pin_unmeasured_click);
            return true;
        }
    }
    @Override public boolean dispatchKeyEvent(KeyEvent event) {
        int code = event.getKeyCode();
        char digit = code >= KeyEvent.KEYCODE_0 && code <= KeyEvent.KEYCODE_9 ? (char) ('0' + code - KeyEvent.KEYCODE_0)
                : code >= KeyEvent.KEYCODE_NUMPAD_0 && code <= KeyEvent.KEYCODE_NUMPAD_9 ? (char) ('0' + code - KeyEvent.KEYCODE_NUMPAD_0) : 0;
        if (digit != 0 && error != null && !(getCurrentFocus() instanceof Spinner) && !event.isCtrlPressed() && !event.isAltPressed() && !event.isMetaPressed()) {
            if (event.getAction() == KeyEvent.ACTION_DOWN && event.getRepeatCount() == 0) press(digit, "keyboard", 1000L + code, event.getEventTime());
            else if (event.getAction() == KeyEvent.ACTION_UP) release(1000L + code, event.getEventTime());
            return true;
        }
        if (code == KeyEvent.KEYCODE_DEL && error != null && event.getAction() == KeyEvent.ACTION_DOWN) { reset(""); return true; }
        return super.dispatchKeyEvent(event);
    }
    private void evaluate() {
        if (busy || complete || !capture.complete()) return;
        if (!capture.matchesDemoPin()) { reset("The practice PIN does not match. Use 246810."); return; }
        final String model = models.getSelectedItemPosition() == 0 ? "isolation_forest" : "one_class_svm";
        final String payload = capture.payload(model);
        final int request = ++generation;
        busy = true; error.setText(R.string.pin_loading); updateControls();
        executor.execute(() -> {
            HttpURLConnection connection = null;
            try {
                connection = (HttpURLConnection) new URL(baseUrl + "/api/demo/pin-rhythm").openConnection();
                connection.setConnectTimeout(12000); connection.setReadTimeout(15000); connection.setInstanceFollowRedirects(false);
                connection.setRequestMethod("POST"); connection.setRequestProperty("Content-Type", "application/json"); connection.setDoOutput(true);
                // Deliberately no session token, PIN digit, user ID or device ID.
                try (OutputStream out = connection.getOutputStream()) { out.write(payload.getBytes(StandardCharsets.UTF_8)); }
                int status = connection.getResponseCode();
                if (status != 200) throw new IllegalStateException("The service rejected the request (" + status + "). Check the service version and try again.");
                ByteArrayOutputStream bytes = new ByteArrayOutputStream();
                try (InputStream in = connection.getInputStream()) {
                    byte[] buffer = new byte[4096]; int n;
                    while ((n = in.read(buffer)) != -1) { if (bytes.size() + n > 65536) throw new IllegalStateException("The service response is too large."); bytes.write(buffer, 0, n); }
                }
                JSONObject response = new JSONObject(new String(bytes.toByteArray(), StandardCharsets.UTF_8));
                runOnUiThread(() -> {
                    if (request != generation || isFinishing() || isDestroyed()) return;
                    try { showResult(response, model); }
                    catch (Exception e) { busy = false; error.setText(R.string.pin_backend_invalid); updateControls(); }
                });
            } catch (Exception e) {
                runOnUiThread(() -> { if (request == generation && !isFinishing() && !isDestroyed()) { busy = false; error.setText(e.getMessage()); updateControls(); } });
            } finally { if (connection != null) connection.disconnect(); }
        });
    }
    private void showResult(JSONObject response, String model) throws Exception {
        if (!response.getString("version").equals("purple-pin-synthetic-v1") || response.getBoolean("identity_verified")
                || !response.getString("account_action").equals("none") || !response.getString("model").equals(model))
            throw new IllegalArgumentException("Unexpected practice response");
        String tier = response.getString("tier");
        if (!(tier.equals("allow") || tier.equals("step_up") || tier.equals("block"))) throw new IllegalArgumentException("Unknown tier");
        JSONObject measured = response.getJSONObject("features_measured"), thresholds = response.getJSONObject("thresholds").getJSONObject(model);
        int color = tier.equals("allow") ? 0xFF177446 : tier.equals("step_up") ? 0xFF8A660D : 0xFFAB3049;
        String title = tier.equals("allow") ? "Rhythm matches the practice reference" : tier.equals("step_up") ? "Rhythm differs · review suggested" : "Rhythm differs greatly · practice alert";
        result.removeAllViews(); result.setPadding(dp(16), dp(16), dp(16), dp(16)); result.setBackground(background(Color.WHITE));
        result.addView(text(title, 20, color, true)); gap(result, 10);
        result.addView(text("The practice PIN is the same. The operator’s identity is not verified.", 13, INK, false)); gap(result, 14);
        result.addView(text(String.format(Locale.US, "Average press: %.0f ms\nAverage gap: %.0f ms", measured.getDouble("mean_dwell_ms"), measured.getDouble("mean_flight_ms")), 18, PURPLE, true)); gap(result, 12);
        result.addView(text(String.format(Locale.US, "Raw score: %.4f\nReview: %.4f · block: %.4f\nThe score is not a fraud probability. No bank account action is taken.", response.getDouble("model_score"), thresholds.getDouble("review"), thresholds.getDouble("block")), 12, MUTED, false)); gap(result, 12);
        button(result, "Try another person or rhythm", PURPLE, v -> reset(""));
        busy = false; complete = true; error.setText(""); capture.reset(); updateControls();
        // Completed input is cleared from memory; show completion separately.
        progress.setText(R.string.pin_evaluated);
        scroll.post(() -> { if (complete) scroll.smoothScrollTo(0, result.getTop()); });
    }
    @Override public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (!hasFocus && !busy && !complete && (capture.active() || capture.count() > 0)) reset("Focus was lost. Enter the PIN again.");
    }
    @Override protected void onStop() { reset("You left the screen. Enter the PIN again."); super.onStop(); }
    @Override protected void onDestroy() { generation++; capture.reset(); executor.shutdownNow(); super.onDestroy(); }
}
