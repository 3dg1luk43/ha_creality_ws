/* Control UI for the Creality printer simulator.
 *
 * Polls GET /api/state and GET /api/log about once a second and drives the
 * simulator through POST /api/action. Plain ES2020, no build step, no network
 * access beyond the simulator's own control API.
 *
 * Rendering updates the DOM in place: rows and cards are created once and
 * only their text changes, and an input the user is editing (focused, or
 * changed but not yet applied) is never overwritten by a poll.
 */
'use strict';

(() => {
  const POLL_MS = 1000;
  const HIDDEN_POLL_MS = 5000;
  const MAX_BACKOFF_MS = 5000;
  const FETCH_TIMEOUT_MS = 4000;
  const ACTION_TIMEOUT_MS = 15000;
  const LOG_CAP = 500;
  const PINNED = [
    'state', 'printProgress', 'withSelfTest', 'printFileName', 'printLeftTime',
    'nozzleTemp', 'targetNozzleTemp', 'bedTemp0', 'targetBedTemp0', 'boxTemp',
    'deviceState', 'err', 'materialStatus', 'cfsConnect',
  ];
  const PINNED_SET = new Set(PINNED);
  const INIT = Symbol('init');

  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  };
  const setText = (node, text) => {
    const value = text === undefined || text === null ? '' : String(text);
    if (node.textContent !== value) node.textContent = value;
  };
  const setAttr = (node, name, value) => {
    const v = String(value);
    if (node.getAttribute(name) !== v) node.setAttribute(name, v);
  };
  const pad = (n, w = 2) => String(n).padStart(w, '0');
  const clockTime = (epochSeconds, ms = false) => {
    const d = new Date(epochSeconds * 1000);
    const base = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    return ms ? `${base}.${pad(d.getMilliseconds(), 3)}` : base;
  };
  const duration = (seconds) => {
    const s = Math.max(0, Math.round(Number(seconds) || 0));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`;
  };
  const present = (v) => v !== undefined && v !== null && v !== '';
  // Temperatures arrive as numbers or, on the K1 family, six-decimal strings.
  const temp = (v) => {
    if (v === undefined) return 'absent';
    if (v === '') return 'blank';
    const n = Number(v);
    return Number.isFinite(n) ? (Number.isInteger(n) ? String(n) : n.toFixed(1)) : String(v);
  };

  // An input the user is working on keeps its value until they are done:
  // while focused (text and number fields), while changed but not yet
  // applied (`dirty`), and until a poll that started after the apply
  // (`hold`), so an older in-flight response cannot flip it back.
  let renderPollStart = 0;
  const syncValue = (input, value) => {
    if (input.type !== 'checkbox' && input.tagName !== 'SELECT' && input === document.activeElement) return;
    if (input.dataset.dirty === '1') return;
    if (Number(input.dataset.hold || 0) > renderPollStart) return;
    if (input.type === 'checkbox') {
      if (input.checked !== Boolean(value)) input.checked = Boolean(value);
    } else {
      const v = value === undefined || value === null ? '' : String(value);
      if (input.value !== v) input.value = v;
    }
  };

  // ================================================================ toasts
  function toast(message, kind = 'ok') {
    const box = $('toasts');
    const node = el('div', `toast t-${kind}`, message);
    node.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    node.title = 'Click to dismiss';
    const close = () => {
      node.classList.add('out');
      setTimeout(() => node.remove(), 250);
    };
    node.addEventListener('click', close);
    box.append(node);
    while (box.children.length > 5) box.firstElementChild.remove();
    setTimeout(close, { error: 7000, warn: 4000, info: 2600 }[kind] || 1800);
  }

  // ================================================================ network
  async function fetchJSON(url, options = {}, timeoutMs = FETCH_TIMEOUT_MS) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const res = await fetch(url, { cache: 'no-store', ...options, signal: ctl.signal });
      const text = await res.text();
      let body = null;
      try {
        body = JSON.parse(text);
      } catch (_err) {
        body = null;
      }
      return { status: res.status, ok: res.ok, body, text };
    } catch (err) {
      if (err && err.name === 'AbortError') throw new Error('timed out');
      throw err;
    } finally {
      clearTimeout(timer);
    }
  }

  async function getJSON(url) {
    const res = await fetchJSON(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    if (res.body === null) throw new Error('the response is not valid JSON');
    return res.body;
  }

  async function act(action, args = {}, button = null) {
    if (button) {
      button.disabled = true;
      button.classList.add('busy');
    }
    try {
      const res = await fetchJSON('/api/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, ...args }),
      }, ACTION_TIMEOUT_MS);
      const body = res.body;
      if (!res.ok || !body || body.ok !== true) {
        const why = (body && body.error) || `HTTP ${res.status} ${res.text.slice(0, 160).trim()}`;
        toast(`${action}: ${why}`, 'error');
        return null;
      }
      if (body.applied === false) {
        // The simulator found nothing to do (a stop with no print running).
        toast(`${action}: nothing to do`, 'info');
      } else {
        const extra = Object.entries(body).filter(([k]) => k !== 'ok' && k !== 'applied');
        toast(extra.length ? `${action}: ${extra.map(([k, v]) => `${k} ${JSON.stringify(v)}`).join(', ')}` : `${action}: ok`);
      }
      return body;
    } catch (err) {
      toast(`${action}: ${err && err.message ? err.message : err}`, 'error');
      return null;
    } finally {
      if (button) {
        button.disabled = false;
        button.classList.remove('busy');
      }
      pollNow();
    }
  }

  // ================================================================ polling
  let pollTimer = null;
  let polling = false;
  let pollAgain = false;
  let failures = 0;
  let everConnected = false;
  let logSeq = 0;

  function schedule(ms) {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(poll, ms);
  }

  function pollNow() {
    if (polling) pollAgain = true;
    else schedule(0);
  }

  async function poll() {
    clearTimeout(pollTimer);
    polling = true;
    const startedAt = performance.now();
    const [state, log] = await Promise.allSettled([
      getJSON('/api/state'),
      getJSON(`/api/log?since=${logSeq}`),
    ]);
    let error = null;
    if (state.status === 'fulfilled') {
      try {
        renderPollStart = startedAt;
        render(state.value);
      } catch (err) {
        error = `render: ${err && err.message}`;
        console.warn('render failed', err);
      }
    } else {
      error = state.reason && state.reason.message;
    }
    if (log.status === 'fulfilled') {
      try {
        renderLog(log.value);
      } catch (err) {
        console.warn('log render failed', err);
      }
    } else if (!error) {
      error = `log: ${log.reason && log.reason.message}`;
    }
    const ok = state.status === 'fulfilled' && log.status === 'fulfilled';
    failures = ok ? 0 : failures + 1;
    if (ok) everConnected = true;
    setConnection(ok, error);
    polling = false;
    let delay = document.hidden ? HIDDEN_POLL_MS : POLL_MS;
    if (!ok) delay = Math.min(MAX_BACKOFF_MS, POLL_MS * failures);
    if (pollAgain) delay = 0;
    pollAgain = false;
    schedule(delay);
  }

  function setConnection(ok, error) {
    const conn = $('h-conn');
    const cls = ok ? 'conn ok' : everConnected ? 'conn lost' : 'conn down';
    if (conn.className !== cls) conn.className = cls;
    setText($('h-conn-text'), ok ? 'live' : `retrying (${failures})`);
    conn.title = ok
      ? `Last update ${clockTime(Date.now() / 1000)}`
      : `Request failed: ${error || 'unknown error'}. Retrying.`;
    document.body.classList.toggle('stale', !ok);
  }

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) pollNow();
  });

  // ================================================================ render
  function render(s) {
    const sim = s.sim || {};
    const tele = s.telemetry || {};
    const profile = sim.profile || {};
    renderHeader(s, sim, profile);
    renderTelemetry(tele, sim.overrides || {}, sim.timed_blanks || {});
    renderJob(sim, tele, profile);
    renderFaults(s, sim, tele);
    renderSettings(s, sim);
    renderControls(sim, tele, profile);
    renderCfs(s, sim, tele, profile);
    renderOverrides(sim.overrides || {});
    renderTimedBlanks(sim.timed_blanks || {});
    renderEvents(sim.events || []);
  }

  // ---------------------------------------------------------------- header
  let listenerSig = '';
  let profilesSig = '';
  let currentProfileKey = '';
  let sessionCount = 0;

  function renderProfiles(profiles, current) {
    const select = $('h-model');
    const sig = JSON.stringify(profiles);
    if (sig !== profilesSig) {
      profilesSig = sig;
      const keep = select.dataset.dirty === '1' ? select.value : current;
      select.replaceChildren(...Object.entries(profiles).map(([key, title]) => {
        const opt = el('option', null, `${key}: ${title}`);
        opt.value = key;
        return opt;
      }));
      select.value = keep;
    }
    syncValue(select, current);
    $('h-switch').disabled = !select.value || select.value === current;
  }

  function renderHeader(s, sim, profile) {
    currentProfileKey = profile.key || '';
    sessionCount = (s.sessions || []).length;
    renderProfiles(s.profiles || {}, currentProfileKey);
    setText($('h-title'), profile.title || 'Creality simulator');
    setText($('h-key'), [profile.key, profile.model, profile.camera && `camera ${profile.camera}`].filter(Boolean).join(' | '));
    const power = $('h-power');
    setText(power, s.powered ? 'power on' : 'power off');
    const pcls = `badge ${s.powered ? 'b-ok' : 'b-bad'}`;
    if (power.className !== pcls) power.className = pcls;

    setText($('h-phase'), `phase ${sim.phase || '-'}${sim.homing ? ', homing' : ''}`);
    const sessions = s.sessions || [];
    const clients = $('h-clients');
    setText(clients, `${sessions.length} client${sessions.length === 1 ? '' : 's'}`);
    clients.title = sessions.length
      ? sessions.map((x) => `${x.peer}  since ${clockTime(x.connected_at)}  ${x.frames_sent} frames  ${x.mode}`).join('\n')
      : 'No WebSocket clients';

    const listeners = s.listeners || {};
    const sig = JSON.stringify(listeners);
    if (sig !== listenerSig) {
      listenerSig = sig;
      const box = $('h-listeners');
      box.replaceChildren(...Object.entries(listeners).map(([name, where]) => {
        const state = String(where).startsWith(':') ? 'l-up' : where === 'off' ? 'l-off' : 'l-bad';
        const chip = el('span', `chip ${state}`, `${name} ${where}`);
        chip.title = `${name}: ${where}`;
        return chip;
      }));
    }
    const title = `${s.powered ? '' : '[off] '}${sim.phase || ''} - ${profile.key || ''} - Creality simulator`;
    if (document.title !== title) document.title = title;
  }

  // ---------------------------------------------------------------- telemetry
  const teleRows = new Map();
  let teleKeysSig = '';

  function valueClass(v, has) {
    if (!has) return 'val v-absent';
    if (v === '') return 'val v-blank';
    if (v === null) return 'val v-null';
    if (typeof v === 'string') return 'val v-str';
    if (typeof v === 'number') return 'val v-num';
    if (typeof v === 'boolean') return 'val v-bool';
    return 'val v-obj';
  }

  function teleRow(key, body) {
    const tr = el('tr');
    const th = el('th', null, key);
    th.scope = 'row';
    const td = el('td', 'val');
    tr.append(th, td);
    body.append(tr);
    const row = { key, tr, td, json: INIT, mark: '', search: key.toLowerCase() };
    teleRows.set(key, row);
    return row;
  }

  function flash(node) {
    const a = node.classList.contains('flash-a');
    node.classList.remove('flash-a', 'flash-b');
    node.classList.add(a ? 'flash-b' : 'flash-a');
  }

  // `mark`: 'ovr' for a manual override, 'blank' for a timed blank, or ''.
  function updateTeleRow(row, has, value, mark) {
    const json = has ? JSON.stringify(value) : undefined;
    if (json !== row.json) {
      const first = row.json === INIT;
      row.json = json;
      row.td.textContent = has ? json : 'absent';
      row.td.className = valueClass(value, has);
      row.search = `${row.key}\n${has ? json : ''}`.toLowerCase();
      if (!first) flash(row.td);
    }
    if (row.mark !== mark) {
      row.mark = mark;
      row.tr.classList.toggle('ovr', mark === 'ovr');
      row.tr.classList.toggle('blank', mark === 'blank');
      row.tr.title = mark === 'ovr' ? 'Overridden (see Overrides)' : mark === 'blank' ? 'Blanked for a while (see Timed blanks)' : '';
    }
  }

  function renderTelemetry(tele, overrides, blanks) {
    const markOf = (key) => (key in overrides ? 'ovr' : key in blanks ? 'blank' : '');
    const pinnedBody = $('tele-pinned');
    const restBody = $('tele-rest');
    for (const key of PINNED) {
      const row = teleRows.get(key) || teleRow(key, pinnedBody);
      updateTeleRow(row, key in tele, tele[key], markOf(key));
    }
    const keys = Object.keys(tele).filter((k) => !PINNED_SET.has(k))
      .sort((a, b) => a.localeCompare(b, 'en', { sensitivity: 'base' }));
    let structural = false;
    for (const key of keys) {
      if (!teleRows.has(key)) {
        teleRow(key, restBody);
        structural = true;
      }
    }
    for (const [key, row] of teleRows) {
      if (!PINNED_SET.has(key) && !(key in tele)) {
        row.tr.remove();
        teleRows.delete(key);
        structural = true;
      }
    }
    if (structural) {
      for (const key of keys) restBody.append(teleRows.get(key).tr);
    }
    for (const key of keys) updateTeleRow(teleRows.get(key), true, tele[key], markOf(key));
    applyTeleFilter();

    const sig = Object.keys(tele).sort().join(',');
    if (sig !== teleKeysSig) {
      teleKeysSig = sig;
      $('tele-keys').replaceChildren(...Object.keys(tele).sort().map((k) => {
        const opt = el('option');
        opt.value = k;
        return opt;
      }));
    }
  }

  function applyTeleFilter() {
    const q = $('tele-filter').value.trim().toLowerCase();
    let shown = 0;
    let total = 0;
    for (const row of teleRows.values()) {
      const visible = !q || row.search.includes(q);
      if (row.tr.hidden === visible) row.tr.hidden = !visible;
      total += 1;
      if (visible) shown += 1;
    }
    setText($('tele-count'), q ? `${shown} of ${total}` : `${total} fields`);
  }

  // ---------------------------------------------------------------- job
  function renderJob(sim, tele, profile) {
    const phase = sim.phase || '-';
    const badge = $('job-phase');
    setText(badge, phase);
    const cls = `badge ph-${phase}`;
    if (badge.className !== cls) badge.className = cls;

    const job = sim.job;
    const bar = $('job-bar');
    let pct = 0;
    if (!job) {
      setText($('job-name'), 'No job');
      setText($('job-meta'), '');
      setText($('job-time'), present(tele.printFileName) ? `printFileName still ${JSON.stringify(tele.printFileName)}` : '');
    } else {
      setText($('job-name'), job.name);
      $('job-name').title = job.path || '';
      setText($('job-meta'), `${job.layers} layers, ${job.objects} objects`);
      pct = job.duration ? Math.min(100, (job.active_seconds / job.duration) * 100) : 0;
      const bits = [
        `${duration(job.active_seconds)} of ${duration(job.duration)} printed (${pct.toFixed(0)}%)`,
        `printProgress ${tele.printProgress ?? '-'}`,
        `left ${tele.printLeftTime ?? '-'} s`,
      ];
      if (tele.withSelfTest !== undefined && tele.withSelfTest !== 100 && tele.withSelfTest !== 0) {
        bits.push(`self-test ${tele.withSelfTest}`);
      }
      setText($('job-time'), bits.join(', '));
    }
    bar.style.width = `${pct.toFixed(1)}%`;
    setAttr($('job-progress'), 'aria-valuenow', pct.toFixed(0));

    setText($('opt-stop-default'), `default (${sim.stop_style || 'model'})`);
    setText($('opt-selftest-default'), `default (${profile.self_test_at_print_start ? 'yes' : 'no'})`);
  }

  // ---------------------------------------------------------------- faults
  function renderFaults(s, sim, tele) {
    const err = tele.err;
    setText($('err-cur'), err === undefined ? 'err absent' : `err ${JSON.stringify(err)}`);
    const errActive = err && typeof err === 'object' ? Number(err.errcode) !== 0 : present(err) && Number(err) !== 0;
    $('err-cur').classList.toggle('hot', Boolean(errActive));

    setText($('runout-cur'), `materialStatus ${tele.materialStatus ?? 'absent'}`);
    $('runout-cur').classList.toggle('hot', Number(tele.materialStatus) === 1);

    setText($('home-cur'), `${sim.homing ? 'homing, ' : ''}autohome ${tele.autohome ?? '-'}`);
    $('home-cur').classList.toggle('hot', Boolean(sim.homing));

    $('btn-power-off').disabled = !s.powered;
    $('btn-power-on').disabled = Boolean(s.powered);

    const settings = s.settings || {};
    syncValue($('set-silent'), settings.silent);
    syncValue($('set-reject'), settings.reject_sets);
    syncValue($('set-ffd'), settings.first_frame_delay);
  }

  // ---------------------------------------------------------------- settings
  function renderSettings(s, sim) {
    const settings = s.settings || {};
    syncValue($('s-frames'), settings.frames);
    syncValue($('s-heartbeat'), settings.printer_heartbeat);
    syncValue($('s-stop'), sim.stop_style);
    syncValue($('s-selftest'), sim.self_test_style);
    syncValue($('s-gcode'), sim.gcode_listing);
    syncValue($('s-finreset'), sim.finished_reset_seconds);
  }

  // ---------------------------------------------------------------- controls
  function renderControls(sim, tele, profile) {
    setText($('c-nozzle-cur'), `${temp(tele.nozzleTemp)} / ${temp(tele.targetNozzleTemp)}`);
    setText($('c-bed-cur'), `${temp(tele.bedTemp0)} / ${temp(tele.targetBedTemp0)}`);
    setText($('c-box-cur'), `${temp(tele.boxTemp)} / ${temp(tele.targetBoxTemp)}`);
    $('c-nozzle').max = profile.max_nozzle || '';
    $('c-bed').max = profile.max_bed || '';
    $('c-box').max = profile.max_box || '';

    const boxCtl = Boolean(profile.box_control);
    $('c-box').disabled = !boxCtl;
    document.querySelector('button[data-target="box"]').disabled = !boxCtl;

    const light = profile.light !== false;
    setText($('c-light-cur'), tele.lightSw === undefined ? 'absent' : tele.lightSw ? 'on' : 'off');
    $('c-light-on').disabled = !light;
    $('c-light-off').disabled = !light;

    const notes = [];
    if (!boxCtl) notes.push('No chamber control on this model.');
    if (profile.box_target_ws_zero) notes.push('targetBoxTemp reads 0 on the WebSocket; the real target is in Moonraker.');
    if (!light) notes.push('No light on this model.');
    if (profile.led_pin) {
      notes.push(`LED level (SET_PIN ${profile.led_pin}): ${sim.led_value ?? 'not set'}.`);
    }
    notes.push(`Limits: nozzle ${profile.max_nozzle ?? '-'}, bed ${profile.max_bed ?? '-'}${profile.max_box ? `, box ${profile.max_box}` : ''}.`);
    setText($('c-note'), notes.join(' '));
  }

  // ---------------------------------------------------------------- CFS
  const boxCards = new Map();

  // "#0RRGGBB" (pad digit), "#RRGGBB", or a comma list of either.
  function swatchColours(raw) {
    if (raw === undefined || raw === null) return [];
    return String(raw).split(',').map((part) => {
      let hex = part.trim().replace(/^#/, '');
      if (hex.length === 7) hex = hex.slice(1);
      return /^[0-9a-f]{6}$/i.test(hex) || /^[0-9a-f]{3}$/i.test(hex) ? `#${hex}` : null;
    }).filter(Boolean);
  }

  function paintSwatch(node, raw) {
    const colours = swatchColours(raw);
    let bg = '';
    if (colours.length === 1) {
      bg = colours[0];
    } else if (colours.length > 1) {
      const step = 100 / colours.length;
      bg = `linear-gradient(90deg, ${colours.map((c, i) => `${c} ${(i * step).toFixed(2)}% ${((i + 1) * step).toFixed(2)}%`).join(', ')})`;
    }
    if (node.dataset.bg !== bg) {
      node.dataset.bg = bg;
      node.style.background = bg;
      node.classList.toggle('empty', !bg);
    }
    node.title = present(raw) ? `color ${JSON.stringify(raw)}` : 'no colour';
  }

  function boxKey(box, index, seen) {
    let key = String(box && box.id);
    if (seen.has(key)) key = `${key}#${index}`;
    seen.add(key);
    return key;
  }

  function buildBoxCard(box) {
    const card = el('div', 'box');
    const head = el('div', 'box-head');
    const name = el('strong');
    const meta = el('span', 'muted small');
    const env = el('span', 'env mono');
    head.append(name, meta, env);
    const isCfs = box && box.type === 0;
    if (isCfs) {
      const remove = el('button', 'small danger', 'Remove');
      remove.type = 'button';
      remove.dataset.cfsRemove = String(box.id);
      remove.title = `Remove CFS box ${box.id}`;
      head.append(remove);
    }
    const list = el('ul', 'slots');
    card.append(head, list);
    const slots = [];
    const materials = Array.isArray(box && box.materials) ? box.materials : [];
    materials.forEach((m) => {
      const li = el('li', 'slot');
      if (!m || typeof m !== 'object') {
        li.append(el('span', 'muted small', `not an object: ${JSON.stringify(m)}`));
        list.append(li);
        slots.push(null);
        return;
      }
      const pick = el('button', 'slot-pick');
      pick.type = 'button';
      pick.dataset.cfsSelect = `${box.id}:${m.id}`;
      pick.title = `Select box ${box.id} slot ${m.id}`;
      const swatch = el('span', 'swatch');
      const text = el('span', 'slot-text');
      const title = el('span', 'slot-name');
      const sub = el('span', 'muted small');
      text.append(title, sub);
      pick.append(swatch, text);
      const pctWrap = el('label', 'pct');
      const pct = el('input', 'n4');
      pct.type = 'number';
      pct.min = '0';
      pct.max = '100';
      pct.step = '1';
      pct.dataset.cfsPercent = `${box.id}:${m.id}`;
      pct.setAttribute('aria-label', `Percent left, box ${box.id} slot ${m.id}`);
      pctWrap.append(pct, document.createTextNode('%'));
      const sel = el('span', 'sel-mark', 'selected');
      const meter = el('span', 'meter');
      const fill = el('span', 'fill');
      meter.append(fill);
      li.append(pick, pctWrap, sel, meter);
      list.append(li);
      slots.push({ li, swatch, title, sub, pct, fill });
    });
    return { card, name, meta, env, slots };
  }

  function boxSignature(box) {
    const materials = Array.isArray(box && box.materials) ? box.materials : [];
    return `${box && box.id}|${box && box.type}|${materials.map((m) => (m && typeof m === 'object' ? m.id : '?')).join(',')}`;
  }

  function updateBoxCard(entry, box) {
    const isCfs = box.type === 0;
    setText(entry.name, isCfs ? `CFS box ${box.id}` : box.type === 1 ? `External spool (box ${box.id})` : `Box ${box.id}`);
    setText(entry.meta, `type ${box.type ?? '-'}, state ${box.state ?? '-'}`);
    const env = [];
    if ('temp' in box) env.push(`${temp(box.temp)} °C`);
    if ('humidity' in box) env.push(`${temp(box.humidity)} %RH`);
    setText(entry.env, env.join('  '));
    const materials = Array.isArray(box.materials) ? box.materials : [];
    materials.forEach((m, i) => {
      const slot = entry.slots[i];
      if (!slot) return;
      paintSwatch(slot.swatch, m.color);
      const empty = Number(m.state) === 0 && !present(m.type) && !present(m.name);
      setText(slot.title, present(m.name) ? m.name : empty ? 'empty' : '(no name)');
      const sub = [present(m.vendor) ? m.vendor : 'no vendor', present(m.type) ? m.type : 'no type', `slot ${m.id}`];
      if (m.state !== undefined) sub.push(`state ${m.state}`);
      if (present(m.rfid)) sub.push(`rfid ${m.rfid}`);
      setText(slot.sub, sub.join(', '));
      syncValue(slot.pct, m.percent);
      const p = Math.max(0, Math.min(100, Number(m.percent) || 0));
      slot.fill.style.width = `${p}%`;
      const selected = Boolean(m.selected);
      slot.li.classList.toggle('selected', selected);
      slot.li.classList.toggle('is-empty', empty);
    });
  }

  function renderCfs(s, sim, tele, profile) {
    const chip = $('cfs-connect');
    setText(chip, tele.cfsConnect === undefined ? 'cfsConnect not reported' : `cfsConnect ${tele.cfsConnect}`);
    const ccls = `chip ${Number(tele.cfsConnect) === 1 ? 'l-up' : 'l-off'}`;
    if (chip.className !== ccls) chip.className = ccls;
    syncValue($('cfs-echo'), sim.cfs_echo_colour);
    setText($('cfs-note'), profile.cfs_capable === false
      ? 'This firmware does not know the CFS: no cfsConnect, and boxsInfo requests go unanswered.'
      : '');

    const container = $('cfs-boxes');
    const boxes = Array.isArray(s.cfs) ? s.cfs : [];
    if (boxes.length) container.querySelectorAll('.none').forEach((n) => n.remove());
    const seen = new Set();
    const keep = new Set();
    boxes.forEach((box, index) => {
      if (!box || typeof box !== 'object') return;
      const key = boxKey(box, index, seen);
      keep.add(key);
      const sig = boxSignature(box);
      let entry = boxCards.get(key);
      if (!entry || entry.sig !== sig) {
        const fresh = buildBoxCard(box);
        fresh.sig = sig;
        if (entry) entry.card.replaceWith(fresh.card);
        entry = fresh;
        boxCards.set(key, entry);
      }
      updateBoxCard(entry, box);
      const at = container.children[keep.size - 1];
      if (at !== entry.card) container.insertBefore(entry.card, at || null);
    });
    for (const [key, entry] of boxCards) {
      if (!keep.has(key)) {
        entry.card.remove();
        boxCards.delete(key);
      }
    }
    if (!boxes.length && !container.querySelector('.none')) container.append(el('p', 'muted none', 'No boxes.'));
  }

  // ---------------------------------------------------------------- overrides
  let overridesSig = null;
  function renderOverrides(overrides) {
    const sig = JSON.stringify(overrides);
    if (sig === overridesSig) return;
    overridesSig = sig;
    const list = $('ovr-list');
    const keys = Object.keys(overrides).sort();
    if (!keys.length) {
      list.replaceChildren(el('li', 'muted none', 'No overrides.'));
      return;
    }
    list.replaceChildren(...keys.map((key) => {
      const li = el('li');
      li.append(el('span', 'mono key', key), el('span', `mono ${valueClass(overrides[key], true)}`, JSON.stringify(overrides[key])));
      const rm = el('button', 'small', 'Remove');
      rm.type = 'button';
      rm.dataset.ovrRemove = key;
      rm.title = `Remove the override on ${key}`;
      li.append(rm);
      return li;
    }));
  }

  // ---------------------------------------------------------------- timed blanks
  const blankRows = new Map();
  function renderTimedBlanks(blanks) {
    const list = $('blank-list');
    const keys = Object.keys(blanks).sort();
    for (const [key, row] of blankRows) {
      if (!(key in blanks)) {
        row.li.remove();
        blankRows.delete(key);
      }
    }
    keys.forEach((key, index) => {
      let row = blankRows.get(key);
      if (!row) {
        const li = el('li');
        const left = el('span', 'mono muted left');
        const rm = el('button', 'small', 'Remove');
        rm.type = 'button';
        rm.dataset.ovrRemove = key;
        rm.title = `Stop blanking ${key} now`;
        li.append(el('span', 'mono key', key), el('span', 'mono val v-blank', '""'), left, rm);
        row = { li, left };
        blankRows.set(key, row);
      }
      setText(row.left, `${Math.max(0, Math.ceil(Number(blanks[key]) || 0))} s left`);
      if (list.children[index] !== row.li) list.insertBefore(row.li, list.children[index] || null);
    });
    $('blank-none').hidden = keys.length > 0;
  }

  // ---------------------------------------------------------------- events
  let eventsSig = null;
  function renderEvents(events) {
    const lastEvent = events[events.length - 1];
    const sig = `${events.length}|${lastEvent ? `${lastEvent.t}|${lastEvent.text}` : ''}`;
    if (sig === eventsSig) return;
    eventsSig = sig;
    const list = $('events');
    if (!events.length) {
      list.replaceChildren(el('li', 'muted none', 'No events yet.'));
      return;
    }
    list.replaceChildren(...events.slice().reverse().map((ev) => {
      const li = el('li');
      li.append(el('time', 'mono muted', clockTime(ev.t)), el('span', null, ev.text));
      li.firstChild.dateTime = new Date(ev.t * 1000).toISOString();
      return li;
    }));
  }

  // ================================================================ log
  const logMeta = new WeakMap();

  function logRow(item) {
    const row = el('div', `lrow d-${item.dir}`);
    const head = el('div', 'lhead');
    head.append(
      el('span', 'l-time mono', clockTime(item.t, true)),
      el('span', `l-dir tag d-${item.dir}`, item.dir),
      el('span', 'l-peer mono', item.peer),
      el('span', 'l-size mono muted', `${item.size} B`),
      el('span', 'l-data mono', item.data.length > 400 ? `${item.data.slice(0, 400)}...` : item.data),
    );
    if (item.note) head.append(el('span', 'l-note', item.note));
    row.append(head);
    logMeta.set(row, {
      item,
      dir: item.dir,
      search: `${item.data}\n${item.note || ''}\n${item.peer}`.toLowerCase(),
    });
    return row;
  }

  function logSeparator(text) {
    const row = el('div', 'lrow l-sep', text);
    logMeta.set(row, { dir: 'sep', search: text.toLowerCase() });
    return row;
  }

  function logFilter() {
    return {
      dirs: { in: $('lf-in').checked, out: $('lf-out').checked, note: $('lf-note').checked, sep: true },
      q: $('lf-text').value.trim().toLowerCase(),
    };
  }

  function applyLogFilterTo(row, f) {
    const meta = logMeta.get(row);
    if (!meta) return true;
    const visible = f.dirs[meta.dir] !== false && (!f.q || meta.search.includes(f.q));
    if (row.hidden === visible) row.hidden = !visible;
    return visible;
  }

  function updateLogCount() {
    const rows = $('log').children;
    let shown = 0;
    for (const row of rows) if (!row.hidden) shown += 1;
    setText($('log-count'), shown === rows.length ? `${rows.length} rows` : `${shown} of ${rows.length} rows`);
  }

  function refilterLog() {
    const f = logFilter();
    for (const row of $('log').children) applyLogFilterTo(row, f);
    updateLogCount();
  }

  function renderLog(r) {
    if (!r || typeof r.seq !== 'number') return;
    const box = $('log');
    if (r.seq < logSeq) {
      // The simulator process restarted: its sequence starts over.
      appendLog([logSeparator(`log restarted at ${clockTime(Date.now() / 1000)}`)]);
      logSeq = 0;
      pollAgain = true;
      return;
    }
    const items = Array.isArray(r.items) ? r.items : [];
    const rows = [];
    if (logSeq > 0 && items.length && items[0].seq > logSeq + 1) {
      rows.push(logSeparator(`${items[0].seq - logSeq - 1} messages not shown: the simulator keeps only its last few hundred`));
    }
    for (const item of items) rows.push(logRow(item));
    logSeq = Math.max(logSeq, r.seq);
    if (rows.length) appendLog(rows);
    else if (!box.children.length) updateLogCount();
  }

  function appendLog(rows) {
    const box = $('log');
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    const f = logFilter();
    const frag = document.createDocumentFragment();
    for (const row of rows) {
      applyLogFilterTo(row, f);
      frag.append(row);
    }
    box.append(frag);
    while (box.children.length > LOG_CAP) box.firstElementChild.remove();
    if ($('lf-follow').checked && atBottom) box.scrollTop = box.scrollHeight;
    updateLogCount();
  }

  function prettyData(text) {
    try {
      return JSON.stringify(JSON.parse(text), null, 2);
    } catch (_err) {
      return text;
    }
  }

  function toggleLogRow(row) {
    const meta = logMeta.get(row);
    if (!meta || !meta.item) return;
    let pre = row.querySelector('pre');
    if (!pre) {
      const { item } = meta;
      pre = el('pre', 'l-body');
      let text = prettyData(item.data);
      if (text === item.data && item.data.endsWith('...') && item.size > item.data.length - 3) {
        text = `${item.data}\n\n(cut by the simulator at 4000 of ${item.size} characters, so not valid JSON)`;
      }
      if (item.note) text += `\n\nnote: ${item.note}`;
      pre.textContent = text;
      pre.hidden = true;
      row.append(pre);
    }
    pre.hidden = !pre.hidden;
    row.classList.toggle('open', !pre.hidden);
  }

  // ================================================================ inputs
  const intOf = (input, name, { optional = false } = {}) => {
    const raw = input.value.trim();
    if (raw === '') {
      if (optional) return undefined;
      throw new Error(`${name} is required`);
    }
    const n = Number(raw);
    if (!Number.isFinite(n) || !Number.isInteger(n)) throw new Error(`${name} must be a whole number`);
    return n;
  };
  const floatOf = (input, name, { optional = false } = {}) => {
    const raw = input.value.trim();
    if (raw === '') {
      if (optional) return undefined;
      throw new Error(`${name} is required`);
    }
    const n = Number(raw);
    if (!Number.isFinite(n)) throw new Error(`${name} must be a number`);
    return n;
  };
  // Runs `build`, toasting a validation error instead of sending.
  const guarded = (action, build, button) => {
    let args;
    try {
      args = build();
    } catch (err) {
      toast(`${action}: ${err.message}`, 'error');
      return null;
    }
    return act(action, args, button);
  };
  const markDirty = (input) => input.addEventListener('input', () => {
    input.dataset.dirty = '1';
  });
  const settle = (input) => {
    input.dataset.hold = String(performance.now());
    delete input.dataset.dirty;
  };

  function bindSetting(input, key, read) {
    markDirty(input);
    input.addEventListener('change', async () => {
      input.dataset.dirty = '1';
      let value;
      try {
        value = read(input);
      } catch (err) {
        toast(`settings: ${err.message}`, 'error');
        delete input.dataset.dirty;
        return;
      }
      await act('settings', { [key]: value });
      settle(input);
    });
  }

  function wire() {
    // Plain buttons: data-act, optional static data-args.
    document.addEventListener('click', (e) => {
      const button = e.target.closest('button[data-act]');
      if (!button) return;
      let args = {};
      if (button.dataset.args) {
        try {
          args = JSON.parse(button.dataset.args);
        } catch (_err) {
          args = {};
        }
      }
      act(button.dataset.act, args, button);
    });

    // Job
    $('f-start').addEventListener('submit', (e) => {
      e.preventDefault();
      const form = e.currentTarget;
      const button = form.querySelector('button[type="submit"]');
      guarded('start_print', () => {
        const args = { name: form.elements.name.value.trim() || 'demo.gcode' };
        const seconds = floatOf(form.elements.seconds, 'seconds', { optional: true });
        const layers = intOf(form.elements.layers, 'layers', { optional: true });
        const objects = intOf(form.elements.objects, 'objects', { optional: true });
        if (seconds !== undefined) args.seconds = seconds;
        if (layers !== undefined) args.layers = layers;
        if (objects !== undefined) args.objects = objects;
        const selfTest = form.elements.self_test.value;
        if (selfTest) args.self_test = selfTest === 'yes';
        return args;
      }, button);
    });
    $('btn-stop').addEventListener('click', (e) => {
      const style = $('stop-style').value;
      act('stop', style ? { style } : {}, e.currentTarget);
    });
    $('btn-swap').addEventListener('click', (e) => {
      guarded('cfs_swap', () => ({ seconds: floatOf($('swap-seconds'), 'seconds') }), e.currentTarget);
    });

    // Faults
    $('btn-err-set').addEventListener('click', (e) => {
      guarded('set_error', () => ({
        code: intOf($('err-code'), 'code'),
        key: intOf($('err-key'), 'key', { optional: true }) ?? 0,
      }), e.currentTarget);
    });
    $('btn-resolve').addEventListener('click', (e) => {
      act('resolve_runout', { resume: $('resolve-resume').checked }, e.currentTarget);
    });
    $('btn-home').addEventListener('click', (e) => {
      guarded('home', () => ({
        axes: ($('home-axes').value.trim() || 'XYZ').toUpperCase(),
        seconds: floatOf($('home-seconds'), 'seconds', { optional: true }) ?? 4,
      }), e.currentTarget);
    });
    $('btn-blank').addEventListener('click', (e) => {
      guarded('blank_fields', () => {
        const fields = $('blank-fields').value.split(/[\s,]+/).filter(Boolean);
        const args = { seconds: floatOf($('blank-seconds'), 'seconds', { optional: true }) ?? 20 };
        if (fields.length) args.fields = fields;
        return args;
      }, e.currentTarget);
    });
    $('btn-power-off').addEventListener('click', (e) => act('power_off', {}, e.currentTarget));
    $('btn-power-on').addEventListener('click', (e) => {
      guarded('power_on', () => ({
        boot_blanks: floatOf($('boot-blanks'), 'boot blanks', { optional: true }) ?? 0,
      }), e.currentTarget);
    });
    bindSetting($('set-silent'), 'silent', (i) => i.checked);
    bindSetting($('set-reject'), 'reject_sets', (i) => i.checked);
    bindSetting($('set-ffd'), 'first_frame_delay', (i) => floatOf(i, 'first frame delay', { optional: true }) ?? 0);

    // Settings
    bindSetting($('s-frames'), 'frames', (i) => i.value);
    bindSetting($('s-heartbeat'), 'printer_heartbeat', (i) => i.checked);
    bindSetting($('s-stop'), 'stop_style', (i) => i.value);
    bindSetting($('s-selftest'), 'self_test_style', (i) => i.value);
    bindSetting($('s-gcode'), 'gcode_listing', (i) => i.value);
    bindSetting($('s-finreset'), 'finished_reset_seconds',
      (i) => floatOf(i, 'finished reset seconds', { optional: true }) ?? 0);

    // Controls
    for (const which of ['nozzle', 'bed', 'box']) {
      const input = $(`c-${which}`);
      const button = document.querySelector(`button[data-target="${which}"]`);
      const send = () => guarded('targets', () => ({ [which]: floatOf(input, `${which} target`) }), button);
      button.addEventListener('click', send);
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') send();
      });
    }
    $('c-light-on').addEventListener('click', (e) => act('light', { on: true }, e.currentTarget));
    $('c-light-off').addEventListener('click', (e) => act('light', { on: false }, e.currentTarget));

    // CFS
    $('cfs-echo').addEventListener('change', async (e) => {
      const select = e.currentTarget;
      select.dataset.dirty = '1';
      await act('cfs_echo', { mode: select.value });
      settle(select);
    });
    const boxes = $('cfs-boxes');
    boxes.addEventListener('click', (e) => {
      const pick = e.target.closest('[data-cfs-select]');
      if (pick) {
        const [box, slot] = pick.dataset.cfsSelect.split(':').map(Number);
        act('cfs_select', { box_id: box, slot_id: slot }, pick);
        return;
      }
      const remove = e.target.closest('[data-cfs-remove]');
      if (remove) act('cfs_remove_box', { box_id: Number(remove.dataset.cfsRemove) }, remove);
    });
    boxes.addEventListener('input', (e) => {
      if (e.target.dataset.cfsPercent) e.target.dataset.dirty = '1';
    });
    boxes.addEventListener('change', async (e) => {
      const input = e.target;
      if (!input.dataset.cfsPercent) return;
      const [box, slot] = input.dataset.cfsPercent.split(':').map(Number);
      input.dataset.dirty = '1';
      await guarded('cfs_percent', () => {
        const percent = intOf(input, 'percent');
        if (percent < 0 || percent > 100) throw new Error('percent is 0 to 100');
        return { box_id: box, slot_id: slot, percent };
      });
      settle(input);
    });

    // Overrides
    $('f-ovr').addEventListener('submit', (e) => {
      e.preventDefault();
      const form = e.currentTarget;
      const key = form.elements.key.value.trim();
      if (!key) {
        toast('set_override: a field name is required', 'error');
        return;
      }
      const raw = form.elements.value.value;
      let value = raw;
      try {
        value = JSON.parse(raw);
      } catch (_err) {
        value = raw;
      }
      act('set_override', { key, value }, form.querySelector('button[type="submit"]')).then((res) => {
        if (res) form.elements.value.value = '';
      });
    });
    $('p-overrides').addEventListener('click', (e) => {
      const button = e.target.closest('[data-ovr-remove]');
      if (button) act('set_override', { key: button.dataset.ovrRemove, value: null }, button);
    });

    // Model switch: explicit button and a confirm, since it drops every client.
    $('h-model').addEventListener('change', (e) => {
      const select = e.currentTarget;
      if (select.value === currentProfileKey) delete select.dataset.dirty;
      else select.dataset.dirty = '1';
      $('h-switch').disabled = select.value === currentProfileKey;
    });
    $('h-switch').addEventListener('click', async (e) => {
      const select = $('h-model');
      const key = select.value;
      if (!key || key === currentProfileKey) return;
      const title = select.selectedOptions[0] ? select.selectedOptions[0].textContent : key;
      const clients = `${sessionCount} connected client${sessionCount === 1 ? '' : 's'}`;
      const ok = window.confirm(
        `Switch the simulated printer to ${title}?\n\n`
        + `It powers off first, which drops ${clients} without a close frame, `
        + 'then powers on clean as the new model.',
      );
      if (!ok) {
        delete select.dataset.dirty;
        select.value = currentProfileKey;
        $('h-switch').disabled = true;
        return;
      }
      await act('switch_profile', { key }, e.currentTarget);
      settle(select);
    });

    // Telemetry filter
    $('tele-filter').addEventListener('input', applyTeleFilter);

    // Log
    for (const id of ['lf-in', 'lf-out', 'lf-note']) $(id).addEventListener('change', refilterLog);
    $('lf-text').addEventListener('input', refilterLog);
    $('lf-follow').addEventListener('change', (e) => {
      if (e.currentTarget.checked) $('log').scrollTop = $('log').scrollHeight;
    });
    $('lf-clear').addEventListener('click', () => {
      $('log').replaceChildren();
      updateLogCount();
    });
    $('log').addEventListener('click', (e) => {
      const head = e.target.closest('.lhead');
      if (head) toggleLogRow(head.parentElement);
    });
  }

  wire();
  poll();
})();
