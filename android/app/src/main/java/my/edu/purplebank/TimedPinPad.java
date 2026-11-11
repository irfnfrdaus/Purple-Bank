package my.edu.purplebank;

import android.content.Context;
import android.view.MotionEvent;
import android.view.inputmethod.InputMethodManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import org.json.JSONObject;

/** Six measured presses; arbitrary PIN digits are never part of timing JSON. */
public final class TimedPinPad extends LinearLayout {
    private final PinRhythmCapture capture=new PinRhythmCapture();
    private final TextView progress;
    public TimedPinPad(Context context,String label){
        super(context);setOrientation(VERTICAL);setSaveEnabled(false);
        TextView title=new TextView(context);title.setText(label);title.setTextSize(14);addView(title);
        progress=new TextView(context);progress.setTextSize(18);progress.setPadding(0,dp(8),0,dp(8));addView(progress);
        String[] rows={"123","456","789","0"};
        for(String row:rows){
            LinearLayout line=new LinearLayout(context);line.setOrientation(HORIZONTAL);addView(line);
            for(char digit:row.toCharArray()){
                Button key=new Button(context);key.setText(String.valueOf(digit));key.setTextSize(20);key.setAllCaps(false);key.setTag("pin-digit-"+digit);
                line.addView(key,new LinearLayout.LayoutParams(0,dp(48),1));
                key.setOnClickListener(v->{ /* A synthetic click has no measured duration. */ });
                key.setOnTouchListener((v,event)->{
                    try {
                        switch(event.getActionMasked()){
                            case MotionEvent.ACTION_DOWN:
                                ((InputMethodManager)context.getSystemService(Context.INPUT_METHOD_SERVICE)).hideSoftInputFromWindow(getWindowToken(),0);
                                if(capture.complete())return true;
                                capture.press(digit,"pointer",event.getPointerId(0),event.getEventTime());break;
                            case MotionEvent.ACTION_UP:
                                if(event.getX()<0 || event.getY()<0 || event.getX()>key.getWidth() || event.getY()>key.getHeight())clear();
                                else capture.release(event.getPointerId(0),event.getEventTime());
                                v.performClick();break;
                            case MotionEvent.ACTION_POINTER_DOWN:
                            case MotionEvent.ACTION_CANCEL:clear();break;
                            default:break;
                        }
                        update();
                    }catch(IllegalArgumentException error){clear();progress.setText(error.getMessage());}
                    return true;
                });
            }
        }
        Button reset=new Button(context);reset.setText("Clear and retry");reset.setAllCaps(false);reset.setOnClickListener(v->clear());addView(reset);update();
    }
    private int dp(int n){return Math.round(n*getResources().getDisplayMetrics().density);}
    private void update(){StringBuilder dots=new StringBuilder();for(int i=0;i<6;i++)dots.append(i<capture.count()?"●":"○");progress.setText(dots.toString());}
    public void clear(){capture.reset();if(progress!=null)update();}
    public String pin(){return capture.pin();}
    public JSONObject timing() throws Exception {return new JSONObject(capture.payload("isolation_forest"));}
    @Override public void onWindowFocusChanged(boolean focus){super.onWindowFocusChanged(focus);if(!focus)clear();}
    @Override protected void onDetachedFromWindow(){clear();super.onDetachedFromWindow();}
}
