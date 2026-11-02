const $=id=>document.getElementById(id);
const gestures={Open_Palm:'Open palm',Closed_Fist:'Closed fist',Thumb_Up:'Thumbs up',Thumb_Down:'Thumbs down',Pointing_Up:'Pointing up',Victory:'Victory / V sign',ILoveYou:'I love you sign'};
let cameras=[],devices=[],editing=null,loading=false,eventSignature='';
const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
const json=(method,data)=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
function notice(message){$('notice').textContent=message;$('notice').hidden=!message;}
async function api(path,options){const r=await fetch(path,options);let data;try{data=await r.json();}catch{}if(!r.ok){if(r.status===401){$('signIn').hidden=false;$('cameraDashboard').hidden=true;$('rtspUrl').value='';}throw Error(typeof data?.detail==='string'?data.detail:'Request failed. Check your settings.');}return data;}
const guard=fn=>async(...args)=>{try{await fn(...args);}catch(e){notice(e.message);}};
function label(text,control){const e=node('label',text);e.append(control);return e;}
function select(options,value){const s=node('select');for(const [key,text] of Object.entries(options))s.add(new Option(text,key));s.value=value;return s;}
function addRule(rule={gesture:'Open_Palm',action_enabled:false,entity_id:'',action:'on',value:null}){
  if($('rules').children.length>=7){notice('Up to seven different gestures can be configured.');return;}
  const box=node('section',undefined,'gesture-rule'),grid=node('div',undefined,'settings-grid');
  const gesture=select(gestures,rule.gesture),mode=select({detect:'Detection only',action:'Run a device action'},rule.action_enabled?'action':'detect');
  const device=node('select'),action=node('select'),value=node('div',undefined,'rule-value');
  device.add(new Option('Choose a device',''));for(const d of devices)device.add(new Option(d.name+' · '+d.entity_id,d.entity_id));
  if(rule.entity_id&&!devices.some(d=>d.entity_id===rule.entity_id))device.add(new Option('Unavailable: '+rule.entity_id,rule.entity_id));
  device.value=rule.entity_id;
  let control,inputs=[];
  function drawValue(saved){value.replaceChildren();inputs=[];control=devices.find(d=>d.entity_id===device.value)?.controls.find(c=>c.key===action.value);if(!control?.kind)return;
    const count=control.kind==='range'?2:1;for(let i=0;i<count;i++){let input;
      if(control.kind==='select'){input=node('select');for(const item of control.options)input.add(new Option(item,item));}
      else{input=node('input');input.type=control.kind==='color'?'color':'number';if(input.type==='number'){input.min=control.min;input.max=control.max;input.step=control.step??1;input.required=true;}else input.value='#ffffff';}
      const initial=Array.isArray(saved)?saved[i]:saved;if(initial!==undefined&&initial!==null)input.value=initial;
      input.setAttribute('aria-label',control.kind==='range'?(i?'High value':'Low value'):control.label);inputs.push(input);value.append(input);
    }
  }
  function actionChoices(key,saved){action.replaceChildren();const choices=devices.find(d=>d.entity_id===device.value)?.controls||[];for(const c of choices)action.add(new Option(c.label,c.key));if(key&&choices.some(c=>c.key===key))action.value=key;drawValue(saved);setMode();}
  function setMode(){const enabled=mode.value==='action';device.disabled=action.disabled=!enabled;device.required=action.required=enabled;for(const input of inputs)input.disabled=!enabled;grid.children[2].hidden=grid.children[3].hidden=!enabled;grid.children[4].hidden=!enabled||!control?.kind;}
  device.onchange=()=>actionChoices();action.onchange=()=>{drawValue(null);setMode();};mode.onchange=setMode;
  grid.append(label('Gesture',gesture),label('When recognized',mode),label('Device',device),label('Action',action),label('Action value',value));
  const remove=node('button','Remove rule','quiet');remove.type='button';remove.onclick=()=>box.remove();box.append(grid,remove);
  box.read=()=>({gesture:gesture.value,action_enabled:mode.value==='action',entity_id:device.value,action:action.value||'on',value:!control?.kind?null:control.kind==='range'?inputs.map(i=>Number(i.value)):control.kind==='number'?Number(inputs[0].value):inputs[0].value});
  $('rules').append(box);actionChoices(rule.action,rule.value);
}
function edit(camera){editing=camera?.id||null;$('formTitle').textContent=camera?'Edit '+camera.name:'Add camera';$('cameraName').value=camera?.name||'';$('rtspUrl').value='';$('rtspUrl').required=!camera;$('rtspUrl').placeholder=camera?'Leave blank to keep the saved connection':'rtsp://username:password@192.168.1.50:554/stream2';$('cameraEnabled').checked=camera?.enabled||false;
  $('snapshotEvents').checked=!!camera?.snapshot_events;
  $('holdSeconds').value=camera?.hold_seconds??1;$('confidence').value=camera?.confidence??.75;$('cooldownSeconds').value=camera?.cooldown_seconds??5;$('fps').value=camera?.fps??3;$('rules').replaceChildren();for(const rule of camera?.rules||[undefined])addRule(rule);$('cameraForm').hidden=false;$('cameraForm').scrollIntoView({behavior:'smooth',block:'start'});$('cameraName').focus();
}
function settings(camera){const {name,enabled,confidence,hold_seconds,cooldown_seconds,fps,rules,snapshot_events}=camera;return {name,enabled,confidence,hold_seconds,cooldown_seconds,fps,rules,snapshot_events};}
function renderEvents(events){
  const signature=JSON.stringify(events);if(signature===eventSignature)return;eventSignature=signature;
  const openIds=new Set([...$('events').querySelectorAll('details[open]')].map(e=>e.dataset.id));
  $('events').replaceChildren();
  for(const event of events){
    const meta=event.metadata,name=meta?.camera_name||cameras.find(c=>c.id===event.camera_id)?.name||'Removed camera';
    const row=node('div',undefined,'event-row');row.append(node('p',`${new Date(event.created*1000).toLocaleString()} · ${name} · ${gestures[event.gesture]||event.gesture} · ${event.outcome}`));
    if(meta){
      row.append(node('p',`${Math.round(meta.confidence*100)}% confidence · threshold ${Math.round(meta.confidence_threshold*100)}% · hold ${meta.hold_seconds}s · ${Math.round(meta.inference_ms)} ms inference`,'help'));
      row.append(node('p',meta.rule.action_enabled?`${meta.rule.entity_id} → ${meta.rule.action}${meta.rule.value!==null?' · '+JSON.stringify(meta.rule.value):''}`:'Detection-only rule','help'));
      const details=node('details'),summary=node('summary',event.has_snapshot?'View snapshot & debugging details':'View debugging details');details.dataset.id=event.id;details.open=openIds.has(event.id);details.append(summary);
      if(event.has_snapshot){const img=node('img');img.alt=`${gestures[event.gesture]||event.gesture} triggering frame`;img.loading='lazy';img.src=`/api/cameras/events/${event.id}/snapshot`;img.className='event-snapshot';details.append(img);}
      else{const reasons={disabled:'Snapshot capture was off.',missing:'No snapshot was received for this event.',invalid:'The frame could not be saved.',low_disk:'Snapshot skipped to preserve disk space.',storage_error:'Snapshot storage failed.',expired:'Snapshot expired after seven days.',deleted:'Snapshot deleted.'};details.append(node('p',reasons[event.snapshot_status]||'No saved snapshot.','help'));}
      details.append(node('p',`${meta.model} · source ${meta.source_width}×${meta.source_height} · frame age ${Math.round(meta.frame_age_ms)} ms`,'help'));
      const links=node('div',undefined,'camera-actions');
      const metadata=node('a','Download event JSON');metadata.href=`/api/cameras/events/${event.id}/metadata`;metadata.download=`adam-event-${event.id}.json`;links.append(metadata);
      if(event.has_snapshot){const download=node('a','Download snapshot');download.href=`/api/cameras/events/${event.id}/snapshot`;download.download=`adam-event-${event.id}.jpg`;links.append(download);const remove=node('button','Delete snapshot','quiet');remove.onclick=guard(async()=>{if(confirm('Delete this saved snapshot? Event details will remain.')){await api(`/api/cameras/events/${event.id}/snapshot`,{method:'DELETE'});await load();}});links.append(remove);}
      details.append(links);row.append(details);
    }
    $('events').append(row);
  }
  if(!events.length)$('events').append(node('p','No gesture events yet.','help'));
}
function render(data){
  cameras=data.cameras;$('cameraDashboard').hidden=false;$('signIn').hidden=true;
  $('workerStatus').textContent=!data.worker_online?'Camera worker is offline. Saved connections will be available when it starts.':data.voice_busy?'Paused for a voice request. Detection will resume automatically.':'Camera worker online · local gesture recognition';
  const list=$('cameraList');
  for(const old of [...list.children])if(!cameras.some(c=>c.id===old.dataset.id))old.remove();
  for(const camera of cameras){let card=[...list.children].find(c=>c.dataset.id===camera.id);if(!card){card=node('article',undefined,'camera-card');card.dataset.id=camera.id;card.append(node('h3'),node('small'),node('p',undefined,'badge'),node('img'),node('p',undefined,'help'),node('div',undefined,'camera-actions'));card.querySelector('img').onerror=()=>{card.querySelector('img').hidden=true;};list.append(card);}
    card.querySelector('h3').textContent=camera.name;card.querySelector('small').textContent=camera.endpoint;
    const runtime=camera.runtime;card.querySelector('.badge').textContent=camera.enabled?runtime.status.replaceAll('_',' '):'Detection paused';
    const preview=card.querySelector('img');preview.alt='Latest preview from '+camera.name;preview.hidden=!camera.has_preview;if(camera.has_preview)preview.src=`/api/cameras/${camera.id}/preview?t=${Date.now()}`;
    card.querySelector('.help').textContent=runtime.gesture&&runtime.gesture!=='None'?`${gestures[runtime.gesture]} · ${Math.round(runtime.confidence*100)}% confidence · ${Math.round(runtime.inference_ms)} ms inference`:'No gesture detected. Keep your hand clearly visible in the camera view.';
    const actions=card.querySelector('.camera-actions');actions.replaceChildren();
    for(const [text,fn] of [['Edit',()=>edit(camera)],['Test connection',async()=>{await api(`/api/cameras/${camera.id}/test`,{method:'POST'});notice('Connection test requested. Watch the preview and status.');}], [camera.enabled?'Pause':'Start',async()=>{await api(`/api/cameras/${camera.id}`,json('PUT',{...settings(camera),enabled:!camera.enabled}));await load();}],['Delete',async()=>{if(confirm('Delete '+camera.name+' and its gesture rules?')){await api(`/api/cameras/${camera.id}`,{method:'DELETE'});if(editing===camera.id)$('cameraForm').hidden=true;await load();}}]]){const b=node('button',text);b.onclick=guard(async()=>{b.disabled=true;try{await fn();}finally{b.disabled=false;}});actions.append(b);}
  }
  if(!cameras.length&&!list.querySelector('.empty'))list.append(node('p','No cameras yet. Add an RTSP connection to begin.','empty help'));
  if(cameras.length)list.querySelector('.empty')?.remove();
  renderEvents(data.events);
}
async function load(){if(loading)return;loading=true;try{render(await api('/api/cameras'));}finally{loading=false;}}
$('newCamera').onclick=()=>edit();$('cancelEdit').onclick=()=>{$('cameraForm').hidden=true;$('rtspUrl').value='';};$('addRule').onclick=()=>{const used=[...$('rules').children].map(b=>b.read().gesture);addRule({gesture:Object.keys(gestures).find(g=>!used.includes(g))||'Open_Palm',action_enabled:false,entity_id:'',action:'on'});};
$('cameraForm').onsubmit=guard(async e=>{e.preventDefault();$('saveCamera').disabled=true;try{const options={name:$('cameraName').value,rtsp_url:$('rtspUrl').value,enabled:$('cameraEnabled').checked,snapshot_events:$('snapshotEvents').checked,confidence:Number($('confidence').value),hold_seconds:Number($('holdSeconds').value),cooldown_seconds:Number($('cooldownSeconds').value),fps:Number($('fps').value),rules:[...$('rules').children].map(b=>b.read())};await api('/api/cameras'+(editing?'/'+editing:''),json(editing?'PUT':'POST',options));$('rtspUrl').value='';$('cameraForm').hidden=true;notice('Camera saved. Remove your hand briefly before testing a gesture.');await load();}finally{$('saveCamera').disabled=false;}});
guard(async()=>{const inventory=await api('/api/home-assistant/inventory');devices=inventory.devices.filter(d=>d.imported&&d.enabled&&d.present&&d.controls?.length);await load();})();
setInterval(()=>{if(!document.hidden&&!$('cameraDashboard').hidden)guard(load)();},2500);
