'use strict';
const $=id=>document.getElementById(id);
function notice(text){$('notice').textContent=text;$('notice').hidden=!text;}
function node(tag,text){const n=document.createElement(tag);if(text)n.textContent=text;return n;}
function picker(id,title){
  const box=node('div'),label=node('label',title),select=node('select');select.id=id;select.add(new Option('Select an installed model',''));label.append(select);
  const help=node('p');help.id=id+'-help';help.className='help';help.setAttribute('role','status');
  const details=node('details'),summary=node('summary','Enter a model ID manually'),manual=node('input');manual.id=id+'-manual';manual.maxLength=200;manual.setAttribute('aria-label',title+' manual ID');
  manual.oninput=()=>setModel(id,manual.value.trim());select.onchange=()=>{manual.value=select.value;};details.append(summary,manual);box.append(label,help,details);return box;
}
function setModel(id,value){const select=$(id);if(value&&!Array.from(select.options).some(o=>o.value===value))select.add(new Option(value+' (saved or manual)',value));select.value=value;$(id+'-manual').value=value;}
$('sttPicker').append(picker('stt_model','Speech recognition model'));
$('chatPicker').append(picker('conversation_model','Conversation model'));
async function request(path,body){
  const response=await fetch(path,body);
  if(response.status===401){$('processingForm').hidden=true;$('signIn').hidden=false;$('api_key').value='';throw Error('Sign in to edit model services.');}
  const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Unable to update model services.');return data;
}
const jsonBody=(data,method='POST')=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
const textKeys=['mode','gpu_url','stt_model','conversation_model'];
const numberKeys=['speech_port','conversation_port','stt_timeout','conversation_timeout','cooldown_seconds'];
function settings(){const data={};for(const k of textKeys)data[k]=$(k).value.trim();for(const k of numberKeys)data[k]=Number($(k).value);data.api_key=$('api_key').value;data.clear_key=$('clear_key').checked;data.conversation_enabled=$('conversation_enabled').checked;data.followup_listening=$('followup_listening').checked;data.interrupt_replies=$('interrupt_replies').checked;return data;}
function serviceUrls(){
  const url=new URL($('gpu_url').value.trim());
  if(!['http:','https:'].includes(url.protocol)||url.port||url.username||url.password||url.search||url.hash||url.pathname!=='/')throw Error('Enter the GPU computer address without a port or path.');
  const host=url.protocol+'//'+url.hostname;
  return {speech:host+':'+Number($('speech_port').value)+'/v1',chat:host+':'+Number($('conversation_port').value)};
}
function catalogPayload(id){const urls=serviceUrls();return id==='stt_model'?{url:urls.speech,api_key:$('api_key').value,clear_key:$('clear_key').checked}:{remote_url:urls.chat};}
async function refreshModels(){
  $('refreshModels').disabled=true;
  await Promise.all(['stt_model','conversation_model'].map(async id=>{
    const help=$(id+'-help');help.textContent='Checking installed models…';
    try{
      const payload=catalogPayload(id),snapshot=JSON.stringify(payload);
      const data=await request(id==='stt_model'?'/api/models/stt/catalog':'/api/conversation/models',jsonBody(payload));
      if(JSON.stringify(catalogPayload(id))!==snapshot){help.textContent='Address or key changed. Refresh again.';return;}
      const selected=$(id).value;$(id).replaceChildren(new Option('Select an installed model',''));for(const model of data.models)$(id).add(new Option(model.label,model.id));setModel(id,selected);
      help.textContent=`${data.models.length} installed model${data.models.length===1?'':'s'} found. `+(selected&&!data.models.some(m=>m.id===selected)?'Your selection was kept but is not listed. ':'')+'Choose a model, then save. Refresh does not load or download models.';
    }catch(error){help.textContent=error.message+' Your selection is unchanged.';}
  }));$('refreshModels').disabled=false;
}
function status(data){$('processingStatus').textContent=(data.retry_in_seconds?`GPU unavailable: using Pi, retry in ${data.retry_in_seconds} seconds. `:'')+`Last transcription: ${data.stt_provider||'no requests yet'}. Last conversation: ${data.conversation_provider||'no requests yet'}.`+ (data.mode==='custom'?' Existing service preferences differ; choose a mode and save to combine them.':'');}
function apply(data){
  for(const k of [...textKeys,...numberKeys])if(k.endsWith('_model'))setModel(k,data[k]);else $(k).value=data[k];
  $('api_key').value='';$('clear_key').checked=false;$('conversation_enabled').checked=data.conversation_enabled;$('followup_listening').checked=data.followup_listening;$('interrupt_replies').checked=data.interrupt_replies;
  $('keyStatus').textContent=data.has_key?'Key saved. Leave blank to keep it for the same speech address.':'No key saved.';
  $('fallbackModels').textContent=`Local recognition: ${data.local_stt_model}. Local conversation: ${data.local_conversation_model}.`;
  status(data);$('processingForm').hidden=false;$('signIn').hidden=true;if(data.stt_model)refreshModels();
}
$('refreshModels').onclick=refreshModels;
$('processingForm').onsubmit=async event=>{event.preventDefault();$('saveProcessing').disabled=true;try{apply(await request('/api/processing/settings',jsonBody(settings(),'PUT')));notice('Saved. New requests use these settings.');}catch(error){notice(error.message);}finally{$('saveProcessing').disabled=false;}};
$('testConversation').onclick=async()=>{
  const text=$('conversation-question').value.trim();if(!text){$('conversation-question').focus();return;}$('testConversation').disabled=true;$('conversation-answer').textContent='Thinking…';
  try{const result=await request('/api/conversation/test',jsonBody({text}));$('conversation-answer').textContent=result.text||'Enable and save conversation settings to try an answer.';status(await request('/api/processing/settings'));}catch(error){$('conversation-answer').textContent=error.message;}finally{$('testConversation').disabled=false;}
};
$('previewIntent').onclick=async()=>{
  const text=$('intent-question').value.trim();if(!text){$('intent-question').focus();return;}
  $('previewIntent').disabled=true;$('intent-answer').textContent='Interpreting request…';$('intent-steps').replaceChildren();$('intent-provider').textContent='';
  try{
    const result=await request('/api/home-assistant/intent-preview',jsonBody({text,context_entity_id:$('intent-context').value||null}));
    $('intent-answer').textContent=result.text;
    for(const step of result.steps||[]){const item=document.createElement('li');item.textContent=step.name+' — '+step.label+(step.value===null?'':': '+(Array.isArray(step.value)?step.value.join(' to '):step.value));$('intent-steps').append(item);}
    $('intent-provider').textContent=result.provider?`${result.provider==='remote'?'GPU':'Pi'} · ${result.model} · ${result.elapsed_ms} ms · Preview only`:'';
  }catch(error){$('intent-answer').textContent=error.message;}finally{$('previewIntent').disabled=false;}
};
request('/api/processing/settings').then(async data=>{apply(data);try{const inventory=await request('/api/home-assistant/inventory');for(const row of inventory.devices)if(row.imported&&row.enabled&&row.present)$('intent-context').add(new Option(row.name,row.entity_id));}catch(error){$('intent-answer').textContent='Previous devices could not be loaded: '+error.message;}}).catch(error=>notice(error.message));
setInterval(()=>{if(!document.hidden&&!$('processingForm').hidden)request('/api/processing/settings').then(status).catch(error=>notice(error.message));},10000);
