'use strict';
const $=id=>document.getElementById(id);
const selected=new Set();
let signedIn=false,setup=false,fleet=null,mode='text',voice=null,recorder=null,recordStream=null,recordClock=null,previewUrl=null,polling=false,sending=false,unitSignature='';
function el(tag,text='',className=''){const n=document.createElement(tag);n.textContent=text;n.className=className;return n;}
function notice(message=''){$('notice').textContent=message;$('notice').hidden=!message;}
async function api(path,options={}){
  const r=await fetch(path,options);
  if(!r.ok){let detail;try{detail=(await r.json()).detail;}catch{}if(r.status===401&&signedIn){signedIn=false;await auth();}throw new Error(typeof detail==='string'?detail:`Request failed (${r.status})`);}
  return r.json();
}
const post=(path,body={})=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
const guard=fn=>async(...args)=>{try{await fn(...args);}catch(e){notice(e.message);}};
async function auth(){
  const state=await api('/api/auth/status');signedIn=state.authenticated;setup=!state.configured;
  $('auth').hidden=signedIn;$('dashboard').hidden=!signedIn;$('logout').hidden=!signedIn;$('benchLink').hidden=!signedIn;
  $('voicesLink').hidden=!signedIn;$('queriesLink').hidden=!signedIn;
  $('authTitle').textContent=setup?'Create your local account':'Welcome back';
  $('authHelp').textContent=setup?(state.setup_local?'Choose an administrator password of at least 12 characters. It stays on this laptop.':'Create the first account on this laptop at http://127.0.0.1:8765.'):'Sign in to send pages and review incoming speech.';
  $('password').minLength=setup?12:1;$('password').autocomplete=setup?'new-password':'current-password';$('login').textContent=setup?'Create account':'Sign in';
  $('login').disabled=setup&&!state.setup_local;
  if(signedIn){await refresh();await refreshVoices();}else $('connection').textContent='Local head-end';
}
async function refreshVoices(){
  if(!signedIn)return;
  const data=await api('/api/voices'),previous=$('pageVoice').value;
  const current=data.voices.find(v=>v.voice_id===data.default_voice);
  $('pageVoice').replaceChildren(new Option(`Default · ${current?.name||'Basic English'}`,''));
  for(const profile of data.voices)if(profile.ready)$('pageVoice').add(new Option(profile.name+' · '+profile.version,profile.voice_id));
  if([...$('pageVoice').options].some(o=>o.value===previous))$('pageVoice').value=previous;
}
function selection(){
  const names=(fleet?.units||[]).filter(u=>selected.has(u.id)).map(u=>u.name);
  $('recipientSummary').textContent=names.length?`To ${names.join(', ')}`:'Select one or more units';
  $('sendPage').disabled=sending||!names.length||(mode==='text'?!$('message').value.trim():!voice)||!!recorder;
  document.querySelectorAll('.unit-card').forEach(n=>n.classList.toggle('selected',selected.has(n.dataset.id)));
}
function units(){
  const signature=JSON.stringify(fleet.units.map(({seen,...u})=>u));
  if(signature===unitSignature)return;unitSignature=signature;
  $('units').replaceChildren();
  if(!fleet.units.length)$('units').append(el('div','Pair the USB-connected box to add your first unit.','empty'));
  for(const unit of fleet.units){
    const card=el('div','','unit-card');card.dataset.id=unit.id;
    const label=el('label','','unit-select'),check=document.createElement('input');check.type='checkbox';check.checked=selected.has(unit.id);check.setAttribute('aria-label',`Select ${unit.name}`);
    check.onchange=()=>{check.checked?selected.add(unit.id):selected.delete(unit.id);selection();};
    const name=el('div');name.append(el('strong',unit.name),el('small',unit.firmware||'Waiting for first connection'));label.append(check,name);
    const actions=el('div','','unit-actions'),listen=el('button','Listen 8 sec','quiet');listen.disabled=!unit.online;
    listen.onclick=guard(async()=>{await post(`/api/units/${unit.id}/listen`);notice(`Listening requested on ${unit.name}. Speak when its display says LISTENING.`);});
    actions.append(el('span',unit.online?'Online':'Offline',`badge ${unit.online?'online':''}`),listen);
    const rename=el('button','Rename','quiet');rename.onclick=guard(async()=>{const name=window.prompt('Name for this unit',unit.name);if(name?.trim()){await api(`/api/units/${unit.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name.trim()})});await refresh();}});
    const wake=el('div',unit.wake_model==='unavailable'?'Wake model not installed · touch/remote testing':`Wake model: ${unit.wake_model}`,'wake');
    card.append(label,actions,wake,rename);$('units').append(card);
  }
}
function historyItem(container,id){
  let item=container.querySelector(`[data-id="${id}"]`);
  if(!item){item=el('article','','history-item');item.dataset.id=id;const heading=el('div','','history-heading');heading.append(el('strong','','title'),el('span','','badge'));item.append(heading,el('p'),el('small'));container.prepend(item);}
  return item;
}
function histories(){
  const names=new Map(fleet.units.map(u=>[u.id,u.name]));
  for(const [key,empty] of [['sessions','No voice sessions yet. Try Listen on the connected unit.'],['pages','Your sent messages and delivery confirmations will appear here.']]){
    const container=$(key),rows=fleet[key];
    container.querySelector('.empty')?.remove();
    const ids=new Set(rows.map(r=>r.id));container.querySelectorAll('[data-id]').forEach(n=>{if(!ids.has(n.dataset.id))n.remove();});
    if(!rows.length){container.append(el('div',empty,'empty'));continue;}
    for(const row of [...rows].reverse()){
      const item=historyItem(container,row.id),badge=item.querySelector('.badge');
      item.querySelector('.title').textContent=names.get(row.unit_id)||'Unit';badge.textContent=row.status||row.state;badge.className=`badge ${row.status||row.state}`;
      if(key==='sessions'){
        item.querySelector('p').textContent=row.text||row.error||(row.status==='listening'?'Receiving live audio…':row.status==='no_speech'?'No speech detected.':'Waiting for local transcription…');
        item.querySelector('small').textContent=`${row.trigger==='wake'?`Wake · ${row.model_id}`:'Touch / remote test'} · ${new Date(row.started*1000).toLocaleTimeString()}${row.preroll_ms?` · ${(row.preroll_ms/1000).toFixed(1)} sec pre-roll`:''}${row.capture_ms?` · ${(row.capture_ms/1000).toFixed(1)} sec capture`:''}${row.endpoint_reason?` · End: ${row.endpoint_reason.replace('_',' ')}`:''}${row.inference_ms?` · ${(row.inference_ms/1000).toFixed(2)} sec recognition`:''}`;
        let progress=item.querySelector('.progress');if(row.status==='listening'){if(!progress){progress=el('div','','progress');progress.append(el('span'));item.append(progress);}progress.firstChild.style.width=`${Math.min(100,100*row.received/row.expected)}%`;}else progress?.remove();
        if(row.clip_id&&!item.querySelector('audio')){const player=document.createElement('audio');player.controls=true;player.preload='none';player.src=`/api/clips/${row.clip_id}/audio`;player.setAttribute('aria-label','Play received voice clip');item.append(player);}
        if(row.reply && row.reply.status!=='ignored'){
          let reply=item.querySelector('.query-reply');if(!reply){reply=el('div','','query-reply');item.append(reply);}
          const delivery=fleet.pages.find(p=>p.id===row.reply.page_id);
          reply.replaceChildren(el('p',`Answer · ${delivery?.state||row.reply.status}: ${row.reply.text||row.reply.error||'Checking your question…'}`));
          if(row.reply.source){const source=el('a',row.reply.source);source.href=row.reply.source_url;source.target='_blank';source.rel='noopener noreferrer';reply.append(source);}
        }
      }else{
        item.querySelector('p').textContent=row.kind==='text'?row.text:'Recorded voice message';
        const remaining=Math.max(0,Math.ceil(row.expires-Date.now()/1000));
        item.querySelector('small').textContent=row.detail||(['queued','offered'].includes(row.state)?`Expires in ${Math.floor(remaining/60)}:${String(remaining%60).padStart(2,'0')}`:new Date(row.created*1000).toLocaleTimeString());
        let cancel=item.querySelector('button');if(['queued','offered'].includes(row.state)){if(!cancel){cancel=el('button','Cancel','quiet');cancel.style.marginLeft='14px';cancel.onclick=guard(async()=>{await post(`/api/pages/${row.id}/cancel`);await refresh();});item.append(cancel);}}else cancel?.remove();
      }
    }
  }
}
async function refresh(){
  if(!signedIn||polling)return;polling=true;
  try{fleet=await api('/api/fleet');$('connection').textContent='Head-end online';$('connection').className='badge online';$('onlineCount').textContent=fleet.units.filter(u=>u.online).length;$('pendingCount').textContent=fleet.pages.filter(p=>['queued','offered','playing'].includes(p.state)).length;$('sessionCount').textContent=fleet.sessions.length;$('serverUrl').textContent=fleet.url;if(!$('pairUrl').value)$('pairUrl').value=fleet.url;units();histories();selection();}
  finally{polling=false;}
}
function tab(value){mode=value;$('textCompose').hidden=mode!=='text';$('voiceCompose').hidden=mode!=='voice';for(const name of ['text','voice']){$(name+'Tab').classList.toggle('active',mode===name);$(name+'Tab').setAttribute('aria-pressed',String(mode===name));}selection();}
function preview(blob){voice=blob;if(previewUrl)URL.revokeObjectURL(previewUrl);previewUrl=URL.createObjectURL(blob);$('voicePreview').src=previewUrl;$('voicePreview').hidden=false;selection();}
function wav(samples){const buffer=new ArrayBuffer(44+samples.length*2),view=new DataView(buffer);const str=(at,s)=>{for(let i=0;i<s.length;i++)view.setUint8(at+i,s.charCodeAt(i));};str(0,'RIFF');view.setUint32(4,36+samples.length*2,true);str(8,'WAVEfmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,16000,true);view.setUint32(28,32000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);str(36,'data');view.setUint32(40,samples.length*2,true);for(let i=0;i<samples.length;i++)view.setInt16(44+i*2,Math.round(Math.max(-1,Math.min(1,samples[i]))*32767),true);return new Blob([buffer],{type:'audio/wav'});}
async function convertRecording(blob){const context=new AudioContext();try{const decoded=await context.decodeAudioData(await blob.arrayBuffer());const frames=Math.min(240000,Math.ceil(decoded.duration*16000)),offline=new OfflineAudioContext(1,frames,16000),source=offline.createBufferSource();source.buffer=decoded;source.connect(offline.destination);source.start();const result=await offline.startRendering();preview(wav(result.getChannelData(0)));$('recordTimer').textContent=`${(frames/16000).toFixed(1)} sec · ready to send`;}finally{await context.close();}}
function stopRecording(){if(recorder&&recorder.state!=='inactive')recorder.stop();}
async function recordVoice(){
  if(recorder){stopRecording();return;}
  if(!navigator.mediaDevices?.getUserMedia)throw new Error('Browser recording needs localhost or HTTPS. Open this page on the laptop, or choose a WAV.');
  recordStream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true}});
  try{const chunks=[];recorder=new MediaRecorder(recordStream);const started=Date.now();recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
    recorder.onstop=guard(async()=>{clearInterval(recordClock);recordStream?.getTracks().forEach(t=>t.stop());recordStream=null;recorder=null;$('recordVoice').textContent='● Record voice';$('recordVoice').disabled=true;try{await convertRecording(new Blob(chunks));}finally{$('recordVoice').disabled=false;selection();}});
    recorder.start();$('recordVoice').textContent='■ Stop recording';recordClock=setInterval(()=>{const seconds=(Date.now()-started)/1000;$('recordTimer').textContent=`Recording ${seconds.toFixed(1)} / 15 sec`;if(seconds>=15)stopRecording();},100);selection();
  }catch(e){recordStream.getTracks().forEach(t=>t.stop());recordStream=null;recorder=null;throw e;}
}
$('loginForm').onsubmit=guard(async e=>{e.preventDefault();$('login').disabled=true;try{await post(setup?'/api/auth/setup':'/api/auth/login',{username:$('username').value,password:$('password').value});$('password').value='';notice();await auth();const next=new URLSearchParams(location.search).get('next');if(['/voices','/bench','/queries'].includes(next))location.assign(next);}finally{$('login').disabled=false;}});
$('logout').onclick=guard(async()=>{stopRecording();await post('/api/auth/logout');signedIn=false;await auth();});
$('textTab').onclick=()=>tab('text');$('voiceTab').onclick=()=>tab('voice');$('message').oninput=selection;
$('selectAll').onclick=()=>{for(const unit of fleet.units)if(unit.online)selected.add(unit.id);unitSignature='';units();selection();};
$('recordVoice').onclick=guard(recordVoice);$('voiceFile').onchange=guard(async e=>{const file=e.target.files[0];if(!file)return;if(file.size>4*1024*1024)throw new Error('Choose a WAV smaller than 4 MiB.');preview(file);$('recordTimer').textContent=file.name;});
$('sendPage').onclick=guard(async()=>{sending=true;selection();try{if(mode==='text')await post('/api/pages/text',{units:[...selected],text:$('message').value.trim(),voice_id:$('pageVoice').value||null});else await api('/api/pages/voice',{method:'POST',headers:{'Content-Type':'audio/wav','X-Units':[...selected].join(',')},body:voice});notice('Message queued. Each unit will confirm when playback finishes.');await refresh();}finally{sending=false;selection();}});
$('pairForm').onsubmit=guard(async e=>{e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;try{await post('/api/units/pair',{name:$('unitName').value,url:$('pairUrl').value});notice('Unit paired. It should appear online shortly.');await refresh();}finally{button.disabled=false;}});
guard(auth)();setInterval(()=>{refresh().catch(e=>{$('connection').textContent='Connection interrupted';$('connection').className='badge';});},1500);
window.addEventListener('beforeunload',()=>{recordStream?.getTracks().forEach(t=>t.stop());});
window.addEventListener('focus',()=>{refreshVoices().catch(()=>{});});
