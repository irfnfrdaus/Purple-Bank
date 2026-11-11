// Run: node tests/pin_rhythm_capture.cjs
const assert=require('node:assert/strict');
const {PinRhythmCapture}=require('../web/pin-rhythm.js');
const c=new PinRhythmCapture();
const digits='246810';
for(let i=0;i<6;i++){
  const t=10000+i*1110;
  c.press(digits[i],'pointer',i,t);c.release(i,t+110);
}
const payload=c.payload('isolation_forest');
assert.equal(c.digits,digits);
assert.equal(payload.strokes[0].down_ms,0);
assert.equal(payload.strokes[1].down_ms-payload.strokes[0].up_ms,1000);
assert.ok(!JSON.stringify(payload).includes(digits));
assert.deepEqual(Object.keys(payload).sort(),['model','source','strokes']);
assert.throws(()=>c.press('2','pointer',9,20000));
c.reset();
assert.throws(()=>c.payload('isolation_forest'));
c.press('2','keyboard','Digit2',100);
assert.equal(c.release('Digit4',120),false);
assert.throws(()=>c.press('4','keyboard','Digit4',140));
assert.throws(()=>c.release('Digit2',105));
assert.equal(c.strokes.length,0);
c.press('2','pointer',1,500);c.release(1,600);
assert.throws(()=>c.press('4','keyboard','Digit4',800));
c.reset();c.press('2','pointer',1,500);
assert.throws(()=>c.release(1,Infinity));
assert.equal(c.strokes.length,0);
console.log('PIN timing capture: relative timestamps, incomplete input, overlap, source mixing and digit exclusion passed');
