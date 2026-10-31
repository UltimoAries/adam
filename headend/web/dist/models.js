'use strict';
const $=id=>document.getElementById(id);
const kinds=['stt','tts'];
function notice(text){$('notice').textContent=text;$('notice').hidden=!text;}
function node(tag,text){const item=document.createElement(tag);if(text)item.textContent=text;return item;}
function field(kind,key,label,type='text'){
  const wrapper=node('label',label),input=node('input');input.id=kind+'-'+key;input.type=type;
  if(type==='password'){input.autocomplete='new-password';input.maxLength=512;}
  wrapper.append(input);return wrapper;
}
for(const kind of kinds){
  const section=node('fieldset');section.className='panel';section.append(node('legend',kind==='stt'?'Speech recognition':'Speaking voice'));
  const label=node('label','Where to run'),select=node('select');select.id=kind+'-mode';
  select.add(new Option('Local only','local'));select.add(new Option('Prefer remote, fall back locally','prefer_remote'));label.append(select);section.append(label);
  section.append(field(kind,'url','Service base URL','url'),field(kind,'model','Model ID'));
  if(kind==='tts')section.append(field(kind,'voice','Voice ID'));
  section.append(field(kind,'api_key','API key (optional)','password'));
  const keyStatus=node('p');keyStatus.id=kind+'-keyStatus';keyStatus.className='help';section.append(keyStatus);
  const clear=field(kind,'clear_key','Remove saved API key','checkbox');clear.className='inline';section.append(clear);
  section.append(field(kind,'timeout_seconds','Remote timeout (seconds)','number'),field(kind,'cooldown_seconds','Retry remote after (seconds)','number'));
  const status=node('p');status.id=kind+'-status';status.className='help model-status';status.setAttribute('role','status');section.append(status);
  $('profiles').append(section);
  $(kind+'-url').placeholder='http://gpu-computer:8000/v1';$(kind+'-url').maxLength=250;$(kind+'-model').maxLength=200;
  $(kind+'-timeout_seconds').min=1;$(kind+'-timeout_seconds').max=30;$(kind+'-timeout_seconds').step='0.5';
  $(kind+'-cooldown_seconds').min=5;$(kind+'-cooldown_seconds').max=300;
}
async function api(options){
  const response=await fetch('/api/models/settings',options);
  if(response.status===401){$('modelForm').hidden=true;$('signIn').hidden=false;for(const k of kinds)$(k+'-api_key').value='';throw Error('Sign in to edit model services.');}
  const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Unable to update model services.');return data;
}
function status(data){for(const k of kinds){const p=data[k];$(k+'-status').textContent=(p.last_provider?'Last request: '+p.last_provider.replace('_',' ')+'. ':'No speech requests since startup or settings change. ')+(p.last_error?p.last_error+'. ':'')+(p.retry_in_seconds?'Remote retry in '+p.retry_in_seconds+' seconds.':'');}}
function apply(data){
  for(const k of kinds){const p=data[k];for(const key of ['mode','url','model','timeout_seconds','cooldown_seconds'])$(k+'-'+key).value=p[key];if(k==='tts')$(k+'-voice').value=p.voice;$(k+'-api_key').value='';$(k+'-clear_key').checked=false;$(k+'-keyStatus').textContent=p.has_key?'Key saved. Leave blank to keep it for this URL.':'No key saved.';}
  status(data);$('modelForm').hidden=false;$('signIn').hidden=true;
}
$('modelForm').onsubmit=async event=>{
  event.preventDefault();$('saveModels').disabled=true;
  try{const data={};for(const k of kinds){data[k]={};for(const key of ['mode','url','model','api_key'])data[k][key]=$(k+'-'+key).value;for(const key of ['timeout_seconds','cooldown_seconds'])data[k][key]=Number($(k+'-'+key).value);data[k].clear_key=$(k+'-clear_key').checked;if(k==='tts')data[k].voice=$(k+'-voice').value;}
    apply(await api({method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}));notice('Saved. New requests use these settings.');
  }catch(error){notice(error.message);}finally{$('saveModels').disabled=false;}
};
api().then(apply).catch(error=>notice(error.message));
setInterval(()=>{if(!document.hidden&&!$('modelForm').hidden)api().then(status).catch(error=>notice(error.message));},10000);

const conversationKeys=['remote_url','remote_model','local_url','local_model','remote_timeout_seconds','local_timeout_seconds','cooldown_seconds'];
async function conversationApi(path='/settings',options){
  const response=await fetch('/api/conversation'+path,options);
  if(response.status===401){$('conversationForm').hidden=true;$('signIn').hidden=false;throw Error('Sign in to edit conversation settings.');}
  const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Check the conversation settings.');return data;
}
function conversationStatus(data){
  $('conversation-status').textContent=(data.last_provider?'Last answer: '+data.last_provider+'. ':'No model answers since startup or settings change. ')+(data.last_error?data.last_error+'. ':'')+(data.retry_in_seconds?'Retrying the preferred computer in '+data.retry_in_seconds+' seconds.':'');
}
function applyConversation(data){
  for(const key of conversationKeys)$('conversation-'+key).value=data[key];
  $('conversation-enabled').checked=data.enabled;$('conversationForm').hidden=false;conversationStatus(data);
}
$('conversationForm').onsubmit=async event=>{
  event.preventDefault();$('saveConversation').disabled=true;
  try{const data={enabled:$('conversation-enabled').checked};for(const key of conversationKeys)data[key]=key.endsWith('_seconds')?Number($('conversation-'+key).value):$('conversation-'+key).value;
    applyConversation(await conversationApi('/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}));notice('Conversation settings saved.');
  }catch(error){notice(error.message);}finally{$('saveConversation').disabled=false;}
};
$('testConversation').onclick=async()=>{
  const text=$('conversation-question').value.trim();if(!text){$('conversation-question').focus();return;}
  $('testConversation').disabled=true;$('conversation-answer').textContent='Thinking…';
  try{const result=await conversationApi('/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});$('conversation-answer').textContent=result.text||'Enable and save conversation settings to try an answer.';conversationStatus(await conversationApi());
  }catch(error){$('conversation-answer').textContent=error.message;}finally{$('testConversation').disabled=false;}
};
conversationApi().then(applyConversation).catch(error=>notice(error.message));
setInterval(()=>{if(!document.hidden&&!$('conversationForm').hidden)conversationApi().then(conversationStatus).catch(error=>notice(error.message));},10000);
