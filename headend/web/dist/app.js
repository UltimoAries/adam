const $ = id => document.getElementById(id);
let clips = [], selected = null, currentJob = null, pollingRecord = false, deviceBusy = false;
let lastText = '', jobGeneration = 0;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if(response.status===401){window.location.assign('/');throw new Error('Sign in to the head-end.');}
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try { const body = await response.json(); message = typeof body.detail === 'string' ? body.detail : 'Check the requested values and try again.'; } catch {}
    throw new Error(message);
  }
  return response.json();
}
function post(path, body) { return api(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body || {})}); }
async function usbPost(path, body) {
  const end=Date.now()+5000;
  while(deviceBusy && Date.now()<end) await sleep(50);
  if(deviceBusy) throw new Error('USB is busy. Wait a moment and try again.');
  deviceBusy=true;
  try {return await post(path,body);} finally {deviceBusy=false;}
}
function notice(message, success = false) { $('notice').textContent = message; $('notice').className = success ? 'success' : ''; $('notice').hidden = !message; }
function fmtDb(value) { return value === null ? 'Silence' : `${value.toFixed(1)} dBFS`; }
function renderList() {
  $('clipCount').textContent = clips.length;
  $('clipList').replaceChildren();
  if (!clips.length) { const p = document.createElement('p'); p.className = 'empty-list'; p.textContent = 'Your recordings will appear here.'; $('clipList').append(p); }
  clips.forEach(clip => {
    const button = document.createElement('button'); button.className = 'clip-item' + (selected?.id === clip.id ? ' selected' : '');
    const icon = document.createElement('span'); icon.className = 'clip-icon'; icon.textContent = '≋'; icon.setAttribute('aria-hidden','true');
    const title = document.createElement('strong'); title.textContent = clip.name;
    const meta = document.createElement('small'); meta.textContent = `${clip.seconds.toFixed(1)}s · ${clip.channels === 2 ? 'stereo' : 'mono'} · ${clip.source === 'stream' ? 'Wi-Fi capture' : clip.source === 'device' ? 'USB capture' : 'WAV upload'}`;
    button.append(icon, title, meta); button.onclick = () => selectClip(clip); $('clipList').append(button);
  });
}
async function refreshClips(selectId = null) {
  clips = await api('/api/clips');
  if (selectId) selectClip(clips.find(c => c.id === selectId));
  else if (!selected && clips.length) selectClip(clips[0]);
  renderList();
}
function selectClip(clip) {
  if (!clip) return;
  selected = clip;
  $('clipTitle').textContent = clip.name;
  $('clipMeta').textContent = `${clip.seconds.toFixed(2)} sec · ${(clip.rate/1000).toFixed(1)} kHz · ${clip.bits}-bit PCM · ${clip.channels === 2 ? 'two channels' : 'one channel'}`;
  $('channel').replaceChildren(); $('sttChannel').replaceChildren();
  if (clip.channels === 2) $('channel').add(new Option('Original stereo', '0'));
  for (let ch=1; ch<=clip.channels; ch++) { $('channel').add(new Option(`Microphone ${ch}`, String(ch))); $('sttChannel').add(new Option(`Microphone ${ch}`, String(ch))); }
  $('gain').value = '0';
  $('download').href = `/api/clips/${clip.id}/audio`; $('download').download = clip.name; $('download').hidden = false;
  $('deleteClip').disabled = false;
  $('transcript').textContent = 'Ready to transcribe this clip.'; $('transcript').className = 'transcript empty';
  $('copyText').hidden = true; lastText = '';
  updatePlayer(); renderList();
}
function updatePlayer() {
  if (!selected) return;
  $('player').src = `/api/clips/${selected.id}/audio?channel=${$('channel').value}&gain_db=${$('gain').value}`;
  drawWaveform();
  const level = selected.levels[Math.max(1,Number($('channel').value))-1];
  const peakDb = level.peak ? 20*Math.log10(level.peak/32768) : null;
  const values = [fmtDb(level.dbfs),fmtDb(peakDb),String(level.clipped)];
  $('levels').querySelectorAll('strong').forEach((element, index) => element.textContent = values[index]);
}
function drawWaveform() {
  const canvas = $('waveform'), ctx = canvas.getContext('2d'), width = canvas.width, height = canvas.height;
  ctx.clearRect(0,0,width,height);
  ctx.strokeStyle = '#263843'; ctx.lineWidth = 1;
  [height*.25,height*.5,height*.75].forEach(y => { ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(width,y);ctx.stroke(); });
  if (!selected) return;
  const channel = Math.max(1,Number($('channel').value))-1, peaks = selected.levels[channel].waveform;
  // Scale only the drawing so quiet signals can be inspected. Audio is unchanged.
  const maximum = Math.max(...peaks, 0.001), scale = 0.83/maximum;
  ctx.strokeStyle = '#75e0d0'; ctx.lineWidth = Math.max(1, width/peaks.length*.47);
  peaks.forEach((peak,i) => {const x=(i+.5)*width/peaks.length, h=Math.max(1, peak*scale*height/2);ctx.beginPath();ctx.moveTo(x,height/2-h);ctx.lineTo(x,height/2+h);ctx.stroke();});
  const labelSize=Math.round(12*width/Math.max(1,canvas.clientWidth));
  ctx.fillStyle='#8ea6b2';ctx.font=`${labelSize}px Segoe UI`;ctx.fillText('Waveform · scaled to inspect',16,labelSize+8);
}
async function uploadFiles(files) {
  for (const file of files) {
    notice(`Uploading ${file.name}…`);
    const clip = await api('/api/clips', {method:'POST',headers:{'Content-Type':'audio/wav','X-Filename':encodeURIComponent(file.name)},body:file});
    await refreshClips(clip.id); notice('Clip saved locally.',true);
    if ($('autoText').checked) await transcribeClip();
  }
}
async function health() {
  try {const state=await api('/api/health');$('serverState').textContent='Local server connected';$('modelState').textContent=`${state.model} · ${state.model_state === 'ready' ? 'ready' : state.model_state === 'loading' ? 'loading…' : state.model_state === 'available' ? 'loads on first use' : state.model_state}`;}
  catch {$('serverState').textContent='Server unavailable';}
}
async function deviceStatus(showErrors = false) {
  if (deviceBusy) return null;
  deviceBusy = true;
  try {
    const state = await api('/api/device');
    $('deviceState').textContent = state.mic.recording === '1' ? `Recording · ${(Number(state.mic.frames)/16000).toFixed(1)} sec captured` : state.audio.playing === '1' ? 'Playing through the box speaker' : 'Connected · microphone ready';
    $('deviceDetails').textContent = `${state.port} · input ${state.mic.gain_db} dB · speaker volume ${state.audio.volume}`;
    return state;
  } catch(error) {$('deviceState').textContent='USB unavailable'; if(showErrors) notice(error.message); return null;}
  finally {deviceBusy=false;}
}
async function fetchLatest() {
  notice('Fetching the box’s completed recording…');
  const clip=await usbPost('/api/device/fetch'); await refreshClips(clip.id); notice('Device clip saved locally. Use the player below to listen.',true);
  if($('autoText').checked) await transcribeClip();
}
async function record() {
  if(pollingRecord) return;
  await usbPost('/api/device/record',{seconds:8}); pollingRecord=true; $('record').disabled=true;
  $('recordState').textContent='Speak now while the box records.'; notice('Recording on the box for 8 seconds.');
  try {
    const end=Date.now()+20000;
    while(Date.now()<end) {await sleep(650);const state=await deviceStatus();if(state?.mic.recording==='0'){await fetchLatest();return;}}
    throw new Error('The recording status timed out. Try Fetch latest clip.');
  } finally {pollingRecord=false;$('record').disabled=false;$('recordState').textContent='Recording starts only when you press Record.';}
}
async function transcribeClip() {
  if(!selected) throw new Error('Choose a clip first.');
  if(currentJob) throw new Error('The recognizer is already working. Wait for that clip to finish.');
  const clipId=selected.id, generation=++jobGeneration;
  $('transcript').textContent='Transcribing locally…';$('transcript').className='transcript working';$('jobTiming').textContent='The model loads once, then stays warm.';
  $('copyText').hidden=true;$('transcribe').disabled=true;
  try {
    currentJob=await post(`/api/clips/${clipId}/transcriptions`,{channel:Number($('sttChannel').value),boost:$('boost').checked});
    $('cancelJob').hidden=false;
    for(;;) {
      await sleep(350); const job=await api(`/api/jobs/${currentJob.id}`);
      if(generation!==jobGeneration) return;
      if(['queued','running','cancelling'].includes(job.status)) {await health();continue;}
      if(selected?.id!==clipId) {notice('Transcription finished for the previous clip. Select it and transcribe again to view its result.');return;}
      if(job.status==='error') throw new Error(job.error);
      if(job.status==='cancelled') {$('transcript').textContent='Transcription cancelled.';$('transcript').className='transcript empty';return;}
      lastText=job.text;
      $('transcript').textContent=job.text || 'No speech detected. Listen to the recording and compare the microphone channels.';
      $('transcript').className=job.text?'transcript':'transcript empty';
      $('jobTiming').textContent=`${(job.inference_ms/1000).toFixed(2)} sec recognition · ${(job.elapsed_ms/1000).toFixed(2)} sec total · input boost ${job.input_gain.toFixed(1)}×`;
      $('copyText').hidden=!job.text; return;
    }
  } catch(error) {$('transcript').textContent=error.message;$('transcript').className='transcript empty';throw error;}
  finally {if(generation===jobGeneration){currentJob=null;$('cancelJob').hidden=true;$('transcribe').disabled=false;await health();}}
}
const guarded = fn => async (...args) => {try {await fn(...args);} catch(error){notice(error.message);}};
$('record').onclick=guarded(record);$('fetchClip').onclick=guarded(fetchLatest);
$('stop').onclick=guarded(async()=>{await usbPost('/api/device/stop');await deviceStatus();notice('Recording and playback stopped.',true);});
$('replayBox').onclick=guarded(async()=>{await usbPost('/api/device/replay',{channel:Number($('sttChannel').value || 1)});notice('Playing the box’s current recording through its speaker.',true);await deviceStatus();});
$('refreshDevice').onclick=()=>deviceStatus(true);
$('channel').onchange=updatePlayer;$('gain').onchange=updatePlayer;
$('transcribe').onclick=guarded(transcribeClip);
$('cancelJob').onclick=guarded(async()=>{if(currentJob) await post(`/api/jobs/${currentJob.id}/cancel`);});
$('copyText').onclick=guarded(async()=>{await navigator.clipboard.writeText(lastText);notice('Transcript copied.',true);});
$('deleteClip').onclick=guarded(async()=>{if(!selected)return;await api(`/api/clips/${selected.id}`,{method:'DELETE'});selected=null;$('player').removeAttribute('src');await refreshClips();if(!clips.length){$('clipTitle').textContent='Choose a clip to begin';$('clipMeta').textContent='Fetch a recording from the box or upload a WAV file.';$('download').hidden=true;$('deleteClip').disabled=true;drawWaveform();}notice('Selected test clip deleted.',true);});
$('fileInput').onchange=guarded(async event=>{await uploadFiles(event.target.files);event.target.value='';});
['dragenter','dragover'].forEach(name=>$('dropzone').addEventListener(name,event=>{event.preventDefault();$('dropzone').classList.add('drag');}));
['dragleave','drop'].forEach(name=>$('dropzone').addEventListener(name,event=>{event.preventDefault();$('dropzone').classList.remove('drag');}));
$('dropzone').addEventListener('drop',guarded(event=>uploadFiles(event.dataTransfer.files)));
$('dropzone').addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();$('fileInput').click();}});
$('player').addEventListener('error',()=>{if(selected)notice('The audio player could not open this clip. Try Original gain or download the WAV.');});
guarded(async()=>{await health();await refreshClips();await deviceStatus();drawWaveform();})();
setInterval(health,7000);setInterval(()=>{if(!pollingRecord)deviceStatus();},5000);
window.addEventListener('resize',drawWaveform);
