'use strict';
(() => {
  const node = id => document.getElementById(id);
  let units = [], signature = '', pending = null, timer = null, generation = 0;
  const saved = key => { try { return sessionStorage.getItem(key); } catch { return null; } };
  const save = (key, value) => { try { value == null ? sessionStorage.removeItem(key) : sessionStorage.setItem(key, value); } catch {} };
  const status = text => { node('commandStatus').textContent = text; };
  function controls() {
    const unit = units.find(u => u.id === node('commandUnit').value);
    node('commandSpeak').disabled = !!pending || !unit?.online;
    if (!unit?.online) node('commandSpeak').checked = false;
    for (const id of ['commandText', 'commandUnit', 'commandSend']) node(id).disabled = !!pending;
  }
  async function request(path, options = {}) {
    const response = await fetch(path, options);
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return data;
  }
  function show(result) {
    const reply = result.reply || {};
    if (reply.status === 'working') {
      status('ADAM is working…');
      timer = setTimeout(() => check(false), 700);
      return;
    }
    const item = document.createElement('article'); item.className = 'history-item';
    const heading = document.createElement('strong'); heading.textContent = pending?.text || result.text || 'Command';
    const answer = document.createElement('p'); answer.textContent = reply.text || reply.error || 'No answer was returned.';
    item.append(heading, answer);
    if (reply.delivery_error) { const note = document.createElement('p'); note.textContent = reply.delivery_error; item.append(note); }
    if (reply.error && reply.text) { const note = document.createElement('p'); note.textContent = reply.error; item.append(note); }
    node('commandReplies').prepend(item);
    while (node('commandReplies').children.length > 10) node('commandReplies').lastChild.remove();
    pending = null; save('adam.pendingCommand', null); node('commandRetry').hidden = true;
    status(reply.status === 'unknown' ? 'Result uncertain — check the device before sending again.' : 'Reply received.');
    controls(); node('commandText').value = '';
  }
  async function check(submit) {
    if (!pending) return;
    clearTimeout(timer); node('commandRetry').hidden = true;
    const turn = generation;
    try {
      const result = await request(submit ? '/api/commands/text' : '/api/commands/text/' + pending.request_id,
        submit ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(pending)} : {});
      if (turn === generation) show(result);
    } catch (error) {
      if (turn !== generation) return;
      if (submit && [404,409,422,429].includes(error.status)) {
        pending = null; save('adam.pendingCommand', null); controls(); status(error.message); return;
      }
      status('Unable to confirm the result. Use Check / retry safely to check this same command without duplicating it. ' + error.message);
      node('commandRetry').hidden = false;
    }
  }
  node('commandForm').addEventListener('submit', event => {
    event.preventDefault(); if (pending) return;
    const text = node('commandText').value.trim(); if (!text) return;
    // getRandomValues works on LAN HTTP as well as HTTPS; randomUUID need not.
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    pending = {request_id:Array.from(bytes, b => b.toString(16).padStart(2,'0')).join(''), text,
      unit_id:node('commandUnit').value || null, speak_reply:node('commandSpeak').checked};
    save('adam.pendingCommand', JSON.stringify(pending)); controls(); status('Sending…'); check(true);
  });
  node('commandRetry').onclick = () => check(true);
  node('commandUnit').onchange = () => { save('adam.commandUnit', node('commandUnit').value); controls(); };
  window.renderCommands = rows => {
    units = rows;
    const next = JSON.stringify(rows.map(u => [u.id, u.name, u.online]));
    if (signature !== next) {
      const previous = node('commandUnit').value || saved('adam.commandUnit') || '';
      node('commandUnit').replaceChildren(new Option('No speaker — reply here', ''));
      for (const unit of rows) node('commandUnit').add(new Option(unit.name + (unit.online ? '' : ' · offline'), unit.id));
      if (rows.some(u => u.id === previous)) node('commandUnit').value = previous;
      signature = next;
    }
    if (!pending && saved('adam.pendingCommand')) {
      try { pending = JSON.parse(saved('adam.pendingCommand')); } catch { save('adam.pendingCommand', null); }
      if (pending) { node('commandText').value = pending.text; check(false); }
    }
    controls();
  };
  window.clearCommands = () => {
    generation++; clearTimeout(timer); pending = null; save('adam.pendingCommand', null);
    node('commandReplies').replaceChildren(); node('commandText').value = ''; node('commandRetry').hidden = true; status(''); controls();
  };
})();
