'use strict';
(() => {
  let signature = '', latest = null;
  const form = document.getElementById('callForm');
  const from = document.getElementById('callFrom'), to = document.getElementById('callTo');
  function ready() {
    const source = latest?.units.find(u => u.id === from.value);
    const target = latest?.units.find(u => u.id === to.value);
    document.getElementById('callStart').disabled = !source?.online || !source?.capable ||
      !target?.online || !target?.capable || !target?.allow_drop_in || from.value === to.value ||
      latest.calls.some(c => c.state !== 'ended' && [c.caller,c.callee].some(id => [from.value,to.value].includes(id)));
  }
  from.onchange = to.onchange = ready;
  form.onsubmit = guard(async e => {
    e.preventDefault();
    document.getElementById('callStart').disabled = true;
    try {
      await post('/api/intercom/calls', {caller:from.value, callee:to.value});
      notice('Connecting the speakers. A chime announces the call.');
    } finally { await refresh(); }
  });
  window.renderIntercom = data => {
    if (!data) return;
    latest = data;
    const next = JSON.stringify(data.units);
    if (signature !== next) {
      signature = next;
      for (const select of [from,to]) {
        const old = select.value;
        select.replaceChildren(new Option('Choose a speaker',''));
        for (const u of data.units) select.add(new Option(`${u.name}${!u.online?' · Offline':!u.capable?' · Firmware update needed':''}`,u.id));
        select.value = data.units.some(u => u.id === old) ? old : '';
      }
      const permissions = document.getElementById('callPermissions');
      permissions.replaceChildren();
      for (const unit of data.units) {
        const label = el('label'), check = document.createElement('input');
        check.type = 'checkbox'; check.checked = unit.allow_drop_in;
        check.style.width = 'auto'; check.style.marginRight = '10px';
        label.append(check, document.createTextNode(unit.name));
        check.onchange = guard(async () => {
          check.disabled = true;
          try { await api(`/api/intercom/units/${unit.id}`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({allow_drop_in:check.checked})}); }
          catch (e) { check.checked = !check.checked; throw e; }
          finally { check.disabled = false; await refresh(); }
        });
        permissions.append(label);
      }
    }
    ready();
    const calls = document.getElementById('calls');
    const callSignature = JSON.stringify(data.calls);
    if (calls.dataset.signature === callSignature) return;
    calls.dataset.signature = callSignature; calls.replaceChildren();
    const name = id => data.units.find(u => u.id === id)?.name || 'Speaker';
    for (const call of data.calls.slice(-5).reverse()) {
      const row = el('article','','history-item');
      row.append(el('strong',`${name(call.caller)} ↔ ${name(call.callee)}`),el('p',call.reason || (call.state === 'active'?'Connected':'Connecting…')));
      if (call.state !== 'ended') {
        const end = el('button','End call','quiet');
        end.onclick = guard(async () => { end.disabled = true; try { await post(`/api/intercom/calls/${call.id}/end`); } finally { await refresh(); } });
        row.append(end);
      }
      calls.append(row);
    }
    if (!data.calls.length) calls.append(el('p','No calls yet.','help'));
  };
})();
