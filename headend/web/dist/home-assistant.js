'use strict';
const $=id=>document.getElementById(id);
const json=(method,body)=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
let settings,dirty=false,inventory=[],editing=null;
const labels={on_off:'On / off',brightness:'Dimming',color_temperature:'Color temperature',color:'Color',temperature:'Temperature',temperature_range:'Temperature range',mode:'Mode',position:'Position',speed:'Fan speed',volume:'Volume',option:'Options',value:'Numeric setting'};
function node(tag,text='',cls=''){const n=document.createElement(tag);n.textContent=text;n.className=cls;return n;}
function notice(text=''){$('notice').textContent=text;$('notice').hidden=!text;}
async function api(path,options={}){const r=await fetch(path,options);if(!r.ok){if(r.status===401){$('signIn').hidden=false;$('haDashboard').hidden=true;$('accessToken').value='';$('deviceDialog').close();}let data;try{data=await r.json();}catch{}throw Error(typeof data?.detail==='string'?data.detail:`Request failed (${r.status}). Check your entries.`);}return r.json();}
const guard=fn=>async e=>{try{await fn(e);}catch(error){notice(error.message);}};
function requireSaved(){if(dirty)throw Error('Save connection changes first.');}
function apply(config){settings=config;dirty=false;$('serverAddress').value=config.url;$('enabled').checked=config.enabled;$('autoImport').checked=config.auto_import;$('accessToken').value='';$('tokenStatus').textContent=config.has_token?'Token saved. Leave blank to keep it.':'No token saved.';$('savedStatus').textContent='Saved connection is in use.';}
function showInventory(data){inventory=data.devices;$('inventorySummary').textContent=`${inventory.filter(r=>r.imported).length} imported · ${inventory.length} discovered${data.last_refresh?' · Updated '+new Date(data.last_refresh*1000).toLocaleTimeString():''}${data.error?' · '+data.error:''}${!data.enabled?' · Control disabled':''}`;renderInventory();}
function deviceOptions(row,extra={}){return {name:row.custom_name,aliases:row.aliases,use_default_name:row.use_default_name,imported:row.imported,enabled:row.enabled,...extra};}
async function importDevice(row){requireSaved();await api('/api/home-assistant/inventory/'+row.entity_id,json('PATCH',deviceOptions(row,{imported:true,enabled:true})));showInventory(await api('/api/home-assistant/inventory'));}
function manage(row){editing=row;$('mapping').textContent='Home Assistant entity: '+row.entity_id;$('deviceName').value=row.custom_name;$('deviceName').placeholder=row.default_name;$('defaultName').textContent='Home Assistant name: '+row.default_name+'. Leave the Adam name blank to follow it automatically.';$('deviceAliases').value=row.aliases.join(', ');$('useDefaultName').checked=row.use_default_name;$('deviceEnabled').checked=row.enabled;$('deviceError').textContent='';$('removeDevice').hidden=!row.imported;$('deviceDialog').showModal();}
function sample(row){const key=row.controls[0]?.key;if(row.capabilities.includes('brightness'))return `Set ${row.name} to 40 percent`;if(key==='on')return `Turn on ${row.name}`;if(['open','lock','activate','press'].includes(key))return `${key[0].toUpperCase()+key.slice(1)} ${row.name}`;return `What is the status of ${row.name}?`;}
async function runControl(row,action,value,result,button){requireSaved();button.disabled=true;result.textContent='Sending…';try{const answer=await api(`/api/home-assistant/inventory/${row.entity_id}/actions`,json('POST',{action,value}));result.textContent=answer.text;if(answer.state){row.state=answer.state;result.closest('.device-card').querySelector('.badge').textContent=answer.state;}notice();}catch(error){result.textContent=error.message;}finally{button.disabled=false;}}
function controlForm(row,c,result){
  const form=node('form','','device-control'),label=node('label',c.label),button=node('button','Set','secondary');button.type='submit';
  let input=document.createElement(c.kind==='select'?'select':'input'),second;
  if(c.kind==='select'){for(const option of c.options)input.add(new Option(option,option));}
  else if(c.kind==='color'){input.type='color';input.value='#ffffff';}
  else {input.type='number';input.min=c.min;input.max=c.max;input.step=c.step;input.required=true;input.placeholder=`${c.min}–${c.max}`;}
  input.setAttribute('aria-label',c.label+' for '+row.name);
  if(c.kind==='range'){second=input.cloneNode();input.setAttribute('aria-label','Low temperature for '+row.name);second.setAttribute('aria-label','High temperature for '+row.name);const pair=node('div','','range-inputs');pair.append(input,second);label.append(pair);}else label.append(input);
  form.append(label,button);form.onsubmit=guard(async e=>{e.preventDefault();let value=c.kind==='range'?[Number(input.value),Number(second.value)]:c.kind==='number'?Number(input.value):input.value;await runControl(row,c.key,value,result,button);});return form;
}
function renderInventory(){
  const query=$('deviceSearch').value.toLocaleLowerCase(),filter=$('deviceFilter').value;
  const rows=inventory.filter(r=>(filter==='all'||filter==='imported'&&r.imported||filter==='suggested'&&r.suggested||filter==='missing'&&!r.present)&&[r.name,r.default_name,r.entity_id,...r.aliases].join(' ').toLocaleLowerCase().includes(query));
  $('inventory').replaceChildren();
  if(!rows.length)$('inventory').append(node('div',inventory.length?'No matching devices. Choose Main controls or All entities to import devices.':'Refresh from Home Assistant after saving your connection.','panel help'));
  for(const row of rows){
    const card=node('article','','device-card');card.dataset.entity=row.entity_id;
    const top=node('div','','device-top');top.append(node('h3',row.name),node('span',!row.present?'Missing':row.state,'badge'));card.append(top,node('p',row.entity_id,'mapping'));
    const caps=node('div','','capabilities');for(const c of row.capabilities)caps.append(node('span',labels[c]||c.replaceAll('_',' ')));if(!row.controls.length)caps.append(node('span','Read-only status'));card.append(caps);
    if(row.conflicts.length)card.append(node('p','Shared name: '+row.conflicts.join(', ')+'. Use a unique name or alias before speaking commands.','device-conflict'));
    const manageButton=node('button',row.imported?'Manage name & aliases':'Import into Adam','secondary');manageButton.onclick=guard(async()=>row.imported?manage(row):await importDevice(row));card.append(manageButton);
    if(row.imported){
      card.append(node('p',!row.enabled?'Commands disabled':!row.present?'Not found on the last refresh. Mapping retained.':`Try “${sample(row)}”`,'help'));
      if(settings.enabled&&row.enabled&&row.present){
        const result=node('p','','device-result');result.setAttribute('role','status');
        const actions=node('div','','device-controls'),status=node('button','Read status','quiet');status.onclick=guard(()=>runControl(row,'status',null,result,status));actions.append(status);
        for(const c of row.controls.filter(c=>!c.kind)){const button=node('button',c.label,'secondary');button.disabled=row.state==='unavailable';button.onclick=guard(()=>runControl(row,c.key,null,result,button));actions.append(button);}
        card.append(actions);
        const valued=row.controls.filter(c=>c.kind);if(valued.length){const details=node('details');details.append(node('summary','Adjust settings'));for(const c of valued)details.append(controlForm(row,c,result));card.append(details);}
        card.append(result);
      }
    }
    $('inventory').append(card);
  }
}
async function loadEntities(){requireSaved();$('loadEntities').disabled=true;try{showInventory(await api('/api/home-assistant/inventory/refresh',json('POST',{})));notice();}finally{$('loadEntities').disabled=false;}}
async function loadUnits(){const fleet=await api('/api/fleet');$('adamUnits').replaceChildren();$('replyUnit').replaceChildren(new Option('Show answer here',''));for(const unit of fleet.units){if(unit.online)$('replyUnit').add(new Option(unit.name,unit.id));const item=node('div','','adam-unit'),form=node('form'),label=node('label','Speaker name'),input=document.createElement('input'),save=node('button','Rename','secondary');input.value=unit.name;input.required=true;input.maxLength=48;input.setAttribute('aria-label','Name for '+unit.name);save.type='submit';label.append(input);form.append(label,save);form.onsubmit=guard(async e=>{e.preventDefault();await api('/api/units/'+unit.id,json('PATCH',{name:input.value.trim()}));await loadUnits();notice('Speaker renamed. Its connection and screen settings are preserved.');});item.append(form,node('small',`${unit.online?'Online':'Offline'} · ${unit.firmware||'Awaiting connection'} · Unit ID: ${unit.id}`),node('small','Speaker · Microphone · Touch display · Clock & weather'),node('small','Wake model: '+unit.wake_model));$('adamUnits').append(item);}if(!fleet.units.length)$('adamUnits').textContent='Pair an Adam unit from Devices & paging.';}
$('settingsForm').oninput=()=>{dirty=true;$('savedStatus').textContent='Unsaved connection changes.';};
$('settingsForm').onsubmit=guard(async e=>{e.preventDefault();$('saveSettings').disabled=true;try{const value={url:$('serverAddress').value.trim(),enabled:$('enabled').checked,auto_import:$('autoImport').checked,entity_id:settings.entity_id,name:settings.name};if($('accessToken').value)value.token=$('accessToken').value.trim();apply(await api('/api/home-assistant/settings',json('PUT',value)));showInventory(await api('/api/home-assistant/inventory'));if(settings.has_token)await loadEntities();notice();}finally{$('accessToken').value='';$('saveSettings').disabled=false;}});
$('checkConnection').onclick=guard(async()=>{requireSaved();$('checkConnection').disabled=true;try{const result=await api('/api/home-assistant/check',json('POST',{}));$('connectionStatus').textContent=`Connected to Home Assistant ${result.version}.`;notice();}finally{$('checkConnection').disabled=false;}});
$('loadEntities').onclick=guard(loadEntities);
$('forgetToken').onclick=guard(async()=>{apply(await api('/api/home-assistant/settings',json('PUT',{url:settings.url,name:settings.name,entity_id:settings.entity_id,enabled:false,auto_import:false,token:''})));showInventory(await api('/api/home-assistant/inventory'));notice('Token removed. Control disabled.');});
$('deviceSearch').oninput=renderInventory;$('deviceFilter').onchange=renderInventory;
$('closeDevice').onclick=()=>$('deviceDialog').close();
$('deviceForm').onsubmit=async e=>{e.preventDefault();$('saveDevice').disabled=true;try{requireSaved();await api('/api/home-assistant/inventory/'+editing.entity_id,json('PATCH',deviceOptions(editing,{name:$('deviceName').value.trim(),aliases:$('deviceAliases').value.split(',').map(v=>v.trim()).filter(Boolean),use_default_name:$('useDefaultName').checked,enabled:$('deviceEnabled').checked,imported:true})));showInventory(await api('/api/home-assistant/inventory'));$('deviceDialog').close();}catch(error){$('deviceError').textContent=error.message;}finally{$('saveDevice').disabled=false;}};
$('removeDevice').onclick=async()=>{try{requireSaved();await api('/api/home-assistant/inventory/'+editing.entity_id,json('PATCH',deviceOptions(editing,{imported:false})));showInventory(await api('/api/home-assistant/inventory'));$('deviceDialog').close();}catch(error){$('deviceError').textContent=error.message;}};
$('commandForm').onsubmit=guard(async e=>{e.preventDefault();requireSaved();$('sendCommand').disabled=true;$('answer').hidden=false;$('answer').textContent='Checking Home Assistant…';try{const result=await api('/api/home-assistant/commands',json('POST',{text:$('command').value,unit_id:$('replyUnit').value||null}));$('answer').textContent=result.text||'No matching command. Use an imported device’s name and an action shown on its card.';if(result.page_id)$('answer').textContent+=' Reply queued for playback.';if(result.delivery_error)$('answer').textContent+=' '+result.delivery_error;notice();}finally{$('sendCommand').disabled=false;}});
guard(async()=>{const auth=await api('/api/auth/status');$('signIn').hidden=auth.authenticated;$('haDashboard').hidden=!auth.authenticated;if(!auth.authenticated)return;apply(await api('/api/home-assistant/settings'));showInventory(await api('/api/home-assistant/inventory'));await loadUnits();if(settings.has_token)await loadEntities();})();
setInterval(async()=>{if(!settings||$('haDashboard').hidden||document.hidden||dirty||$('deviceDialog').open||$('inventory').contains(document.activeElement)||$('inventory').querySelector('details[open]'))return;try{showInventory(await api('/api/home-assistant/inventory'));}catch(error){notice(error.message);}},30000);
