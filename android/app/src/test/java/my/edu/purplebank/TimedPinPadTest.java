package my.edu.purplebank;

import static org.junit.Assert.*;
import android.view.MotionEvent;
import android.view.View;
import android.widget.Button;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;

@RunWith(RobolectricTestRunner.class)
@Config(sdk={26,35,36})
public class TimedPinPadTest {
    private TimedPinPad pad(){return new TimedPinPad(RuntimeEnvironment.getApplication(),"PIN");}
    private void event(View key,int action,long tick){
        key.layout(0,0,100,60);
        MotionEvent e=MotionEvent.obtain(tick,tick,action,20,20,0);key.dispatchTouchEvent(e);e.recycle();
    }
    @Test public void realPressesProduceArbitraryPinAndOnlyTimingPayload() throws Exception {
        TimedPinPad p=pad();String digits="482619";long tick=1000;
        for(char digit:digits.toCharArray()){
            View key=p.findViewWithTag("pin-digit-"+digit);
            event(key,MotionEvent.ACTION_DOWN,tick);event(key,MotionEvent.ACTION_UP,tick+110);tick+=310;
        }
        assertEquals(digits,p.pin());
        assertEquals(6,p.timing().getJSONArray("strokes").length());
        assertFalse(p.timing().has("pin"));assertFalse(p.timing().toString().contains(digits));
    }
    @Test public void clickAloneNeverInventsTimingsAndCancelDropsIncompleteInput(){
        TimedPinPad p=pad();View key=p.findViewWithTag("pin-digit-4");
        key.performClick();
        try{p.pin();fail("Click produced a PIN");}catch(IllegalStateException expected){}
        event(key,MotionEvent.ACTION_DOWN,1000);event(key,MotionEvent.ACTION_CANCEL,1050);
        event(key,MotionEvent.ACTION_UP,1110);
        try{p.pin();fail("Cancelled press survived");}catch(IllegalStateException expected){}
    }
    @Test public void focusLossClearsSecretAndTiming(){
        TimedPinPad p=pad();long tick=1000;
        for(char digit:"482619".toCharArray()){
            View key=p.findViewWithTag("pin-digit-"+digit);
            event(key,MotionEvent.ACTION_DOWN,tick);event(key,MotionEvent.ACTION_UP,tick+110);tick+=310;
        }
        assertEquals("482619",p.pin());p.onWindowFocusChanged(false);
        try{p.pin();fail("Focus loss kept a PIN");}catch(IllegalStateException expected){}
    }
}
