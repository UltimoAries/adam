'use strict';
const $=id=>document.getElementById(id);
let settings;
function el(tag,text='',className=''){const e=document.createElement(tag);e.textContent=text;e.className=className;return e;}
function notice(text=''){$('notice').textContent=text;$('notice').hidden=!text;}
async function api(path,options={}){const r=await fetch(path,options);if(!r.ok){if(r.status===401){$('signIn').hidden=false;$('queryDashboard').hidden=true;}let data;try{data=await r.json();}catch{}throw Error(typeof data?.detail==='string'?data.detail:`Request failed (${r.status}). Check your settings.`);}return r.json();}
const json=(method,body)=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
const guard=fn=>async e=>{try{await fn(e);}catch(error){notice(error.message);}};
function dirty(){$('savedStatus').textContent='Unsaved changes — save settings to apply them.';}
function locations(){
  const selected=$('defaultLocation').value||settings.default_location;$('locations').replaceChildren();$('defaultLocation').replaceChildren();
  settings.locations.forEach((place,index)=>{
    const option=el('option',place.name);option.value=place.name;$('defaultLocation').append(option);
    const item=el('div','','saved-location');item.append(el('strong',place.name),el('small',`${place.latitude.toFixed(4)}, ${place.longitude.toFixed(4)}`));
    const label=el('label','Spoken aliases (separate with commas)'),input=document.createElement('input');input.value=place.aliases.join(', ');input.maxLength=480;input.setAttribute('aria-label',`Aliases for ${place.name}`);input.oninput=()=>{place.aliases=input.value.split(',').map(s=>s.trim()).filter(Boolean);dirty();};label.append(input);
    const remove=el('button','Remove','quiet');remove.type='button';remove.disabled=settings.locations.length===1;remove.onclick=()=>{settings.locations.splice(index,1);locations();dirty();};item.append(label,remove);$('locations').append(item);
  });
  if(settings.locations.some(p=>p.name===selected))$('defaultLocation').value=selected;
}
async function init(){
  const auth=await api('/api/auth/status');$('signIn').hidden=auth.authenticated;$('queryDashboard').hidden=!auth.authenticated;if(!auth.authenticated)return;
  const [config,voices,fleet]=await Promise.all([api('/api/queries/settings'),api('/api/voices'),api('/api/fleet')]);settings=config;
  $('enabled').checked=config.enabled;$('units').value=config.units;$('provider').value=config.provider;locations();$('defaultLocation').value=config.default_location;
  for(const voice of voices.voices){const option=el('option',voice.name);option.value=voice.voice_id;$('replyVoice').append(option);}
  if(config.voice_id&&!voices.voices.some(v=>v.voice_id===config.voice_id)){const option=el('option',`${config.voice_id} (unavailable)`);option.value=config.voice_id;$('replyVoice').append(option);}
  $('replyVoice').value=config.voice_id||'';
  for(const unit of fleet.units.filter(u=>u.online)){const option=el('option',unit.name);option.value=unit.id;$('testUnit').append(option);}
}
$('settingsForm').oninput=dirty;
$('settingsForm').onsubmit=guard(async e=>{e.preventDefault();$('saveSettings').disabled=true;try{const value={...settings,enabled:$('enabled').checked,provider:$('provider').value,units:$('units').value,default_location:$('defaultLocation').value,voice_id:$('replyVoice').value||null};settings=await api('/api/queries/settings',json('PUT',value));$('savedStatus').textContent='Saved. New questions use these settings.';notice();}finally{$('saveSettings').disabled=false;}});
$('searchForm').onsubmit=guard(async e=>{e.preventDefault();$('searchLocations').disabled=true;$('searchResults').textContent='Searching…';try{const places=await api('/api/queries/locations?name='+encodeURIComponent($('searchName').value));$('searchResults').replaceChildren();if(!places.length)$('searchResults').textContent='No matching locations. Try a city and state.';for(const place of places){const button=el('button',`${place.name} · ${place.latitude.toFixed(3)}, ${place.longitude.toFixed(3)}`,'quiet location-result');button.type='button';button.onclick=()=>{if(settings.locations.length>=16){notice('You can save up to 16 locations.');return;}if(settings.locations.some(p=>p.name===place.name)){notice('That location is already saved.');return;}settings.locations.push(place);locations();dirty();button.disabled=true;};$('searchResults').append(button);}}finally{$('searchLocations').disabled=false;}});
$('testForm').onsubmit=guard(async e=>{e.preventDefault();$('testQuery').disabled=true;const output=$('answer');output.hidden=false;output.textContent='Checking weather…';try{const result=await api('/api/queries/test',json('POST',{text:$('question').value,unit_id:$('testUnit').value||null}));output.replaceChildren(el('p',result.text||'No weather answer. Check that weather is enabled and try one of the supported questions.'));if(result.source){const source=el('a',result.source);source.href=result.source_url;source.target='_blank';source.rel='noopener noreferrer';output.append(source,el('small',`Weather time: ${result.valid_time} · Retrieved ${new Date(result.fetched_at*1000).toLocaleTimeString()}`));}if(result.page_id)output.append(el('small','Queued for playback. Follow its confirmation in Message delivery on the dashboard.'));}catch(error){output.textContent=error.message;throw error;}finally{$('testQuery').disabled=false;}});
guard(init)();
