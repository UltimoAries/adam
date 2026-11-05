'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const svgNS = 'http://www.w3.org/2000/svg';
  let data = null, roomId = 'living', lightId = '', editing = false, busy = false;
  let timer, reader, generation = 0, drag = null, controlsKey = '', authenticated = false;
  const markers = new Map(), rooms = new Map();
  const node = (tag, text = '', cls = '') => {
    const n = document.createElement(tag); n.textContent = text; n.className = cls; return n;
  };
  const svg = (tag, attrs) => {
    const n = document.createElementNS(svgNS, tag);
    for (const [key, value] of Object.entries(attrs)) n.setAttribute(key, value);
    return n;
  };
  const currentLight = () => data?.devices.find(d => d.entity_id === lightId);
  const viewport = () => data?.viewport || {x: 0, y: 0, width: 1000, height: 700};
  function inside(room, x, y) {
    const b = room.bounds;
    if (x < b.x || x > b.x+b.width || y < b.y || y > b.y+b.height) return false;
    if (!room.points) return true;
    let hit = false;
    for (let i = 0, j = room.points.length-1; i < room.points.length; j = i++) {
      const [ax, ay] = room.points[i], [bx, by] = room.points[j];
      if (Math.abs((x-ax)*(by-ay)-(y-ay)*(bx-ax)) < 1e-7 && x >= Math.min(ax,bx) && x <= Math.max(ax,bx) && y >= Math.min(ay,by) && y <= Math.max(ay,by)) return true;
      if ((ay > y) !== (by > y) && x < (bx-ax)*(y-ay)/(by-ay)+ax) hit = !hit;
    }
    return hit;
  }
  const roomAt = (x, y) => data?.rooms.find(r => inside(r, x, y));
  const active = () => authenticated && !document.hidden;
  function cancelRead() { generation++; reader?.abort(); reader = null; clearTimeout(timer); }
  function signedOut() {
    authenticated = false; cancelRead(); data = null; drag = null; lightId = '';
    markers.clear(); rooms.clear(); $('lightMarkers').replaceChildren(); $('floorSvg').replaceChildren();
    $('floorArtwork').removeAttribute('src'); $('floorArtwork').hidden = true;
    $('roomLights').replaceChildren(); $('lightControls').replaceChildren(); $('lightChoice').replaceChildren();
    $('roomChoice').replaceChildren(); $('lightName').textContent = ''; $('lightState').textContent = '';
    $('mapNotice').textContent = ''; $('controlResult').textContent = ''; $('mapDashboard').hidden = true; $('signIn').hidden = false;
  }
  async function api(path, options = {}) {
    const response = await fetch(path, {cache: 'no-store', ...options});
    if (response.status === 401) signedOut();
    const result = await response.json();
    if (!response.ok) throw Error(typeof result.detail === 'string' ? result.detail : `Request failed (${response.status}). Check your entries.`);
    return result;
  }
  const json = (method, body) => ({method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  function unknown() {
    if (!data) return;
    data.checked_at = null;
    data.devices.forEach(d => { d.state = 'unavailable'; d.controls = []; });
    data.rooms.forEach(r => { r.state = r.assigned_count ? 'unknown' : 'unassigned'; r.on_count = 0; r.unknown_count = r.assigned_count; });
    render();
  }
  async function refresh(afterMutation = false) {
    clearTimeout(timer);
    if (!active() || (busy && !afterMutation) || drag || reader) { if (active()) timer = setTimeout(refresh, 1000); return; }
    const epoch = generation, controller = new AbortController(); reader = controller;
    const timeout = setTimeout(() => controller.abort(), 18000);
    try {
      const next = await api('/api/map/lights', {signal: controller.signal});
      if (epoch !== generation || !active()) return;
      if (!data) $('mapNotice').textContent = '';
      if (data?.layout !== next.layout) {
        rooms.clear(); markers.clear(); $('floorSvg').replaceChildren(); $('lightMarkers').replaceChildren();
        if (!next.rooms.some(r => r.id === roomId)) roomId = next.rooms[0].id;
      }
      data = next;
      if (data.layout_error) $('mapNotice').textContent = data.layout_error;
      if (lightId && !currentLight()) lightId = '';
      $('mapDashboard').hidden = false; $('signIn').hidden = true;
      render();
      $('mapStatus').textContent = data.error || (data.checked_at ? `Checked ${new Date(data.checked_at*1000).toLocaleTimeString()}` : 'Status unavailable');
    } catch (error) {
      if (epoch !== generation || !active()) return;
      unknown(); $('mapStatus').textContent = 'Status unavailable · retrying';
      if (!data) $('mapNotice').textContent = 'Could not load the map. Retrying…';
    } finally {
      clearTimeout(timeout); if (reader === controller) reader = null;
      if (epoch === generation && active()) timer = setTimeout(refresh, 5000);
    }
  }
  function chooseRoom(id) { roomId = id; render(); }
  function chooseLight(id) {
    const changed = lightId !== id;
    lightId = id; const light = currentLight();
    if (light?.room_id) roomId = light.room_id;
    if (changed) { controlsKey = ''; $('controlResult').textContent = ''; }
    render();
  }
  function point(event) {
    const rect = $('floorSurface').getBoundingClientRect();
    const v = viewport();
    return {x: Math.round(v.x+(event.clientX-rect.left)/rect.width*v.width), y: Math.round(v.y+(event.clientY-rect.top)/rect.height*v.height)};
  }
  function buildRooms() {
    if (rooms.size) return;
    const art = !!data.background_url;
    $('layoutName').textContent = data.name;
    $('layoutSummary').textContent = `${data.rooms.length} spaces · One home`;
    const v = viewport();
    $('floorSurface').style.aspectRatio = String((data.aspect_ratio || 10/7)*(v.width/1000)/(v.height/700));
    $('floorSvg').setAttribute('viewBox', `${v.x} ${v.y} ${v.width} ${v.height}`);
    Object.assign($('floorArtwork').style, {left: `${-v.x/v.width*100}%`, top: `${-v.y/v.height*100}%`, width: `${1000/v.width*100}%`, height: `${700/v.height*100}%`});
    $('floorScroll').classList.toggle('has-artwork', art);
    $('floorArtwork').hidden = !art;
    if (art) $('floorArtwork').src = data.background_url;
    else $('floorArtwork').removeAttribute('src');
    const defs = svg('defs', {}); $('floorSvg').append(defs);
    for (const room of data.rooms) {
      const b = room.bounds, group = svg('g', {class: 'floor-room', role: 'button', tabindex: 0, 'data-room': room.id});
      group.dataset.outdoor = String(!!room.outdoor);
      const shape = room.points ? svg('polygon', {points: room.points.map(p => p.join(',')).join(' '), class: 'room-floor'}) : svg('rect', {x: b.x, y: b.y, width: b.width, height: b.height, rx: 6, class: 'room-floor'});
      const gradient = svg('radialGradient', {id: 'roomGlow-'+room.id, gradientUnits: 'userSpaceOnUse', r: Math.max(b.width,b.height)*.9});
      gradient.append(svg('stop', {offset: '0%', 'stop-color': '#fff8db', 'stop-opacity': 1}),
        svg('stop', {offset: '55%', 'stop-color': '#ffe3a0', 'stop-opacity': .7}),
        svg('stop', {offset: '100%', 'stop-color': '#ffcf70', 'stop-opacity': 0}));
      defs.append(gradient);
      const glow = shape.cloneNode(); glow.setAttribute('class', 'room-illumination'); glow.setAttribute('fill', `url(#roomGlow-${room.id})`);
      group.append(shape, glow);
      const title = svg('text', {x: b.x+16, y: b.y+27, class: 'room-label'}); title.textContent = room.name;
      const count = svg('text', {x: b.x+16, y: b.y+46, class: 'room-count'});
      group.append(title, count); rooms.set(room.id, group); $('floorSvg').append(group);
      if (room.outdoor) {
        title.setAttribute('x', room.anchor[0]); title.setAttribute('y', room.id === 'outdoor_back' ? -57 : 762);
        title.setAttribute('text-anchor', 'middle');
      }
      group.addEventListener('click', event => {
        if (busy || !editing) return;
        const p = point(event);
        chooseRoom(room.id);
        if (editing && currentLight()?.assignable) place(room.id, p.x, p.y);
      });
      group.addEventListener('keydown', event => {
        if (editing && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); chooseRoom(room.id); }
      });
    }
    if (art) return;
    $('floorSvg').append(svg('path', {class: 'floor-walls', d: 'M70 120H930V620H70Z M430 120V230 M430 278V500 M700 120V400 M700 450V500 M430 365H510 M560 365H758 M808 365H930 M70 500H145 M270 500H475 M520 500H600 M650 500H760 M810 500H930 M565 500V535 M565 578V620'}));
    $('floorSvg').append(svg('path', {class: 'floor-doors', d: 'M430 230A48 48 0 0 1 478 278H430 M145 500A62 62 0 0 0 207 562V500 M510 365A50 50 0 0 0 560 415H510 M758 365A50 50 0 0 1 808 415H758 M700 400A50 50 0 0 1 650 450V400 M475 500A45 45 0 0 0 520 545H475 M600 500A50 50 0 0 1 650 550H600 M760 500A50 50 0 0 1 810 550H760 M565 535A43 43 0 0 0 608 578H565'}));
  }
  function markerPosition(button, x, y) {
    const v = viewport();
    button.style.left = `${(x-v.x)/v.width*100}%`; button.style.top = `${(y-v.y)/v.height*100}%`;
    button.dataset.labelAbove = String(y > v.y+v.height-65);
    button.dataset.labelEdge = x < v.x+70 ? 'left' : x > v.x+v.width-70 ? 'right' : '';
  }
  function addMarker(light) {
    const button = node('button', '☼', 'light-marker'); button.type = 'button'; button.dataset.entity = light.entity_id;
    let press = null;
    button.append(node('span', light.name, 'light-marker-label'));
    button.addEventListener('click', event => {
      const blocked = event.detail !== 0 && (press?.moved || press?.blocked); press = null;
      if (busy || blocked) return;
      chooseLight(light.entity_id);
      const current = currentLight();
      if (!editing && current?.assignable && ['on','off'].includes(current.state)) act(current.state === 'on' ? 'off' : 'on', null);
    });
    button.addEventListener('pointerdown', event => {
      press = {x: event.clientX, y: event.clientY, moved: false, blocked: busy};
      if (!editing && !busy && event.button === 0) button.setPointerCapture(event.pointerId);
      if (!editing || busy || event.button !== 0) return;
      event.preventDefault(); button.focus({preventScroll: true});
      chooseLight(light.entity_id); if (!currentLight()?.assignable) return;
      cancelRead(); const p = point(event);
      drag = {id: light.entity_id, button, pointer: event.pointerId, start: p, originX: currentLight().x, originY: currentLight().y, x: currentLight().x, y: currentLight().y};
      button.setPointerCapture(event.pointerId);
    });
    button.addEventListener('pointermove', event => {
      if (press && Math.hypot(event.clientX-press.x, event.clientY-press.y) > 6) press.moved = true;
      if (drag?.button !== button || drag.pointer !== event.pointerId) return;
      const p = point(event); drag.x = drag.originX+p.x-drag.start.x; drag.y = drag.originY+p.y-drag.start.y;
      markerPosition(button, drag.x, drag.y);
    });
    button.addEventListener('pointerup', event => {
      if (drag?.button !== button) return;
      const moved = drag; drag = null;
      if (button.hasPointerCapture(event.pointerId)) button.releasePointerCapture(event.pointerId);
      if (Math.hypot(moved.x-moved.originX, moved.y-moved.originY) < 4) { render(); refresh(); return; }
      const room = roomAt(moved.x, moved.y);
      if (room) place(room.id, moved.x, moved.y);
      else { $('mapNotice').textContent = 'Drop the marker inside a room.'; render(); refresh(); }
    });
    button.addEventListener('pointercancel', () => { press = null; drag = null; render(); refresh(); });
    button.addEventListener('keydown', event => {
      if (!editing || busy || !['ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(event.key)) return;
      event.preventDefault(); chooseLight(light.entity_id);
      const d = currentLight(), step = event.shiftKey ? 2 : 10;
      const x = d.x + (event.key === 'ArrowRight' ? step : event.key === 'ArrowLeft' ? -step : 0);
      const y = d.y + (event.key === 'ArrowDown' ? step : event.key === 'ArrowUp' ? -step : 0);
      const room = roomAt(x, y); if (room) place(room.id, x, y);
    });
    markers.set(light.entity_id, button); $('lightMarkers').append(button); return button;
  }
  function options(select, rows, placeholder) {
    const signature = JSON.stringify(rows);
    if (select.dataset.options === signature) return;
    select.dataset.options = signature; select.replaceChildren();
    if (placeholder) select.add(new Option(placeholder, ''));
    rows.forEach(([id, name]) => select.add(new Option(name, id)));
  }
  function render() {
    if (!data) return;
    buildRooms();
    for (const room of data.rooms) {
      const group = rooms.get(room.id); group.dataset.state = room.state;
      group.dataset.unknown = String(room.unknown_count > 0);
      const level = Math.max(0, Math.min(1, room.brightness || 0));
      group.style.setProperty('--room-glow', String(.18+.44*level));
      group.style.setProperty('--room-dim', String(.16*(1-level)));
      const on = data.devices.filter(d => d.room_id === room.id && d.state === 'on');
      const anchor = room.anchor || [room.bounds.x+room.bounds.width/2, room.bounds.y+room.bounds.height/2];
      const gradient = document.getElementById('roomGlow-'+room.id);
      gradient.setAttribute('cx', on.length ? on.reduce((sum,d) => sum+d.x, 0)/on.length : anchor[0]);
      gradient.setAttribute('cy', on.length ? on.reduce((sum,d) => sum+d.y, 0)/on.length : anchor[1]);
      group.setAttribute('tabindex', editing ? '0' : '-1');
      group.setAttribute('role', editing ? 'button' : 'group');
      if (editing) group.setAttribute('aria-pressed', String(room.id === roomId));
      else group.removeAttribute('aria-pressed');
      const description = room.assigned_count ? `${room.on_count} on · ${room.assigned_count} placed${room.unknown_count ? ` · ${room.unknown_count} unavailable` : ''}` : 'No lights placed';
      group.setAttribute('aria-label', `${room.name}: ${description}`); group.querySelector('.room-count').textContent = description;
    }
    const placed = new Set(data.devices.filter(d => d.room_id).map(d => d.entity_id));
    for (const [id, button] of markers) if (!placed.has(id)) { button.remove(); markers.delete(id); }
    for (const light of data.devices.filter(d => d.room_id)) {
      const button = markers.get(light.entity_id) || addMarker(light);
      markerPosition(button, light.x, light.y); button.dataset.state = light.state;
      button.querySelector('span').textContent = light.name;
      const available = light.assignable && ['on','off'].includes(light.state);
      const hint = editing ? 'Drag or use arrow keys to move. Clicking does not switch the light.' : available ? `Click to turn ${light.state === 'on' ? 'off' : 'on'}.` : 'Control unavailable.';
      button.setAttribute('aria-label', `${light.name}: ${light.state}. ${hint}`);
      button.setAttribute('aria-pressed', String(editing ? light.entity_id === lightId : light.state === 'on'));
      button.setAttribute('aria-disabled', String(busy || (!editing && !available)));
      button.dataset.selected = String(light.entity_id === lightId);
      button.title = `${light.name} · ${light.state}. ${hint}`;
    }
    options($('roomChoice'), data.rooms.map(r => [r.id, r.name])); $('roomChoice').value = roomId;
    options($('lightChoice'), data.devices.map(d => [d.entity_id, `${d.name}${d.room_id ? '' : ' · not placed'}`]), 'Choose an imported light');
    $('lightChoice').value = lightId; $('lightEmpty').hidden = data.devices.length > 0;
    $('roomTitle').textContent = data.rooms.find(r => r.id === roomId)?.name || 'Room';
    const roomLights = data.devices.filter(d => d.room_id === roomId);
    const signature = JSON.stringify(roomLights.map(d => [d.entity_id, d.name, d.state, d.entity_id === lightId]));
    if ($('roomLights').dataset.signature !== signature) {
      $('roomLights').dataset.signature = signature; $('roomLights').replaceChildren();
      for (const d of roomLights) {
        const button = node('button', d.name); button.append(node('small', d.state));
        button.setAttribute('aria-pressed', String(d.entity_id === lightId)); button.onclick = () => { if (!busy) chooseLight(d.entity_id); };
        $('roomLights').append(button);
      }
      if (!roomLights.length) $('roomLights').append(node('p', 'No lights placed in this room yet.', 'help'));
    }
    const light = currentLight(); $('lightDetail').hidden = !light;
    if (light) {
      $('lightName').textContent = light.name;
      $('lightState').textContent = `${light.state === 'on' ? 'On' : light.state === 'off' ? 'Off' : 'Status unavailable'}${light.state === 'on' && light.brightness_known ? ` · ${Math.round(light.brightness*100)}% brightness` : ''}${!light.assignable ? ' · disabled or removed from inventory' : ''}`;
      renderControls(light);
      $('placeLight').textContent = light.room_id ? 'Move to selected room' : 'Place in this room';
      $('placeLight').disabled = !editing || busy || !light.assignable; $('removeLight').disabled = !editing || busy || !light.room_id;
    }
    $('roomChoice').disabled = busy; $('lightChoice').disabled = busy; $('moveMode').disabled = busy;
    $('floorSurface').classList.toggle('editing', editing); $('moveMode').setAttribute('aria-pressed', String(editing));
    $('moveMode').textContent = editing ? 'Edit mode: On' : 'Edit mode: Off';
    $('placementActions').hidden = !editing; $('editHelp').hidden = !editing;
    $('lightInspector').hidden = !editing;
    $('floorWorkspace').classList.toggle('viewing', !editing);
  }
  function renderControls(light) {
    const key = JSON.stringify([light.entity_id, light.controls, light.assignable]);
    if (controlsKey !== key) {
      controlsKey = key; $('lightControls').replaceChildren();
      for (const c of light.controls) {
        if (!c.kind) {
          const button = node('button', c.label, c.key === 'on' ? 'primary' : 'secondary');
          button.onclick = () => act(c.key, null); $('lightControls').append(button); continue;
        }
        const form = node('form'), label = node('label', c.label), input = node('input');
        input.setAttribute('aria-label', c.label+' for '+light.name);
        if (c.kind === 'color') { input.type = 'color'; input.value = '#ffffff'; }
        else {
          input.type = 'range'; input.min = c.min; input.max = c.max; input.step = c.step;
          input.value = c.key === 'brightness' ? Math.round(light.brightness*100) : Math.round((c.min+c.max)/2);
          input.dataset.control = c.key;
          const output = node('output', input.value); input.oninput = () => { input.dataset.dirty = 'true'; output.value = input.value; };
          label.append(output);
        }
        const button = node('button', 'Apply', 'secondary'); button.type = 'submit'; label.append(input); form.append(label, button);
        form.onsubmit = event => { event.preventDefault(); act(c.key, c.kind === 'color' ? input.value : Number(input.value)); };
        $('lightControls').append(form);
      }
      if (!light.controls.length) $('lightControls').append(node('p', 'Controls are available when this light reports its state.', 'help'));
    }
    const brightness = $('lightControls').querySelector('[data-control=brightness]');
    if (brightness && !brightness.dataset.dirty && document.activeElement !== brightness) {
      brightness.value = Math.round(light.brightness*100);
      brightness.closest('label').querySelector('output').value = brightness.value;
    }
    $('lightControls').querySelectorAll('button,input').forEach(n => { n.disabled = editing || busy || !light.assignable || light.state === 'unavailable'; });
  }
  async function mutate(path, body, method, control = false) {
    if (busy || !data || !authenticated) return false;
    busy = true; cancelRead(); render();
    const target = $('mapNotice'); target.textContent = control ? 'Sending…' : 'Saving marker…';
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 45000);
    try {
      const result = await api(path, {...json(method, {...body, version: data.version}), signal: controller.signal});
      if (!authenticated) return false;
      target.textContent = control ? result.text : 'Marker saved.';
      return true;
    } catch (error) {
      if (authenticated) target.textContent = error.name === 'AbortError' ? 'The request timed out. Check the current status before trying again; it may have reached the device.' : error.message;
      return false;
    } finally {
      clearTimeout(timeout);
      if (control) controlsKey = '';
      if (authenticated) await refresh(true);
      busy = false; if (authenticated) render();
    }
  }
  async function place(room, x = null, y = null) {
    if (!editing || !currentLight() || busy) return;
    await mutate(`/api/map/lights/${encodeURIComponent(lightId)}`, {room_id: room, x, y}, 'PUT');
    if (currentLight()?.room_id) roomId = currentLight().room_id;
    render();
  }
  function act(action, value) {
    const light = currentLight();
    if (editing || !light || busy || light.state === 'unavailable' || !light.assignable) return;
    mutate(`/api/map/lights/${encodeURIComponent(lightId)}/actions`, {action, value}, 'POST', true);
  }
  $('roomChoice').onchange = () => chooseRoom($('roomChoice').value);
  $('lightChoice').onchange = () => chooseLight($('lightChoice').value);
  $('moveMode').onclick = () => {
    editing = !editing; render();
    $('mapHelp').textContent = editing ? 'Editing is on: drag markers or use arrow keys. Clicking markers will not switch lights. Turn Edit mode off when finished.' : 'Click a light marker to switch it on or off. Turn on Edit mode to place or move markers.';
  };
  $('placeLight').onclick = () => {
    const room = data.rooms.find(r => r.id === roomId), b = room.bounds;
    // Find a free grid position so adding lights doesn't stack them at the centre.
    const positions = [.5, .25, .75].flatMap(y => [.5, .25, .75].map(x => ({x: b.x+b.width*x, y: b.y+b.height*y}))).filter(p => inside(room, p.x, p.y));
    if (room.anchor) positions.push({x: room.anchor[0], y: room.anchor[1]});
    const p = positions.find(p => !data.devices.some(d => d.entity_id !== lightId && d.room_id === roomId && Math.hypot(d.x-p.x, d.y-p.y) < 45)) || positions[0];
    place(roomId, p.x, p.y);
  };
  $('removeLight').onclick = () => place(null);
  $('floorArtwork').onerror = () => { $('mapNotice').textContent = 'The floor-plan image could not be loaded. Reload to try again.'; };
  document.addEventListener('visibilitychange', () => {
    cancelRead(); if (!active()) { drag = null; unknown(); $('mapStatus').textContent = 'Status paused'; } else refresh();
  });
  window.addEventListener('pageshow', () => { if (authenticated) refresh(); });
  api('/api/auth/status').then(auth => {
    authenticated = auth.authenticated; $('signIn').hidden = authenticated;
    if (authenticated) refresh(); else signedOut();
  }).catch(() => { $('mapNotice').textContent = 'Could not connect to ADAM. Reload to try again.'; });
})();
