// Map placement is local metadata. These requests never control a light.
const get = id => document.getElementById(id);
const dashboard = get('dashboard');
const listeners = new Set();
let data = null, timer = null, reading = null, generation = 0, saving = false;
let selectedRoom = '', dragging = false, drawn = '';
const active = () => !dashboard.hidden && !document.hidden;
const roomText = room => room.state === 'unassigned' ? 'No lights assigned'
  : room.state === 'unknown' ? 'Status unavailable'
  : room.on_count ? `${room.on_count} of ${room.assigned_count} on${room.unknown_count ? ` · ${room.unknown_count} unavailable` : ''}`
  : 'All lights off';

function emit() { for (const listener of listeners) listener(data, selectedRoom); }
export const mapLights = {
  subscribe(listener) { listeners.add(listener); listener(data, selectedRoom); },
  selectRoom(id) {
    if (!data?.rooms.some(room => room.id === id)) return;
    selectedRoom = id; get('mapRoomSelect').value = id;
    document.querySelectorAll('.map-room').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.room === id)));
    emit();
  },
  canDrop(id) { return !saving && !!data?.devices.some(device => device.entity_id === id && device.assignable); },
  drop(id, room) { if (this.canDrop(id)) { this.selectRoom(room); place(id, room); } },
};

function options(select, values, placeholder) {
  const value = select.value;
  const signature = JSON.stringify(values);
  if (select.dataset.options !== signature) {
    select.replaceChildren(new Option(placeholder, ''), ...values.map(([key, name]) => new Option(name, key)));
    select.dataset.options = signature;
    select.value = value;
  }
}

function draw() {
  const signature = JSON.stringify(data && [data.rooms, data.devices, data.enabled]);
  if (signature !== drawn && !dragging) {
    drawn = signature;
    options(get('mapLightSelect'), (data?.devices || []).filter(d => d.assignable).map(d => [d.entity_id, `${d.name} (${d.entity_id})`]), 'Choose a light');
    options(get('mapRoomSelect'), (data?.rooms || []).map(r => [r.id, r.name]), 'Choose a room');
    get('mapRoomSelect').value = selectedRoom;
    get('mapRooms').replaceChildren();
    for (const room of data?.rooms || []) {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'map-room'; button.dataset.room = room.id;
      button.dataset.state = room.state; button.setAttribute('aria-pressed', String(selectedRoom === room.id));
      const name = document.createElement('strong'), status = document.createElement('span');
      name.textContent = room.name; status.textContent = roomText(room);
      button.append(name, status); button.onclick = () => mapLights.selectRoom(room.id);
      button.ondragover = event => { if (event.dataTransfer.types.includes('application/x-adam-light')) {event.preventDefault(); event.dataTransfer.dropEffect = 'move';} };
      button.ondrop = event => {event.preventDefault(); mapLights.drop(event.dataTransfer.getData('application/x-adam-light'), room.id);};
      get('mapRooms').append(button);
    }
    const focused = document.activeElement?.dataset.remove;
    get('mapLightList').replaceChildren();
    for (const device of data?.devices || []) {
      const card = document.createElement('div'); card.className = 'map-light';
      card.dataset.entity = device.entity_id; card.dataset.state = device.state;
      card.draggable = device.assignable;
      const dot = document.createElement('span'); dot.className = 'map-light-dot'; dot.setAttribute('aria-hidden', 'true');
      const label = document.createElement('div');
      const name = document.createElement('strong'), detail = document.createElement('small');
      name.textContent = device.name;
      const room = data.rooms.find(r => r.id === device.room_id);
      detail.textContent = `${device.state === 'unavailable' ? 'Unavailable' : device.state === 'on' ? 'On' : 'Off'} · ${room?.name || 'Unassigned'} · ${device.entity_id}`;
      label.append(name, detail); card.append(dot, label);
      if (device.room_id) {
        const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'quiet';
        remove.textContent = 'Remove'; remove.dataset.remove = device.entity_id;
        remove.setAttribute('aria-label', `Remove ${device.name} from ${room?.name || 'room'}`);
        remove.onclick = () => place(device.entity_id, null); card.append(remove);
        if (focused === device.entity_id) queueMicrotask(() => remove.focus({preventScroll: true}));
      }
      card.ondragstart = event => {
        if (!mapLights.canDrop(device.entity_id)) {event.preventDefault(); return;}
        dragging = true; event.dataTransfer.setData('application/x-adam-light', device.entity_id);
        event.dataTransfer.effectAllowed = 'move'; get('mapAssignStatus').textContent = `Drop ${device.name} onto a room.`;
      };
      card.ondragend = () => {dragging = false; draw();};
      get('mapLightList').append(card);
    }
  }
  get('mapLightEmpty').hidden = !data || !!data.devices.length;
  get('mapLightSelect').disabled = saving || !data?.devices.some(d => d.assignable);
  get('mapRoomSelect').disabled = saving || !data;
  get('mapAssign').disabled = saving || !data?.devices.some(d => d.assignable);
  get('mapLightList').querySelectorAll('button').forEach(button => {button.disabled = saving;});
  get('mapLightStatus').textContent = data?.error || (data?.checked_at
    ? `Checked ${new Date(data.checked_at * 1000).toLocaleTimeString()} · every 5 seconds` : 'Waiting for light status');
  emit();
}

function unavailable(message, clear = false) {
  if (clear) {data = null; selectedRoom = '';}
  else if (data) data = {...data, error: message, checked_at: null,
    devices: data.devices.map(d => ({...d, state: 'unavailable', brightness: 0})),
    rooms: data.rooms.map(r => ({...r, state: r.assigned_count ? 'unknown' : 'unassigned', on_count: 0, unknown_count: r.assigned_count, brightness: 0}))};
  draw(); get('mapLightStatus').textContent = message;
}

async function refresh() {
  if (!active() || reading || saving) return;
  const current = generation, controller = new AbortController(); reading = controller;
  const timeout = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch('/api/map/lights', {cache: 'no-store', signal: controller.signal});
    if (current !== generation || !active()) return;
    if (!response.ok) {
      unavailable(response.status === 401 ? 'Sign in to view room lights.' : 'Light status unavailable · retrying', response.status === 401);
      return;
    }
    const result = await response.json();
    if (current !== generation || !active()) return;
    data = result; draw();
  } catch {
    if (current === generation && active()) unavailable('Light status unavailable · retrying');
  } finally {
    clearTimeout(timeout);
    if (reading === controller) reading = null;
    if (active() && !saving) {clearTimeout(timer); timer = setTimeout(refresh, 5000);}
  }
}

async function place(entity, room) {
  if (!data || saving) return;
  const name = data.devices.find(d => d.entity_id === entity)?.name || 'Light';
  const roomName = data.rooms.find(r => r.id === room)?.name;
  saving = true; generation++; clearTimeout(timer); reading?.abort(); reading = null;
  draw(); get('mapAssignStatus').textContent = 'Saving assignment…';
  const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(`/api/map/lights/${encodeURIComponent(entity)}`, {
      method: 'PUT', headers: {'Content-Type': 'application/json'}, signal: controller.signal,
      body: JSON.stringify({room_id: room, version: data.version}),
    });
    const result = await response.json();
    if (!response.ok) throw Error(typeof result.detail === 'string' ? result.detail : 'Assignment could not be saved.');
    get('mapAssignStatus').textContent = room ? `${name} assigned to ${roomName}.` : `${name} removed from the map.`;
  } catch (error) {
    get('mapAssignStatus').textContent = error.name === 'AbortError'
      ? 'Save timed out. Refreshing assignments to check the result.' : error.message;
  } finally {
    clearTimeout(timeout); saving = false; draw(); refresh();
  }
}

get('mapAssignForm').onsubmit = event => {
  event.preventDefault();
  const entity = get('mapLightSelect').value, room = get('mapRoomSelect').value;
  if (entity && room) place(entity, room);
};
get('mapRoomSelect').onchange = event => mapLights.selectRoom(event.target.value);
get('mapLightSelect').onchange = event => {
  const room = data?.devices.find(d => d.entity_id === event.target.value)?.room_id;
  if (room) mapLights.selectRoom(room);
};
function sync() {
  generation++; clearTimeout(timer); reading?.abort(); reading = null;
  if (dashboard.hidden) {unavailable('Sign in to view room lights.', true); get('mapAssignStatus').textContent = '';}
  else {unavailable('Refreshing light status…'); if (active()) refresh();}
}
new MutationObserver(sync).observe(dashboard, {attributes: true, attributeFilter: ['hidden']});
document.addEventListener('visibilitychange', sync);
sync();
