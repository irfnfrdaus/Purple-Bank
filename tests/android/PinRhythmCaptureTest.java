package my.edu.purplebank;

/** Host-JVM contract and failure-path checks, no Android framework required. */
public final class PinRhythmCaptureTest {
    private static int checks;
    private static void check(boolean result) { checks++; if (!result) throw new AssertionError("Check " + checks); }
    private static void rejected(Runnable operation) {
        try { operation.run(); throw new AssertionError("Expected rejection"); }
        catch (IllegalArgumentException | IllegalStateException expected) { checks++; }
    }
    private static PinRhythmCapture attempt(String source, int gap, long offset, String digits) {
        PinRhythmCapture c = new PinRhythmCapture();
        for (int i = 0; i < 6; i++) { long t = offset + i * (110L + gap); c.press(digits.charAt(i), source, i, t); c.release(i, t + 110); }
        return c;
    }
    public static void main(String[] args) throws Exception {
        for (String source : new String[]{"pointer", "keyboard"}) {
            for (String model : new String[]{"isolation_forest", "one_class_svm"}) {
                for (int gap : new int[]{200, 1000}) {
                    PinRhythmCapture c = attempt(source, gap, 10000, "246810");
                    check(c.complete() && c.matchesDemoPin());
                    String payload = c.payload(model);
                    check(!payload.contains("246810") && !payload.contains("digit") && !payload.contains("user_id"));
                    check(payload.equals(attempt(source, gap, 900000, "246810").payload(model)));
                    System.out.println("WIRE\t" + payload);
                }
            }
        }
        PinRhythmCapture c = new PinRhythmCapture();
        rejected(() -> c.payload("isolation_forest"));
        c.press('2', "pointer", 1, 100); check(!c.release(2, 210)); check(c.active());
        rejected(() -> c.press('4', "pointer", 2, 300)); check(c.count() == 0 && !c.active());
        c.press('2', "pointer", 1, 100); rejected(() -> c.release(1, 110)); check(c.count() == 0);
        c.press('2', "pointer", 1, 100); rejected(() -> c.release(1, 3200));
        c.press('2', "pointer", 1, 100); c.release(1, 210);
        rejected(() -> c.press('4', "keyboard", 2, 410)); check(c.count() == 0);
        c.press('2', "pointer", 1, 100); c.release(1, 210); c.press('4', "pointer", 2, 220);
        rejected(() -> c.release(2, 330));
        c.press('2', "pointer", 1, 100); c.release(1, 210); c.press('4', "pointer", 2, 6000);
        rejected(() -> c.release(2, 6110));
        c.press('2', "pointer", 1, 100); c.reset(); check(!c.release(1, 210) && c.count() == 0);
        rejected(() -> c.press('2', "owner", 1, 100));
        rejected(() -> c.press('x', "pointer", 1, 100));
        rejected(() -> c.press('2', "pointer", 1, -1));
        c.press('2', "pointer", 1, 100); rejected(() -> c.release(1, 50));
        check(!attempt("pointer", 200, 100, "246811").matchesDemoPin());
        PinRhythmCapture full = attempt("pointer", 200, 100, "246810");
        rejected(() -> full.payload("fake"));
        rejected(() -> full.press('2', "pointer", 7, 5000)); check(!full.complete());
        check(BackendUrl.normalise(" https://example.test/demo/ ", false).equals("https://example.test/demo"));
        check(BackendUrl.normalise("http://127.0.0.1:8000", true).equals("http://127.0.0.1:8000"));
        for (String invalid : new String[]{"http://example.test", "https://user:pass@example.test", "https://example.test?x=1", "https://example.test/#token", "https://example.test:99999", "file:///tmp/x", "https://example.test:0"}) {
            boolean bad = false; try { BackendUrl.normalise(invalid, false); } catch (Exception expected) { bad = true; } check(bad);
        }
        System.out.println("Android host-JVM checks passed: " + checks);
    }
}
