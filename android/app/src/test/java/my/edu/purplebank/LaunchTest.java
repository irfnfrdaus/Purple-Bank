package my.edu.purplebank;

import static org.junit.Assert.*;
import static org.robolectric.Shadows.shadowOf;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.os.Looper;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.TextView;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.annotation.Config;
import org.robolectric.shadows.ShadowAlertDialog;
import org.robolectric.util.ReflectionHelpers;
import org.json.JSONObject;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = {26, 35, 36})
public class LaunchTest {
    private boolean hasText(View view, String wanted) {
        if (view instanceof TextView && ((TextView) view).getText().toString().contains(wanted)) return true;
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++) if (hasText(group.getChildAt(i), wanted)) return true;
        }
        return false;
    }
    private Button button(View view, String wanted) {
        if (view instanceof Button && ((Button) view).getText().toString().equals(wanted)) return (Button) view;
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++) {
                Button result = button(group.getChildAt(i), wanted);
                if (result != null) return result;
            }
        }
        return null;
    }
    @Test public void oldPreferenceTypesDoNotCrashStartup() {
        RuntimeEnvironment.getApplication().getSharedPreferences("purple_fyp", 0).edit()
            .putLong("device_id", 123L).putInt("api_base_url", 7).commit();
        ActivityController<MainActivity> controller = Robolectric.buildActivity(MainActivity.class).setup().visible();
        assertTrue(hasText(controller.get().findViewById(android.R.id.content), "Welcome back"));
        controller.pause().stop().destroy();
    }

    @Test public void opensLoginWithoutBackendAndSurvivesRecreation() {
        ActivityController<MainActivity> controller = Robolectric.buildActivity(MainActivity.class).setup().visible();
        MainActivity activity = controller.get();
        View content = activity.findViewById(android.R.id.content);
        assertTrue(hasText(content, "Welcome back"));
        assertTrue(hasText(content, "Sign in"));
        assertNull(button(content, "Fill public customer demo"));
        assertNotNull(button(content, "Create account"));
        assertNotNull(button(content, "Connection settings"));
        controller.recreate();
        assertTrue(hasText(controller.get().findViewById(android.R.id.content), "Welcome back"));
        controller.pause().stop().destroy();
    }

    @Test public void missingProfileOffersIndependentPinPracticeWithoutCredentials() throws Exception {
        ActivityController<MainActivity> controller = Robolectric.buildActivity(MainActivity.class).setup().visible();
        MainActivity activity = controller.get();
        ReflectionHelpers.setField(activity, "baseUrl", "https://example.test/purplebank/");
        ReflectionHelpers.setField(activity, "token", "private-test-bank-session");
        ReflectionHelpers.callInstanceMethod(activity, "showTransferResult",
            ReflectionHelpers.ClassParameter.from(JSONObject.class, new JSONObject().put("status", "enrollment_required")));
        AlertDialog dialog = ShadowAlertDialog.getLatestAlertDialog();
        String message = ((TextView) dialog.findViewById(android.R.id.message)).getText().toString();
        assertTrue(message.contains("No funds have moved"));
        assertTrue(message.contains("generated reference data"));
        assertTrue(message.contains("246810, not your transaction PIN"));
        assertNotNull(dialog.getButton(AlertDialog.BUTTON_NEGATIVE));
        assertEquals("Set up PIN rhythm", dialog.getButton(AlertDialog.BUTTON_POSITIVE).getText().toString());
        assertEquals("Practice PIN rhythm", dialog.getButton(AlertDialog.BUTTON_NEUTRAL).getText().toString());
        assertNull(shadowOf(activity).getNextStartedActivity());
        dialog.getButton(AlertDialog.BUTTON_NEUTRAL).performClick();
        shadowOf(Looper.getMainLooper()).idle();
        Intent opened = shadowOf(activity).getNextStartedActivity();
        assertNotNull(opened);
        assertEquals(Intent.ACTION_VIEW, opened.getAction());
        assertEquals("https://example.test/purplebank/pin-demo", opened.getDataString());
        assertNull(opened.getExtras());
        assertNull(shadowOf(activity).getNextStartedActivity());
        controller.pause().stop().destroy();
    }

    @Test public void pinPracticeRejectsCredentialsOrQueriesInSavedBackend() {
        ActivityController<MainActivity> controller = Robolectric.buildActivity(MainActivity.class).setup().visible();
        MainActivity activity = controller.get();
        ReflectionHelpers.setField(activity, "baseUrl", "https://user:secret@example.test/purplebank?session=private");
        ReflectionHelpers.callInstanceMethod(activity, "openPinRhythmDemo");
        assertNull(shadowOf(activity).getNextStartedActivity());
        controller.pause().stop().destroy();
    }
    @Test public void pinPolicyResultUsesClearEnglishWithoutMisleadingModelLabel() throws Exception {
        ActivityController<MainActivity> controller = Robolectric.buildActivity(MainActivity.class).setup().visible();
        MainActivity activity = controller.get();
        JSONObject timing = new JSONObject().put("mean_dwell_ms",65).put("mean_flight_ms",304);
        JSONObject details = new JSONObject().put("score_available",false).put("policy_score_available",true).put("pin_timing",timing);
        JSONObject transaction = new JSONObject().put("status","completed").put("amount_minor",200000).put("decision_details",details);
        ReflectionHelpers.callInstanceMethod(activity,"showTransferResult",ReflectionHelpers.ClassParameter.from(JSONObject.class,new JSONObject().put("status","completed").put("transaction",transaction)));
        AlertDialog dialog = ShadowAlertDialog.getLatestAlertDialog();
        String message = ((TextView) dialog.findViewById(android.R.id.message)).getText().toString();
        assertTrue(message.contains("PIN rhythm check"));
        assertTrue(message.contains("Average press: 65 ms"));
        assertTrue(message.contains("Average gap: 304 ms"));
        assertTrue(message.contains("sender was debited and the recipient credited once"));
        assertFalse(message.contains("Model:"));
        assertFalse(message.contains("synthetic"));
        assertFalse(message.contains("ML"));
        assertEquals("Back to account",dialog.getButton(AlertDialog.BUTTON_POSITIVE).getText().toString());
        controller.pause().stop().destroy();
    }

    @Test public void appBiometricErrorsUseEnglishMessages() {
        assertEquals("Verification cancelled.",DeviceBiometric.errorMessage(5));
        assertTrue(DeviceBiometric.errorMessage(7).contains("Too many biometric attempts"));
        assertTrue(DeviceBiometric.errorMessage(3).contains("timed out"));
        assertTrue(DeviceBiometric.errorMessage(999).contains("could not be completed"));
    }

}
