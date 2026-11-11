package my.edu.purplebank;

import android.annotation.TargetApi;
import android.app.Activity;
import android.hardware.biometrics.BiometricManager;
import android.hardware.biometrics.BiometricPrompt;
import android.os.Build;
import android.os.CancellationSignal;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.nio.charset.StandardCharsets;
import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.PrivateKey;
import java.security.Signature;
import java.security.spec.ECGenParameterSpec;

/** Auth-per-use device key. No face/fingerprint images or templates leave Android. */
@TargetApi(30)
final class DeviceBiometric {
    interface Result { void signed(String signature); void failed(String message); }
    private DeviceBiometric() {}
    static boolean supported(Activity activity) {
        if(Build.VERSION.SDK_INT<30)return false;
        BiometricManager manager=activity.getSystemService(BiometricManager.class);
        return manager!=null && manager.canAuthenticate(BiometricManager.Authenticators.BIOMETRIC_STRONG)==BiometricManager.BIOMETRIC_SUCCESS;
    }
    private static String alias(String server,String user) throws Exception {
        byte[] digest=MessageDigest.getInstance("SHA-256").digest((server+"\n"+user).getBytes(StandardCharsets.UTF_8));
        return "purple-biometric-"+Base64.encodeToString(digest,Base64.NO_WRAP|Base64.URL_SAFE);
    }
    static String publicKey(String server,String user) throws Exception {
        String name=alias(server,user);KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);
        if(!store.containsAlias(name)){
            KeyPairGenerator generator=KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC,"AndroidKeyStore");
            generator.initialize(new KeyGenParameterSpec.Builder(name,KeyProperties.PURPOSE_SIGN)
                .setAlgorithmParameterSpec(new ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256).setUserAuthenticationRequired(true)
                .setUserAuthenticationParameters(0,KeyProperties.AUTH_BIOMETRIC_STRONG)
                .setInvalidatedByBiometricEnrollment(true).build());
            generator.generateKeyPair();store.load(null);
        }
        return Base64.encodeToString(store.getCertificate(name).getPublicKey().getEncoded(),Base64.NO_WRAP);
    }
    static String errorMessage(int code) {
        switch(code){
            case BiometricPrompt.BIOMETRIC_ERROR_CANCELED:
            case BiometricPrompt.BIOMETRIC_ERROR_USER_CANCELED:return "Verification cancelled.";
            case BiometricPrompt.BIOMETRIC_ERROR_NO_BIOMETRICS:
            case BiometricPrompt.BIOMETRIC_ERROR_HW_NOT_PRESENT:return "Strong biometrics are not set up. Enrol them in your phone settings.";
            case BiometricPrompt.BIOMETRIC_ERROR_HW_UNAVAILABLE:return "Biometrics are temporarily unavailable. Try again.";
            case BiometricPrompt.BIOMETRIC_ERROR_TIMEOUT:return "Biometric verification timed out. Try again.";
            case BiometricPrompt.BIOMETRIC_ERROR_LOCKOUT:
            case BiometricPrompt.BIOMETRIC_ERROR_LOCKOUT_PERMANENT:return "Too many biometric attempts. Unlock your phone and try again later.";
            case BiometricPrompt.BIOMETRIC_ERROR_SECURITY_UPDATE_REQUIRED:return "A phone security update is required to use biometrics.";
            case BiometricPrompt.BIOMETRIC_ERROR_UNABLE_TO_PROCESS:return "The biometric reading could not be processed. Try again.";
            default:return "Biometric verification could not be completed. Try again or request administrator review.";
        }
    }

    static CancellationSignal sign(Activity activity,String server,String user,String message,Result result) throws Exception {
        if(!supported(activity))throw new Exception("Strong biometrics are unavailable. Set up phone biometrics or request administrator review.");
        KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);
        // Never regenerate a missing/invalidated signing key during a transfer.
        PrivateKey key=(PrivateKey)store.getKey(alias(server,user),null);
        if(key==null)throw new Exception("The device key is unavailable. Recovery and setup must be verified.");
        Signature signature=Signature.getInstance("SHA256withECDSA");signature.initSign(key);
        CancellationSignal cancel=new CancellationSignal();
        BiometricPrompt prompt=new BiometricPrompt.Builder(activity).setTitle("Verify Purple Bank")
            .setSubtitle("Use face or fingerprint supported by your phone")
            .setDescription("This verification securely confirms the server request for your account.")
            .setAllowedAuthenticators(BiometricManager.Authenticators.BIOMETRIC_STRONG)
            .setNegativeButton("Cancel",activity.getMainExecutor(),(d,w)->{cancel.cancel();result.failed("Verification cancelled.");}).build();
        prompt.authenticate(new BiometricPrompt.CryptoObject(signature),cancel,activity.getMainExecutor(),new BiometricPrompt.AuthenticationCallback(){
            @Override public void onAuthenticationSucceeded(BiometricPrompt.AuthenticationResult auth){
                try {
                    Signature unlocked=auth.getCryptoObject()==null?null:auth.getCryptoObject().getSignature();
                    if(unlocked==null)throw new Exception("No signing operation was verified.");
                    unlocked.update(message.getBytes(StandardCharsets.UTF_8));
                    result.signed(Base64.encodeToString(unlocked.sign(),Base64.NO_WRAP));
                }catch(Exception e){result.failed(e.getMessage());}
            }
            @Override public void onAuthenticationError(int code,CharSequence error){result.failed(errorMessage(code));}
            @Override public void onAuthenticationFailed(){/* OS retains the prompt; no server approval. */}
        });
        return cancel;
    }
}
