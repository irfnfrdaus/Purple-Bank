package my.edu.purplebank;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.content.Intent;
import android.net.Uri;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Bundle;
import android.os.SystemClock;
import android.text.Editable;
import android.text.InputType;
import android.text.TextWatcher;
import android.view.View;
import android.view.WindowInsets;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.ByteArrayOutputStream;
import java.math.BigDecimal;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.text.NumberFormat;
import java.util.ArrayList;
import java.util.Locale;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Native Java client; all decisions, locks and ledger writes stay on the server. */
public class MainActivity extends Activity {
    private static final int PURPLE = Color.rgb(112,71,232);
    private static final int INK = Color.rgb(36,32,56);
    private static final int MUTED = Color.rgb(132,125,148);
    private static final int BG = Color.rgb(247,248,252);
    private boolean pinEnabled=false, biometricEnrolled=false, pinRhythmEnrolled=false;
    private android.os.CancellationSignal biometricCancellation;
    private boolean biometricBusy=false;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final ArrayList<Long> intervals = new ArrayList<>();
    private long lastInput;
    private LinearLayout root;
    private SharedPreferences preferences;
    private String baseUrl;
    private String token;
    private String deviceId;
    private String attemptId, attemptFingerprint;
    private JSONObject overview;
    private JSONArray recipients = new JSONArray();
    private boolean registerMode = false;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        preferences = getSharedPreferences("purple_fyp", MODE_PRIVATE);
        deviceId = savedString("device_id", null);
        if (deviceId == null) {
            deviceId = UUID.randomUUID().toString();
            preferences.edit().putString("device_id", deviceId).apply();
        }
        baseUrl = savedString("api_base_url", "https://43.163.87.189/purplebank");
        if(baseUrl.isEmpty())baseUrl="https://43.163.87.189/purplebank";
        loginScreen();
    }

    private String savedString(String key, String fallback) {
        Object value = preferences.getAll().get(key);
        return value instanceof String ? (String) value : fallback;
    }

    private void openPrivateWeb() {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(BuildConfig.WEB_PORTAL_URL)));
        } catch (android.content.ActivityNotFoundException error) {
            toast("Install a browser to open Purple Bank on the web.");
        }
    }

    private void openPinRhythmDemo() {
        try {
            String address = BackendUrl.normalise(baseUrl, BuildConfig.DEBUG) + "/pin-demo";
            // Never put the bank
            // session, gate credentials or transaction PIN in this link.
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(address)));
        } catch (android.content.ActivityNotFoundException error) {
            toast("Install a browser to open PIN practice.");
        } catch (Exception error) {
            toast("Check the service address in Connection settings.");
        }
    }

    private void showEnrollmentHelp() {
        View focus = getCurrentFocus();
        if (focus != null) {
            android.view.inputmethod.InputMethodManager keyboard =
                (android.view.inputmethod.InputMethodManager) getSystemService(INPUT_METHOD_SERVICE);
            if (keyboard != null) keyboard.hideSoftInputFromWindow(focus.getWindowToken(), 0);
            focus.clearFocus();
        }
        new AlertDialog.Builder(this).setTitle("Set up your PIN rhythm first")
            .setMessage("No funds have moved. Your account PIN rhythm reference has not been set up. Select Set up PIN rhythm and enter your existing PIN once at your usual pace.\n\nFor separate practice with generated reference data, use practice PIN 246810, not your transaction PIN. Practice does not approve transfers.")
            .setNegativeButton("Back", (dialog, which) -> refresh())
            .setPositiveButton("Set up PIN rhythm", (dialog, which) -> accountSecurity())
            .setNeutralButton("Practice PIN rhythm", (dialog, which) -> openPinRhythmDemo())
            .show();
    }

    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private GradientDrawable background(int color, int radius) {
        GradientDrawable bg = new GradientDrawable(); bg.setColor(color); bg.setCornerRadius(dp(radius)); return bg;
    }
    private TextView text(String value, int size, int color, boolean bold) {
        TextView view = new TextView(this); view.setText(value); view.setTextSize(size); view.setTextColor(color);
        if (bold) view.setTypeface(null, Typeface.BOLD); view.setLineSpacing(dp(3),1);
        return view;
    }
    private void gap(LinearLayout parent, int size) {
        View space = new View(this); parent.addView(space, new LinearLayout.LayoutParams(1,dp(size)));
    }
    private LinearLayout column() { LinearLayout layout = new LinearLayout(this); layout.setOrientation(LinearLayout.VERTICAL); return layout; }
    private void screen() {
        ScrollView scroll = new ScrollView(this); scroll.setBackgroundColor(BG); scroll.setFillViewport(true);
        root = column(); root.setPadding(dp(23),dp(25),dp(23),dp(30)); scroll.addView(root);
        if (Build.VERSION.SDK_INT >= 30) {
            scroll.setOnApplyWindowInsetsListener((v,insets)->{
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                v.setPadding(bars.left,bars.top,bars.right,bars.bottom); return insets;
            });
        } else scroll.setFitsSystemWindows(true);
        setContentView(scroll);
        root.addView(text("purple bank.",27,PURPLE,true)); gap(root,6);
        root.addView(text("Research prototype · Simulated funds",10,MUTED,true)); gap(root,27);
    }
    private LinearLayout card(LinearLayout parent) {
        LinearLayout view = column(); view.setPadding(dp(20),dp(20),dp(20),dp(20)); view.setBackground(background(Color.WHITE,16));
        parent.addView(view,new LinearLayout.LayoutParams(-1,-2)); gap(parent,18); return view;
    }
    private EditText input(LinearLayout parent, String label, String hint, int type) {
        parent.addView(text(label,12,MUTED,true)); gap(parent,7);
        EditText view = new EditText(this); view.setTextSize(14); view.setTextColor(INK); view.setHint(hint); view.setSingleLine(true);
        view.setInputType(type); view.setPadding(dp(12),dp(10),dp(12),dp(10)); view.setBackground(background(BG,8));
        parent.addView(view,new LinearLayout.LayoutParams(-1,dp(49))); gap(parent,17); return view;
    }
    private Button button(LinearLayout parent,String label,int color,View.OnClickListener listener) {
        Button view = new Button(this); view.setText(label); view.setTextSize(13); view.setAllCaps(false); view.setTextColor(Color.WHITE);
        view.setBackgroundTintList(android.content.res.ColorStateList.valueOf(color)); view.setOnClickListener(listener);
        parent.addView(view,new LinearLayout.LayoutParams(-1,dp(51))); gap(parent,7); return view;
    }
    private void accessSettings(Runnable after) {
        LinearLayout form=column();form.setPadding(dp(24),dp(10),dp(24),0);
        EditText server=input(form,"Service address","https://…/purplebank",InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);server.setText(baseUrl);
        AlertDialog dialog=new AlertDialog.Builder(this).setTitle("Purple Bank connection").setView(form).setNegativeButton("Cancel",null).setPositiveButton("Save",null).create();
        dialog.setOnShowListener(d->dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v->{try{
            baseUrl=BackendUrl.normalise(server.getText().toString(),BuildConfig.DEBUG);
            preferences.edit().putString("api_base_url",baseUrl).apply();dialog.dismiss();if(after!=null)after.run();
        }catch(Exception e){toast(e.getMessage());}}));dialog.show();
    }

    private void loginScreen() {
        screen();root.addView(text("Welcome back",30,INK,true));gap(root,10);
        root.addView(text("Sign in to manage your account and transfers.",14,MUTED,false));gap(root,22);
        LinearLayout form=card(root);form.addView(text(registerMode?"Create account":"Sign in",23,INK,true));gap(form,20);
        EditText name=registerMode?input(form,"Full name","Account name",InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_WORDS):null;
        EditText email=registerMode?input(form,"Email","you@example.com",InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS):null;
        EditText username=input(form,"Username",registerMode?"3–32 letters, numbers, _ or .":"Username or email",InputType.TYPE_CLASS_TEXT);
        EditText password=input(form,"Password",registerMode?"At least 12 characters":"Enter your password",InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);password.setSaveEnabled(false);
        TimedPinPad pin=registerMode?new TimedPinPad(this,"Set your transaction PIN at your usual pace"):null;
        if(pin!=null)form.addView(pin);
        EditText confirm=registerMode?input(form,"Confirm transaction PIN","The same 6 digits",InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD):null;
        if(pin!=null){confirm.setSaveEnabled(false);}
        TextView error=text("",12,0xFFC13A53,false);form.addView(error);
        Button signIn=button(form,registerMode?"Create account":"Sign in",PURPLE,null);
        signIn.setOnClickListener(v->{
            final boolean creating=registerMode;
            String identifier=username.getText().toString().trim().toLowerCase(Locale.ROOT), secret=password.getText().toString();
            if(identifier.isEmpty() || secret.isEmpty()){error.setText("Enter your username and password.");return;}
            try {
                JSONObject body=new JSONObject().put("email",creating?email.getText().toString().trim():identifier).put("password",secret).put("device_id",deviceId);
                if(creating){
                    if(!identifier.matches("[a-z0-9_.]{3,32}"))throw new Exception("Username: 3–32 letters, numbers, _ or .");
                    if(secret.length()<12)throw new Exception("Use at least 12 password characters.");
                    if(!pin.pin().equals(confirm.getText().toString()))throw new Exception("Enter and confirm the same 6-digit PIN.");
                    body.put("full_name",name.getText().toString().trim()).put("username",identifier).put("pin",pin.pin()).put("pin_timing",pin.timing());
                }
                signIn.setEnabled(false);error.setText(R.string.loading_connect);
                call(creating?"/auth/register":"/auth/login",body,result->{
                    body.remove("pin");body.remove("password");password.setText("");
                    if(creating){registerMode=false;loginScreen();toast("Account created. Sign in on this device.");}
                    else if(!result.getJSONObject("user").getString("role").equals("customer")){
                        signIn.setEnabled(true);error.setText("Administrator accounts use the web security console.");
                    }else{token=result.getString("token");refresh();}
                },e->{body.remove("pin");body.remove("password");signIn.setEnabled(true);error.setText(e.getMessage());});
            }catch(Exception e){error.setText(e.getMessage());}
        });
        button(form,registerMode?"Back to sign in":"Create account",0xFF80788E,v->{registerMode=!registerMode;loginScreen();});
        button(root,"Connection settings",0xFF9282B8,v->accessSettings(null));
        root.addView(text("Version "+BuildConfig.VERSION_NAME+" · Research prototype with simulated funds",11,MUTED,false));
    }

    private void accountSecurity() {
        screen();root.addView(text("Account security",25,INK,true));gap(root,20);
        if(pinEnabled){root.addView(text("Your transaction PIN is set up and required for every transfer. PIN recovery requires a separate verified process.",14,MUTED,false));}
        else {
            LinearLayout form=card(root);
            EditText username=input(form,"New username","3–32 letters, numbers, _ or .",InputType.TYPE_CLASS_TEXT);
            EditText password=input(form,"Current password","Confirm your account password",InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);password.setSaveEnabled(false);
            TimedPinPad pin=new TimedPinPad(this,"New transaction PIN · use your usual pace");form.addView(pin);
            EditText confirm=input(form,"Confirm PIN","6 digits",InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD);confirm.setSaveEnabled(false);
            button(form,"Set up PIN",PURPLE,v->{try{
                if(!pin.pin().equals(confirm.getText().toString()))throw new Exception("The PINs do not match.");
                JSONObject body=new JSONObject().put("username",username.getText().toString().trim()).put("password",password.getText().toString()).put("pin",pin.pin()).put("pin_timing",pin.timing());
                call("/security/setup",body,r->{body.remove("pin");body.remove("password");refresh();},e->{body.remove("pin");body.remove("password");showError(e);});
            }catch(Exception e){showError(e);}});
        }
        if(pinEnabled){
            root.addView(text(pinRhythmEnrolled?"Your PIN rhythm reference is set up for transfer checks.":"Set up the rhythm of your existing PIN once to enable transfers.",14,MUTED,false));
            if(!pinRhythmEnrolled)button(root,"Set up PIN rhythm",PURPLE,v->enrolPinRhythm());
            root.addView(text(biometricEnrolled?"Phone biometrics are set up for this device.":"Set up phone biometrics to verify transfers that need an additional check.",14,MUTED,false));gap(root,12);
            root.addView(text("Face or fingerprint availability depends on your phone’s supported strong biometrics. No face or fingerprint images are sent to the server. Android 11 or later is required.",12,MUTED,false));
            if(!biometricEnrolled)button(root,"Set up phone biometrics",PURPLE,v->enrolBiometric());
        }
        button(root,"Back",0xFF80788E,v->refresh());
    }

    private void enrolPinRhythm(){
        LinearLayout form=column();form.setPadding(dp(20),dp(8),dp(20),0);
        EditText password=input(form,"Account password","Confirm PIN rhythm setup",InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);password.setSaveEnabled(false);
        TimedPinPad pad=new TimedPinPad(this,"Enter your existing PIN at your usual pace");form.addView(pad);
        TextView error=text("",12,0xFFC13A53,false);form.addView(error);
        form.addView(text("These press timings become your reference. Generated timing variations support the check; the rhythm alone does not verify identity.",12,MUTED,false));
        ScrollView scroll=new ScrollView(this);scroll.addView(form);
        AlertDialog dialog=new AlertDialog.Builder(this).setTitle("Set up PIN rhythm").setView(scroll).setNegativeButton("Cancel",null).setPositiveButton("Set up",null).create();
        dialog.setOnShowListener(d->dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v->{try{
            JSONObject body=new JSONObject().put("password",password.getText().toString()).put("pin",pad.pin()).put("pin_timing",pad.timing());
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(false);
            call("/security/pin-rhythm/enrolment",body,r->{body.remove("password");body.remove("pin");dialog.dismiss();refresh();},e->{body.remove("password");body.remove("pin");dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(true);pad.clear();error.setText(e.getMessage());});
        }catch(Exception e){error.setText(e.getMessage());}}));dialog.show();
    }

    private void enrolBiometric(){
        if(!DeviceBiometric.supported(this)){toast("First set up strong biometrics in your phone settings. Android 11 or later is required.");return;}
        LinearLayout form=column();form.setPadding(dp(24),dp(12),dp(24),0);
        EditText password=input(form,"Account password","Confirm setup",InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);password.setSaveEnabled(false);
        EditText pin=input(form,"Transaction PIN","6 digits",InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD);pin.setSaveEnabled(false);
        new AlertDialog.Builder(this).setTitle("Set up phone biometrics").setView(form).setNegativeButton("Cancel",null).setPositiveButton("Continue",(d,w)->{
            try {
                String user=overview.getJSONObject("user").getString("id");
                String key=DeviceBiometric.publicKey(baseUrl,user);
                JSONObject body=new JSONObject().put("password",password.getText().toString()).put("pin",pin.getText().toString()).put("public_key",key);
                password.setText("");pin.setText("");
                call("/security/biometric/enrolment",body,challenge->{body.remove("password");body.remove("pin");signBiometric(challenge,null);},e->{body.remove("password");body.remove("pin");showError(e);});
            }catch(Exception e){showError(e);}
        }).show();
    }

    private void signBiometric(JSONObject challenge,String transferChallenge){
        if(biometricBusy)return;
        biometricBusy=true;
        try {
            String id=challenge.getString("challenge_id"), user=overview.getJSONObject("user").getString("id");
            biometricCancellation=DeviceBiometric.sign(this,baseUrl,user,challenge.getString("message"),new DeviceBiometric.Result(){
                private boolean finished=false;
                @Override public void signed(String signature){
                    if(finished)return;finished=true;biometricBusy=false;biometricCancellation=null;
                    try{call("/security/biometric/challenges/"+id+"/verify",new JSONObject().put("signature",signature),result->{
                        if(transferChallenge==null){toast("Phone biometrics are set up.");refresh();}else showTransferResult(result);
                    },MainActivity.this::showError);}catch(Exception e){showError(e);}
                }
                @Override public void failed(String message){
                    if(finished)return;finished=true;biometricBusy=false;biometricCancellation=null;
                    if(transferChallenge!=null)cancelBiometricTransfer(transferChallenge);else if(!isFinishing()&&!isDestroyed())toast(message);
                }
            });
        }catch(Exception e){biometricBusy=false;if(transferChallenge!=null)cancelBiometricTransfer(transferChallenge);showError(e);}
    }

    private void cancelBiometricTransfer(String challenge){
        call("/step-up/"+challenge+"/cancel",new JSONObject(),result->{if(!isFinishing()&&!isDestroyed())showTransferResult(result);},this::showError);
    }

    private String money(long minor) {
        NumberFormat number = NumberFormat.getCurrencyInstance(new Locale("en","MY"));
        return number.format(BigDecimal.valueOf(minor,2));
    }
    private void refresh() {
        call("/overview",null,result->{overview=result;call("/security/settings",null,settings->{pinEnabled=settings.getBoolean("pin_enabled");biometricEnrolled=settings.getBoolean("biometric_enrolled");pinRhythmEnrolled=settings.getBoolean("pin_rhythm_enrolled");call("/recipients",null,r->{recipients=r.getJSONArray("items");dashboard();},this::showError);},this::showError);},this::showError);
    }
    private void dashboard() throws Exception {
        screen(); JSONObject user=overview.getJSONObject("user"); boolean locked=user.getString("status").equals("locked");
        root.addView(text("Hello, "+user.getString("full_name").split(" ")[0]+" 👋",26,INK,true));gap(root,8);
        root.addView(text("Your money, with a watchful second layer.",13,MUTED,false));gap(root,21);
        LinearLayout balance=card(root);balance.setBackground(background(PURPLE,16));
        balance.addView(text("AVAILABLE BALANCE",10,0xFFDACEFF,true));gap(balance,13);
        balance.addView(text(money(user.getLong("balance_minor")),32,Color.WHITE,true));gap(balance,16);
        balance.addView(text(user.getString("account_no"),12,0xFFE0D6FF,false));
        LinearLayout status=card(root);status.addView(text(locked?"Account locked — review required":"Protection checks active",18,locked?0xFFC13A53:0xFF13846B,true));gap(status,9);
        status.addView(text(locked?"Transfers are paused. A web administrator must review and unlock the account. Blocked transfers do not change your balance.":"Your PIN rhythm is compared with your registered reference. Normal transfers proceed, medium risk requires biometrics, and high risk blocks the transfer and locks the account.",12,MUTED,false));
        button(root,"Transfer money",PURPLE,v->{try{transferScreen();}catch(Exception e){showError(e);}}).setEnabled(!locked);
        button(root,"Account security",0xFF8070AB,v->accountSecurity());
        gap(root,16); root.addView(text("Recent activity",19,INK,true));gap(root,15);
        JSONArray history=overview.getJSONArray("transactions");
        if(history.length()==0)root.addView(text("No transfers yet.",13,MUTED,false));
        for(int i=0;i<Math.min(10,history.length());i++) {
            JSONObject t=history.getJSONObject(i);LinearLayout row=card(root);
            row.addView(text(t.getString("recipient_name"),15,INK,true));gap(row,5);
            String transferStatus=t.getString("status"); boolean blocked=transferStatus.equals("blocked");
            row.addView(text(money(t.getLong("amount_minor"))+" · "+statusLabel(transferStatus),14,blocked?0xFFC13A53:(transferStatus.equals("completed")?0xFF13846B:PURPLE),true));
            gap(row,5);row.addView(text("Reference "+t.getString("id").substring(0,8),10,MUTED,false));
        }
        gap(root,20);button(root,"Refresh",0xFF9282B8,v->refresh());
        button(root,"Sign out",0xFF80788E,v->call("/auth/logout",new JSONObject(),r->{token=null;loginScreen();},e->{token=null;loginScreen();}));
    }

    private void transferScreen() throws Exception {
        if(!pinEnabled || !pinRhythmEnrolled){accountSecurity();toast("Set up your PIN and PIN rhythm first.");return;}
        screen();root.addView(text("Move money.\nKeep control.",29,INK,true));gap(root,23);
        LinearLayout form=card(root);form.addView(text("New transfer",18,INK,true));gap(form,21);
        form.addView(text("Recipient",12,MUTED,true));gap(form,8);
        Spinner payee=new Spinner(this);ArrayList<String> names=new ArrayList<>();
        for(int i=0;i<recipients.length();i++)names.add(recipients.getJSONObject(i).getString("full_name"));
        ArrayAdapter<String> adapter=new ArrayAdapter<>(this,android.R.layout.simple_spinner_dropdown_item,names);payee.setAdapter(adapter);form.addView(payee);gap(form,20);
        EditText amount=input(form,"Amount (MYR)","250.00",InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_FLAG_DECIMAL);
        intervals.clear();lastInput=0;
        amount.addTextChangedListener(new TextWatcher(){
            @Override public void beforeTextChanged(CharSequence s,int start,int count,int after){}
            @Override public void onTextChanged(CharSequence s,int start,int before,int count){
                long tick=SystemClock.elapsedRealtime();if(lastInput>0 && tick-lastInput>=10 && tick-lastInput<=5000)intervals.add(tick-lastInput);
                if(intervals.size()>100)intervals.remove(0);lastInput=tick;
            }
            @Override public void afterTextChanged(Editable e){}
        });
        TextView error=text("",12,0xFFC13A53,false);form.addView(error);
        Button submit=button(form,"Review & send",PURPLE,null);
        submit.setOnClickListener(v->{
            try {
                String raw=amount.getText().toString().trim();if(!raw.matches("[0-9]+(\\.[0-9]{1,2})?"))throw new Exception("Enter an amount with up to two decimal places.");
                int minor=new BigDecimal(raw).movePointRight(2).intValueExact();
                if(minor<1 || minor>10000000)throw new Exception("Use an amount between RM 0.01 and RM 100,000.");
                JSONObject recipient=recipients.getJSONObject(payee.getSelectedItemPosition());
                JSONObject signals=new JSONObject().put("samples",intervals.size());
                if(!intervals.isEmpty()){double total=0;for(long interval:intervals)total+=interval;signals.put("typing_interval_ms",total/intervals.size());}
                String fingerprint=recipient.getString("id")+":"+minor;
                if(attemptId==null || !fingerprint.equals(attemptFingerprint)){attemptId=UUID.randomUUID().toString();attemptFingerprint=fingerprint;}
                JSONObject body=new JSONObject().put("recipient_id",recipient.getString("id")).put("amount_minor",minor).put("request_id",attemptId).put("telemetry",signals);
                TimedPinPad enteredPin=new TimedPinPad(this,"Enter your PIN at your usual pace");
                AlertDialog dialog=new AlertDialog.Builder(this).setTitle("Confirm transfer").setView(enteredPin)
                    .setMessage(money(minor)+" to "+recipient.getString("full_name")+". Your rhythm will be compared with your registered timings.")
                    .setNegativeButton("Cancel",null).setPositiveButton("Send",null).create();
                dialog.setOnShowListener(d->dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(control->{
                    try {
                        body.put("pin",enteredPin.pin()).put("pin_timing",enteredPin.timing());
                        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(false);submit.setEnabled(false);error.setText(R.string.loading_risk);
                        call("/transfers",body,result->{body.remove("pin");dialog.dismiss();attemptId=null;showTransferResult(result);},e->{body.remove("pin");enteredPin.clear();dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(true);submit.setEnabled(true);error.setText(e.getMessage());toast(e.getMessage());});
                    }catch(Exception e){toast(e.getMessage());}
                }));dialog.show();
            }catch(Exception e){error.setText(e.getMessage());}
        });
        gap(root,12);root.addView(text("Enter your transaction PIN before sending. Normal transfers proceed. Medium risk holds the funds for biometrics or administrator review. High risk blocks the transfer and locks the account. Set up your PIN rhythm once in Account security.",11,MUTED,false));
        gap(root,18);button(root,"Back to overview",0xFF80788E,v->refresh());
    }

    private static String rawScore(double value) {return Double.isNaN(value)?"Not available":String.format(Locale.US,"%.4f",value);}
    private static String percentile(double value) {return Double.isNaN(value)?"Not available":String.format(Locale.US,"%.1f / 100",value*100);}
    private static boolean scoreAvailable(JSONObject record) {
        JSONObject details=record.optJSONObject("decision_details");
        double raw=record.optDouble("model_score",Double.NaN), rank=record.optDouble("risk_score",Double.NaN);
        return details!=null && details.optBoolean("score_available",false)
            && !Double.isNaN(raw) && !Double.isInfinite(raw) && !Double.isNaN(rank) && !Double.isInfinite(rank);
    }
    private static String scoreSummary(JSONObject record,String separator) {
        JSONObject details=record.optJSONObject("decision_details");
        if(details!=null && details.optBoolean("policy_score_available",false)){
            JSONObject timing=details.optJSONObject("pin_timing");
            return "PIN rhythm check"+(timing==null?"":"\nAverage press: "+Math.round(timing.optDouble("mean_dwell_ms"))+" ms · Average gap: "+Math.round(timing.optDouble("mean_flight_ms"))+" ms");
        }
        if(!scoreAvailable(record))return "Risk assessment: Not scored";
        return "Validation percentile: "+percentile(record.optDouble("risk_score",Double.NaN))
            +separator+"Raw anomaly score: "+rawScore(record.optDouble("model_score",Double.NaN));
    }
    private static String metric(JSONObject object,String name) {
        double value=object.optDouble(name,Double.NaN);
        if(Double.isNaN(value))return "Not available";
        return name.equals("roc_auc")?String.format(Locale.US,"%.4f",value):String.format(Locale.US,"%.2f%%",value*100);
    }
    private static String statusLabel(String status) {
        switch(status){
            case "completed":return "COMPLETED";
            case "blocked":return "BLOCKED";
            case "step_up_required":return "VERIFICATION REQUIRED";
            case "enrollment_required":return "SETUP REQUIRED";
            case "cancelled":return "CANCELLED";
            default:return status.toUpperCase(Locale.ROOT).replace('_',' ');
        }
    }
    private void showTransferResult(JSONObject result) throws Exception {
        JSONObject t=result.optJSONObject("transaction");
        String status=result.optString("status",t==null?"":t.optString("status"));
        if(status.isEmpty()&&t!=null)status=t.optString("status");
        if(status.equals("step_up_required")) {
            String challenge=result.optString("challenge_id",t==null?"":t.optString("challenge_id"));
            if(challenge.isEmpty())throw new Exception("The transfer is held, but its verification reference is missing. No transfer was approved.");
            new AlertDialog.Builder(this).setTitle("Transfer on hold")
                .setMessage("Your balance has not been debited. Verify with phone biometrics or wait for administrator review. If biometrics are not set up, cancel this transfer and set them up in Account security.")
                .setCancelable(false)
                .setNegativeButton("Cancel transfer",(d,w)->cancelBiometricTransfer(challenge))
                .setNeutralButton("Administrator review",(d,w)->refresh())
                .setPositiveButton("Verify with biometrics",(d,w)->{
                    call("/step-up/"+challenge+"/biometric",new JSONObject(),proof->signBiometric(proof,challenge),this::showError);
                }).show();return;
        }
        if(status.equals("enrollment_required")) {
            showEnrollmentHelp();return;
        }
        if(t==null)throw new Exception(result.optString("message","The transfer response is incomplete. Check your history before trying again."));
        if(!status.equals("completed") && !status.equals("blocked") && !status.equals("cancelled")) {
            new AlertDialog.Builder(this).setTitle("Transfer not confirmed")
                .setMessage("Server status: "+statusLabel(status)+". Check your history or wait for review before trying again.")
                .setPositiveButton("View account",(d,w)->refresh()).show();return;
        }
        boolean blocked=status.equals("blocked"), cancelled=status.equals("cancelled");
        String title=blocked?"Transfer blocked. Balance unchanged.":cancelled?"Transfer cancelled":"Transfer complete";
        String note=blocked?"No funds were transferred. Your account is locked pending administrator review.":cancelled?"Verification was not approved. No funds were transferred.":"The sender was debited and the recipient credited once.";
        String assessment=scoreSummary(t,"\n");
        if(scoreAvailable(t))assessment="Risk model: "+t.optString("model_name","See activity details")+"\n"+assessment;
        new AlertDialog.Builder(this).setTitle(title)
            .setMessage(money(t.optLong("amount_minor",0))+"\n"+assessment+"\n\n"+note+(scoreAvailable(t)?"\n\nA validation percentile is not a fraud probability.":""))
            .setCancelable(false).setPositiveButton("Back to account",(d,w)->refresh()).show();
    }
    private void toast(String message){Toast.makeText(this,message,Toast.LENGTH_LONG).show();}
    private void showError(Exception error) {
        if(error instanceof ApiError && ((ApiError)error).status==401){token=null;loginScreen();}
        new AlertDialog.Builder(this).setTitle("Unable to complete request").setMessage(error.getMessage()).setPositiveButton("OK",null).show();
    }
    interface Success { void accept(JSONObject result) throws Exception; }
    interface Failure { void accept(Exception error); }
    static class ApiError extends Exception { final int status; ApiError(int status,String message){super(message);this.status=status;} }
    private void call(String path,JSONObject body,Success success,Failure failure) {
        if(executor.isShutdown())return;
        final String endpoint=baseUrl+"/api"+path, session=token;
        executor.execute(()->{
            HttpURLConnection connection=null;
            try {
                connection=(HttpURLConnection)new URL(endpoint).openConnection();
                connection.setConnectTimeout(12000);connection.setReadTimeout(15000);connection.setInstanceFollowRedirects(false);
                connection.setRequestMethod(body==null?"GET":"POST");
                if(session!=null)connection.setRequestProperty("X-Purple-Session",session);
                if(body!=null){connection.setRequestProperty("Content-Type","application/json");connection.setDoOutput(true);
                    try(OutputStream out=connection.getOutputStream()){out.write(body.toString().getBytes(StandardCharsets.UTF_8));}}
                int code=connection.getResponseCode();String payload;
                try(InputStream in=code<400?connection.getInputStream():connection.getErrorStream()) {
                    if(in==null)payload="{}";
                    else {
                        ByteArrayOutputStream bytes=new ByteArrayOutputStream();byte[] buffer=new byte[4096];int length;
                        while((length=in.read(buffer))!=-1)bytes.write(buffer,0,length);
                        payload=new String(bytes.toByteArray(),StandardCharsets.UTF_8);
                    }
                }
                String trimmed = payload.trim();
                if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) {
                    String message = code == 401 || code == 403
                        ? "Your session is invalid or access was denied. Sign in again."
                        : "The service address did not return the Purple Bank API (HTTP " + code + ").";
                    throw new ApiError(code, message);
                }
                JSONObject result=trimmed.startsWith("[")?new JSONObject().put("items",new JSONArray(trimmed)):new JSONObject(trimmed);
                if(code<200 || code>=300)throw new ApiError(code,result.optString("detail","Server returned "+code));
                runOnUiThread(()->{if(!isFinishing()&&!isDestroyed())try{success.accept(result);}catch(Exception e){failure.accept(e);}});
            } catch(Exception error){runOnUiThread(()->{if(!isFinishing()&&!isDestroyed())failure.accept(error);});}
            finally{if(connection!=null)connection.disconnect();}
        });
    }
    @Override protected void onStop(){
        if(biometricCancellation!=null)biometricCancellation.cancel();
        super.onStop();
    }
    @Override protected void onDestroy(){super.onDestroy();executor.shutdownNow();token=null;}
}
