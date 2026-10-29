'use strict';
const $=id=>document.getElementById(id);
let library=null,packageFile=null,importing=false,generating=false,audioUrl=null;
const node=(tag,text='',className='')=>{const n=document.createElement(tag);n.textContent=text;n.className=className;return n;};
function notice(message=''){$('notice').textContent=message;$('notice').hidden=!message;}
async function api(path,options={}){const response=await fetch(path,options);if(!response.ok){let message;try{message=(await response.json()).detail;}catch{}if(response.status===401){$('voiceDashboard').hidden=true;$('signIn').hidden=false;}throw new Error(typeof message==='string'?message:`Request failed (${response.status})`);}return response.json();}
const post=(path,body)=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
const guard=fn=>async(...args)=>{try{await fn(...args);}catch(error){notice(error.message);}};
function controls(){$('choosePackage').disabled=importing||generating;$('previewVoice').disabled=importing||generating;$('previewText').disabled=importing||generating;$('importPackage').disabled=!packageFile||importing||generating;$('generatePreview').disabled=importing||generating||!$('previewVoice').value;document.querySelectorAll('.voice-actions button').forEach(button=>{button.disabled=importing||generating||button.dataset.locked==='true';});}
async function refresh(){
  library=await api('/api/voices');const previous=$('previewVoice').value;
  $('previewVoice').replaceChildren();for(const voice of library.voices)if(voice.ready)$('previewVoice').add(new Option(voice.name+' · '+voice.version,voice.voice_id));
  $('previewVoice').value=library.voices.some(v=>v.voice_id===previous&&v.ready)?previous:library.default_voice;
  const active=library.voices.find(v=>v.voice_id===library.default_voice);
  $('defaultDescription').textContent=active?.ready?`Default: ${active.name} · ${active.version}`:'Install the basic voice or import a voice package to begin.';
  $('voiceCount').textContent=`${library.voices.filter(v=>v.ready).length} voices`;$('voiceList').replaceChildren();
  for(const voice of library.voices){
    const card=node('article','',`voice-card ${voice.active?'active':''}`),heading=node('div','','section-title');
    heading.append(node('h3',voice.name),node('span',voice.active?'Default':voice.builtin?'Included':'Imported',`badge ${voice.active?'online':''}`));
    card.append(heading,node('p',voice.description,'description'),node('p',`${voice.language} · Piper ONNX · v${voice.version} · ${(voice.model_bytes/1048576).toFixed(1)} MiB`,'voice-meta'));
    if(voice.author)card.append(node('p','By '+voice.author,'voice-meta'));
    if(!voice.ready)card.append(node('p','The basic model is missing. See the setup command in the format guide.','help'));
    const actions=node('div','','voice-actions');
    const preview=node('button','Preview','secondary');preview.dataset.locked=String(!voice.ready);preview.onclick=guard(async()=>{$('previewVoice').value=voice.voice_id;await generate();$('previewPlayer').scrollIntoView({behavior:'smooth',block:'center'});});
    const activate=node('button',voice.active?'Current default':'Use as default',voice.active?'quiet':'primary');activate.dataset.locked=String(voice.active||!voice.ready);activate.onclick=guard(async()=>{await post('/api/voices/default',{voice_id:voice.voice_id});await refresh();notice(`${voice.name} is now the default for typed pages.`);});actions.append(preview,activate);
    if(!voice.builtin){const remove=node('button','Remove','quiet danger');remove.dataset.locked=String(voice.active);remove.title=voice.active?'Choose another default before removing this voice':'';remove.onclick=guard(async()=>{await api('/api/voices/'+encodeURIComponent(voice.voice_id),{method:'DELETE'});await refresh();notice('Voice removed.');});actions.append(remove);}
    card.append(actions);
    if(voice.license){const details=node('details');details.append(node('summary','Source & license'),node('p',voice.license.name),node('p',voice.license.attribution));if(voice.license.source_url){const link=node('a','View source / model card ↗');link.href=voice.license.source_url;link.target='_blank';link.rel='noopener noreferrer';details.append(link);}card.append(details);}
    $('voiceList').append(card);
  }controls();
}
async function generate(){
  generating=true;controls();$('previewState').textContent='Generating locally…';notice();const start=performance.now();
  try{const response=await fetch('/api/voices/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({voice_id:$('previewVoice').value,text:$('previewText').value.trim()})});if(!response.ok){let message;try{message=(await response.json()).detail;}catch{}throw new Error(typeof message==='string'?message:'Could not generate this preview.');}const blob=await response.blob();if(audioUrl)URL.revokeObjectURL(audioUrl);audioUrl=URL.createObjectURL(blob);$('previewPlayer').src=audioUrl;$('previewPlayer').hidden=false;$('previewDetails').textContent=`Preview ready · generated locally in ${((performance.now()-start)/1000).toFixed(1)} seconds.`;$('previewDetails').hidden=false;$('previewState').textContent='Ready to play';}
  finally{generating=false;if($('previewState').textContent==='Generating locally…')$('previewState').textContent='Local preview';controls();}
}
function upload(file){return new Promise((resolve,reject)=>{const request=new XMLHttpRequest();request.open('POST','/api/voices/import');request.setRequestHeader('Content-Type','application/zip');request.upload.onprogress=event=>{if(event.lengthComputable){$('uploadProgress').value=100*event.loaded/event.total;if(event.loaded===event.total)$('importState').textContent='Checking the package and testing its voice. This may take up to 35 seconds…';}};request.onload=()=>{let result;try{result=JSON.parse(request.responseText);}catch{return reject(new Error('The server returned an invalid import response.'));}if(request.status>=200&&request.status<300)resolve(result);else reject(new Error(typeof result.detail==='string'?result.detail:'Voice import failed.'));};request.onerror=()=>reject(new Error('Upload interrupted. Your current voice remains selected.'));request.send(file);});}
$('choosePackage').onclick=()=>$('packageInput').click();
$('packageInput').onchange=()=>{packageFile=$('packageInput').files[0]||null;if(packageFile&&packageFile.size>160*1024*1024){notice('Choose a voice ZIP smaller than 160 MiB.');packageFile=null;}else notice();$('packageName').textContent=packageFile?`${packageFile.name} · ${(packageFile.size/1048576).toFixed(1)} MiB`:'No package selected';controls();};
$('importPackage').onclick=guard(async()=>{if(!packageFile)return;importing=true;controls();$('uploadProgress').hidden=false;$('uploadProgress').value=0;$('importState').textContent='Uploading your voice package…';notice();try{const result=await upload(packageFile);$('importState').textContent=`${result.name} passed validation. Preview it, then choose Use as default when ready.`;packageFile=null;$('packageInput').value='';$('packageName').textContent='No package selected';await refresh();$('previewVoice').value=result.voice_id;notice('Voice installed. Your current default has stayed selected.');}catch(error){$('importState').textContent='Import failed. Your current default remains selected.';throw error;}finally{importing=false;$('uploadProgress').hidden=true;controls();}});
$('generatePreview').onclick=guard(generate);
guard(async()=>{const state=await api('/api/auth/status');$('signIn').hidden=state.authenticated;$('voiceDashboard').hidden=!state.authenticated;if(state.authenticated)await refresh();})();
window.addEventListener('beforeunload',()=>{if(audioUrl)URL.revokeObjectURL(audioUrl);});
