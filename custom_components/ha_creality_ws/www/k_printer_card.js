const CARD_TAG = "k-printer-card";
const EDITOR_TAG = "k-printer-card-editor";

const clamp = (v, a, b) => Math.min(Math.max(v, a), b);
const mdi = (name) => `mdi:${name}`;
const normStr = (x) => String(x ?? "").toLowerCase();

function fmtTimeLeft(seconds) {
  const s = Number(seconds) || 0;
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2,"0")}:${String(sec).padStart(2,"0")}`;
  if (m > 0) return `${m}:${String(sec).padStart(2,"0")}`;
  return `${sec}s`;
}
function computeIcon(status) {
  const st = normStr(status);
  if (["off","unknown","stopped"].includes(st)) return mdi("printer-3d-off");
  if (["printing","resuming","pausing","paused"].includes(st)) return mdi("printer-3d-nozzle");
  if (st === "error") return mdi("close-octagon");
  if (st === "self-testing") return mdi("cogs");
  return mdi("printer-3d");
}
function computeColor(status) {
  const st = normStr(status);
  if (["off","unknown","stopped"].includes(st)) return "var(--secondary-text-color)";
  if (["paused","pausing"].includes(st)) return "#fc6d09";
  if (st === "error") return "var(--error-color)";
  if (["printing","resuming","processing"].includes(st)) return "var(--primary-color)";
  if (["idle","completed"].includes(st)) return "var(--success-color, #4caf50)";
  if (st === "self-testing") return "var(--info-color, #2196f3)";
  return "var(--secondary-text-color)";
}

class KPrinterCard extends HTMLElement {
  static getStubConfig() {
    return {
      name: "3D Printer",
      camera: "", status: "", progress: "", time_left: "",
      nozzle: "", bed: "", box: "",
      layer: "", total_layers: "",
      light: "", pause_btn: "", resume_btn: "", stop_btn: "",
    };
  }
  static getConfigElement() { return document.createElement(EDITOR_TAG); }

  setConfig(config) {
    this._cfg = { ...KPrinterCard.getStubConfig(), ...(config || {}) };
    if (!this._root) {
      this._root = this.attachShadow({ mode: "open" });
      this._render();
    } else {
      this._render();
    }
  }
  set hass(hass) {
    this._hass = hass;
    if (this._root) this._update();
  }
  getCardSize() { return 3; }

  _render() {
    if (!this._root) return;

    const style = `
      /* inherit HA fonts & typography */
      :host { font: inherit; color: var(--primary-text-color); }

      /* unify horizontal padding so right edges line up */
      :host { --row-xpad: 6px; }

      .card {
        border-radius: var(--ha-card-border-radius, 12px);
        background: var(--card-background-color);
        color: var(--primary-text-color);
        box-shadow: var(--ha-card-box-shadow, 0 2px 4px rgba(0,0,0,.2));
        padding: 10px var(--row-xpad) 10px var(--row-xpad);
        display: grid;
        grid-template-rows: auto auto;
        gap: 6px;
      }

      /* top row */
      .row-top {
        display: grid;
        grid-template-columns: 1fr auto;
        align-items: center;
        gap: 8px;
        padding: 0 var(--row-xpad);
      }
      .title { display:flex; align-items:center; gap:10px; min-height:44px; }

      /* icon + progress ring */
      .shape { position:relative; width:40px; height:40px; border-radius:50%;
        display:grid; place-items:center;
        background: radial-gradient(var(--card-background-color) 62%, transparent 0);
      }
      .ring { position:absolute; inset:0; border-radius:50%;
        mask: radial-gradient(circle at 50% 50%, transparent 54%, black 55%);
        -webkit-mask: radial-gradient(circle at 50% 50%, transparent 54%, black 55%);
        background: conic-gradient(var(--ring-color, var(--primary-color)) var(--ring-pct,0%),
                                  rgba(128,128,128,.25) var(--ring-pct,0%));
      }
      ha-icon { --mdc-icon-size:24px; width:24px; height:24px; color: var(--icon-color); }

      .name { font-weight:600; font-size:.95rem; line-height:1.2; }
      .secondary { color:var(--secondary-text-color); font-size:.8rem; }

      /* action chips – align right edge to telemetry via the same side padding */
      .chips {
        display:flex; gap:8px; justify-content:flex-end; flex-wrap:nowrap;
        padding: 0 var(--row-xpad);
      }
      .chip {
        display:inline-flex; align-items:center; justify-content:center;
        gap:6px; min-width:38px; height:34px;
        border-radius:18px; padding:0 10px;
        font-size:.8rem; background:var(--chip-bg, rgba(128,128,128,.14));
        color:var(--chip-fg, var(--primary-text-color));
        cursor:pointer; user-select:none; border:none; outline:none;
      }
      .chip[hidden]{ display:none !important; }
      .chip:active { transform: translateY(1px); }
      .chip.danger { --chip-bg: rgba(244, 67, 54, .95); --chip-fg:#fff; }
      .chip.warn   { --chip-bg: rgba(252, 109, 9, .90);  --chip-fg:#fff; }
      .chip.ok     { --chip-bg: rgba(76, 175, 80, .90);  --chip-fg:#fff; }
      .chip.light-on  { --chip-bg: rgba(255, 235, 59, .95); }
      .chip.light-off { --chip-bg: rgba(150,150,150,.35); }

      /* telemetry row – single line, same right padding, tighter pills */
      .telemetry {
        display:flex;
        gap:6px;
        justify-content:center;   /* was: flex-start */
        flex-wrap:nowrap;
        padding: 0 var(--row-xpad);
        min-width:0;
        overflow:hidden;
      }
      .pill {
        display:inline-flex; align-items:center; gap:6px;
        padding:6px 10px; border-radius:14px;
        background:rgba(127,127,127,.12);
        font-size:.8rem; border:1px solid rgba(255,255,255,0.08);
        white-space:nowrap; flex:0 0 auto;
      }
      .pill ha-icon { --mdc-icon-size:16px; width:16px; height:16px; }

      .click { cursor:pointer; }

    `;

    this._root.innerHTML = `
      <ha-card class="card">
        <style>${style}</style>
        <div class="row-top">
          <div class="title click" id="more" role="button" tabindex="0">
            <div class="shape">
              <div class="ring" id="ring"></div>
              <ha-icon id="icon"></ha-icon>
            </div>
            <div>
              <div class="name" id="name"></div>
              <div class="secondary" id="secondary"></div>
            </div>
          </div>
          <div class="chips">
            <button class="chip warn"   id="pause"  title="Pause"><ha-icon icon="mdi:pause"></ha-icon></button>
            <button class="chip ok"     id="resume" title="Resume"><ha-icon icon="mdi:play"></ha-icon></button>
            <button class="chip danger" id="stop"   title="Stop"><ha-icon icon="mdi:stop"></ha-icon></button>
            <button class="chip"        id="light"  title="Light"><ha-icon icon="mdi:lightbulb"></ha-icon></button>
          </div>
        </div>

        <div class="telemetry">
          <div class="pill"><ha-icon icon="mdi:printer-3d-nozzle-heat"></ha-icon><span id="nozzle"></span></div>
          <div class="pill"><ha-icon icon="mdi:heating-coil"></ha-icon><span id="bed"></span></div>
          <div class="pill"><ha-icon icon="mdi:thermometer"></ha-icon><span id="box"></span></div>
          <div class="pill"><ha-icon icon="mdi:progress-clock"></ha-icon><span id="time"></span></div>
          <div class="pill"><ha-icon icon="mdi:layers-triple"></ha-icon><span id="layers"></span></div>
        </div>
      </ha-card>
    `;

    // events
    const fireMoreInfo = (eid) => {
      if (!eid) return;
      this.dispatchEvent(new CustomEvent("hass-more-info", {
        detail: { entityId: eid },
        bubbles: true,
        composed: true,
      }));
    };

    this._root.getElementById("more")?.addEventListener("click", () => {
      const eid = this._cfg.camera || this._cfg.status || this._cfg.progress;
      fireMoreInfo(eid);
    });
    this._root.getElementById("more")?.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        const eid = this._cfg.camera || this._cfg.status || this._cfg.progress;
        fireMoreInfo(eid);
      }
    });

    this._root.getElementById("pause")?.addEventListener("click", () => this._pressButtonEntity(this._cfg.pause_btn) );
    this._root.getElementById("resume")?.addEventListener("click", () => this._pressButtonEntity(this._cfg.resume_btn) );
    this._root.getElementById("stop")?.addEventListener("click", () => this._pressButtonEntity(this._cfg.stop_btn) );
    this._root.getElementById("light")?.addEventListener("click", () => this._toggleEntity(this._cfg.light) );

    this._update();
  }

  async _pressButtonEntity(eid) {
    if (!this._hass || !eid) return;
    await this._hass.callService("button", "press", { entity_id: eid });
  }
  async _toggleEntity(eid) {
    if (!this._hass || !eid) return;
    const st = this._hass.states[eid];
    const domain = (eid.split(".")[0] || "").toLowerCase();
    if (domain === "switch" || domain === "light") {
      await this._hass.callService(domain, st?.state === "on" ? "turn_off" : "turn_on", { entity_id: eid });
    } else {
      await this._hass.callService("homeassistant", "toggle", { entity_id: eid });
    }
  }

  _update() {
    if (!this._root) return;
    const g = (eid) => this._hass?.states?.[eid]?.state;
    const gNum = (eid) => Number(g(eid));

    const name = this._cfg.name || "3D Printer";
    const status = g(this._cfg.status) ?? "unknown";
    const pct = clamp(Number.isFinite(gNum(this._cfg.progress)) ? gNum(this._cfg.progress) : 0, 0, 100);
    const timeLeft = gNum(this._cfg.time_left) || 0;
    const nozzle = gNum(this._cfg.nozzle);
    const bed = gNum(this._cfg.bed);
    const box = gNum(this._cfg.box);
    const layer = (g(this._cfg.layer) ?? "") + "";
    const totalLayers = (g(this._cfg.total_layers) ?? "") + "";
    const lightState = g(this._cfg.light);

    const st = normStr(status);
    const isPrinting = ["printing","resuming","pausing"].includes(st);
    const isPaused = st === "paused";
    const showStop = isPrinting || isPaused || st === "self-testing";
    const showLight = !["off","unknown"].includes(st);

    // Title/status
    this._root.getElementById("name").textContent = name;
    const proper = status ? status[0].toUpperCase() + status.slice(1) : "Unknown";
    const sec = (isPrinting || isPaused) ? `${pct}% ${proper}` : proper;
    this._root.getElementById("secondary").textContent = sec;

    // Icon & ring
    const iconEl = this._root.getElementById("icon");
    iconEl.setAttribute("icon", computeIcon(status));
    iconEl.style.setProperty("--icon-color", computeColor(status));
    const ring = this._root.getElementById("ring");
    ring.style.setProperty("--ring-pct", isPrinting || isPaused ? `${pct}%` : "0%");
    ring.style.setProperty("--ring-color", computeColor(status));

    // Chips
    this._root.getElementById("pause").hidden = !isPrinting;
    this._root.getElementById("resume").hidden = !isPaused;
    this._root.getElementById("stop").hidden = !showStop;

    const lightBtn = this._root.getElementById("light");
    lightBtn.hidden = !showLight;
    lightBtn.classList.toggle("light-on", lightState === "on");
    lightBtn.classList.toggle("light-off", lightState !== "on");

    // Telemetry
    const n = Number.isFinite(nozzle) ? `${nozzle.toFixed(1)} °C` : "—";
    const b = Number.isFinite(bed)    ? `${bed.toFixed(1)} °C`    : "—";
    const bx = Number.isFinite(box)   ? `${box.toFixed(1)} °C`    : "—";
    this._root.getElementById("nozzle").textContent = n;
    this._root.getElementById("bed").textContent    = b;
    this._root.getElementById("box").textContent    = bx;
    this._root.getElementById("time").textContent   = fmtTimeLeft(timeLeft);
    this._root.getElementById("layers").textContent = `${layer || "?"}/${totalLayers || "?"}`;
  }
}
customElements.define(CARD_TAG, KPrinterCard);

/* Simple editor */
class KPrinterCardEditor extends HTMLElement {
  set hass(hass) { this._hass = hass; if (this._form) this._form.hass = hass; }
  setConfig(config) { this._cfg = { ...KPrinterCard.getStubConfig(), ...(config || {}) }; this._render(); }
  connectedCallback() { if (!this._root) { this._root = this.attachShadow({ mode: "open" }); this._render(); } }
  _render() {
    if (!this._root) return;
    if (!this._form) {
      this._root.innerHTML = `<ha-form id="f"></ha-form>`;
      this._form = this._root.getElementById("f");
      this._form.hass = this._hass;
      this._form.addEventListener("value-changed", (ev) => {
        const val = ev.detail?.value || {};
        this._cfg = val;
        clearTimeout(this._t);
        this._t = setTimeout(() => {
          this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: val } }));
        }, 120);
      });
    }
    this._form.schema = [
      { name: "name",         label: "Printer Name", selector: { text: {} } },
      { name: "camera",       label: "Camera", selector: { entity: { domain: "camera" } } },
      { name: "status",       label: "Print Status Sensor", selector: { entity: { domain: "sensor" } } },
      { name: "progress",     label: "Print Progress Sensor (%)", selector: { entity: { domain: "sensor" } } },
      { name: "time_left",    label: "Time Left Sensor (seconds)", selector: { entity: { domain: "sensor" } } },
      { name: "nozzle",       label: "Nozzle Temperature Sensor", selector: { entity: { domain: "sensor" } } },
      { name: "bed",          label: "Bed Temperature Sensor", selector: { entity: { domain: "sensor" } } },
      { name: "box",          label: "Enclosure Temperature Sensor", selector: { entity: { domain: "sensor" } } },
      { name: "layer",        label: "Current Layer Sensor", selector: { entity: { domain: "sensor" } } },
      { name: "total_layers", label: "Total Layers Sensor", selector: { entity: { domain: "sensor" } } },
      { name: "light",        label: "Light Switch", selector: { entity: { domain: "switch" } } },
      { name: "pause_btn",    label: "Pause Button", selector: { entity: { domain: "button" } } },
      { name: "resume_btn",   label: "Resume Button", selector: { entity: { domain: "button" } } },
      { name: "stop_btn",     label: "Stop Button", selector: { entity: { domain: "button" } } },
    ];
    this._form.data = this._cfg;
  }
}
customElements.define(EDITOR_TAG, KPrinterCardEditor);

try {
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: CARD_TAG,
    name: "Creality Printer Card",
    description: "Standalone card for Creality K-Series printers",
    preview: true,
  });
} catch (_) {}
