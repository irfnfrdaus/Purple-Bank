'use strict';
const pinBasePath = new URL('../', document.currentScript.src).pathname.replace(/\/$/, '');
const capture=new PinRhythmCapture(),get=id=>document.getElementById(id);
let busy=false,complete=false,controller=null;
function progress(){
  get('pin-progress').textContent=`${capture.strokes.length} / 6 digits recorded`;
  get('pin-submit').disabled=busy||complete||capture.strokes.length!==6;
  document.querySelectorAll('[data-digit]').forEach(b=>{b.disabled=busy||complete||capture.strokes.length===6;b.classList.remove('pressed');});
  get('pin-model').disabled=busy;
}
function reset(message=''){
  if(controller)controller.abort();controller=null;busy=false;complete=false;
  capture.reset();get('pin-result').replaceChildren();get('pin-error').textContent=message;progress();
}
function press(digit,source,id){
  if(busy||complete)return;
  try{capture.press(digit,source,id,performance.now());get('pin-error').textContent='';document.querySelector(`[data-digit="${digit}"]`)?.classList.add('pressed');}
  catch(error){reset(error.message);}
}
function release(id){
  if(busy||complete)return;
  try{capture.release(id,performance.now());progress();}catch(error){reset(error.message);}
}
document.querySelectorAll('[data-digit]').forEach(button=>{
  button.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();button.focus();button.setPointerCapture(e.pointerId);press(button.dataset.digit,'pointer',e.pointerId);});
  button.addEventListener('pointerup',e=>{if(e.button!==0)return;e.preventDefault();release(e.pointerId);});
  button.addEventListener('pointercancel',()=>reset('Touch cancelled. Enter the PIN again.'));
  button.addEventListener('lostpointercapture',()=>{if(capture.source==='pointer'&&capture.active)reset('Touch interrupted. Enter the PIN again.');});
  // Programmatic click / assistive activation has no measured press duration.
  button.addEventListener('click',()=>{if(!capture.strokes.length&&!capture.active&&!busy&&!complete)get('pin-error').textContent='Press and release a digit to measure its duration. Automatic clicks cannot be scored.';});
});
document.addEventListener('keydown',e=>{
  if(e.target.closest('select,a')||e.ctrlKey||e.altKey||e.metaKey)return;
  if(e.key==='Backspace'||e.key==='Escape'){e.preventDefault();reset();return;}
  if(e.key==='Enter'&&!e.target.closest('button')){e.preventDefault();evaluate();return;}
  if(/^[0-9]$/.test(e.key)){e.preventDefault();if(!e.repeat)press(e.key,'keyboard',e.code);}
});
document.addEventListener('keyup',e=>{if(/^[0-9]$/.test(e.key)&&!e.ctrlKey&&!e.altKey&&!e.metaKey){e.preventDefault();release(e.code);}});
window.addEventListener('blur',()=>{if(capture.active||capture.strokes.length&&!complete&&!busy)reset('Focus lost. Enter the PIN again.');});
document.addEventListener('visibilitychange',()=>{if(document.hidden&&(capture.active||capture.strokes.length&&!complete&&!busy))reset('Page left. Enter the PIN again.');});
get('pin-reset').onclick=()=>reset();get('pin-submit').onclick=()=>evaluate();
get('pin-model').onchange=()=>reset('Model changed. Enter the PIN again for a new attempt.');
async function evaluate(){
  if(busy||complete||capture.strokes.length!==6)return;
  if(capture.digits!=='246810'){reset('Practice PIN does not match. Use 246810.');return;}
  busy=true;progress();controller=new AbortController();const request=controller;
  const timer=setTimeout(()=>request.abort(),15000);
  try{
    const response=await fetch(pinBasePath+'/api/demo/pin-rhythm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(capture.payload(get('pin-model').value)),signal:request.signal});
    const result=await response.json();if(!response.ok)throw new Error('Invalid timings or server unavailable. Clear the PIN and try again.');
    if(controller!==request)return;
    complete=true;
    const tone=result.tier==='block'?'red':result.tier==='step_up'?'amber':'';
    const title=result.tier==='allow'?'Rhythm matches the reference':result.tier==='step_up'?'Rhythm differs · review suggested':'Rhythm differs substantially · timing alert';
    get('pin-result').innerHTML=`<div class="pin-verdict ${tone}"><h3>${title}</h3><p>The practice PIN is the same. The model compares two timing measures with generated reference data; the operator’s identity is not verified.</p></div><div class="pin-values"><div><small>Average press duration</small><strong>${Number(result.features_measured.mean_dwell_ms).toFixed(0)} ms</strong></div><div><small>Average pause between digits</small><strong>${Number(result.features_measured.mean_flight_ms).toFixed(0)} ms</strong></div></div><p class="pin-disclosure">Raw score: ${Number(result.model_score).toFixed(4)} · review: ${Number(result.thresholds[result.model].review).toFixed(4)} · block: ${Number(result.thresholds[result.model].block).toFixed(4)}. This score is not fraud probability. Reference: 700 generated training sessions / 300 generated validation sessions. No banking action is taken.</p><button class="btn primary full" id="pin-again">Try another person or rhythm</button>`;
    get('pin-again').onclick=()=>reset();
  }catch(error){if(controller===request)get('pin-error').textContent=error.name==='AbortError'?'The server did not respond. Try again.':error.message;}
  finally{clearTimeout(timer);if(controller===request){busy=false;controller=null;progress();}}
}
progress();
