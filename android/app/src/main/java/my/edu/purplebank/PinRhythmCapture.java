package my.edu.purplebank;

/** Bounded monotonic timing capture. No Android dependency; no digit in payload. */
public final class PinRhythmCapture {
    private final long[][] strokes = new long[6][2];
    private final StringBuilder digits = new StringBuilder();
    private int count;
    private long origin = -1, down, activeId;
    private boolean active;
    private char activeDigit;
    private String source;

    public void reset() {
        count = 0; origin = -1; active = false; source = null; digits.setLength(0);
        for (long[] stroke : strokes) { stroke[0] = 0; stroke[1] = 0; }
        activeDigit = 0; down = 0; activeId = 0;
    }
    private IllegalArgumentException invalid(String message) { reset(); return new IllegalArgumentException(message); }
    public int count() { return count; }
    public boolean active() { return active; }
    public boolean complete() { return count == 6 && !active; }
    public String pin() {
        if(!complete())throw new IllegalStateException("Complete all six PIN presses first.");
        return digits.toString();
    }
    public boolean matchesDemoPin() { return complete() && "246810".contentEquals(digits); }
    public void press(char digit, String inputSource, long id, long time) {
        if (active || count == 6 || source != null && !source.equals(inputSource))
            throw invalid("Use one digit and one input method at a time. Start again.");
        if (digit < '0' || digit > '9' || time < 0 || !("pointer".equals(inputSource) || "keyboard".equals(inputSource)))
            throw invalid("Invalid timing input. Enter the PIN again.");
        if (origin < 0) origin = time;
        if (time < origin || time - origin > 45000) throw invalid("This attempt expired. Enter the PIN again.");
        source = inputSource; active = true; activeDigit = digit; activeId = id; down = time - origin;
    }
    public boolean release(long id, long time) {
        if (!active || activeId != id) return false;
        long up = time - origin;
        long hold = up - down;
        if (time < origin || up > 45000 || hold < 15 || hold > 3000
                || count > 0 && (down - strokes[count - 1][1] < 20 || down - strokes[count - 1][1] > 5000))
            throw invalid("Incomplete timing: press for 15–3000 ms, then release and pause for 20–5000 ms. Start again.");
        strokes[count][0] = down; strokes[count][1] = up; count++;
        digits.append(activeDigit); active = false; activeDigit = 0;
        return true;
    }
    public String payload(String model) {
        if (!complete()) throw new IllegalStateException("Complete all six digits first.");
        if (!("isolation_forest".equals(model) || "one_class_svm".equals(model)))
            throw new IllegalArgumentException("Invalid model.");
        StringBuilder result = new StringBuilder("{\"strokes\":[");
        for (int i = 0; i < count; i++) {
            if (i > 0) result.append(',');
            result.append("{\"down_ms\":").append(strokes[i][0]).append(",\"up_ms\":").append(strokes[i][1]).append('}');
        }
        return result.append("],\"source\":\"").append(source).append("\",\"model\":\"").append(model).append("\"}").toString();
    }
}
