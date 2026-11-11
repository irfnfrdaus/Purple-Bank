'use strict';
// Digits stay in memory on the page. Only relative timing leaves the collector.
// Shared with the browser test so pointer and keyboard use identical rules.
class PinRhythmCapture {
  constructor(){this.reset();}
  reset(){this.digits='';this.strokes=[];this.active=null;this.source=null;this.origin=null;}
  press(digit,source,id,time){
    if(this.active||this.strokes.length===6||this.source&&this.source!==source)throw new Error('Use one input method and one digit at a time. Clear the PIN and try again.');
    if(!/^[0-9]$/.test(digit)||!Number.isFinite(time))throw new Error('Invalid timing input.');
    if(this.origin===null)this.origin=time;
    this.source=source;this.active={digit,id,down_ms:time-this.origin};
  }
  release(id,time){
    if(!this.active||id!==this.active.id)return false;
    const stroke={down_ms:this.active.down_ms,up_ms:time-this.origin};
    const hold=stroke.up_ms-stroke.down_ms,last=this.strokes.at(-1);
    if(!Number.isFinite(time)||hold<15||hold>3000||stroke.up_ms>45000||last&&(stroke.down_ms-last.up_ms<20||stroke.down_ms-last.up_ms>5000)){
      this.reset();throw new Error('Incomplete timing: hold for 15–3000 ms, release, then pause for 20–5000 ms. Enter the PIN again.');
    }
    this.digits+=this.active.digit;this.strokes.push(stroke);this.active=null;return true;
  }
  payload(model){
    if(this.active||this.strokes.length!==6)throw new Error('Complete all six digits first.');
    return {strokes:this.strokes.map(s=>({...s})),source:this.source,model};
  }
}
globalThis.PinRhythmCapture=PinRhythmCapture;
if(typeof module!=='undefined')module.exports={PinRhythmCapture};
