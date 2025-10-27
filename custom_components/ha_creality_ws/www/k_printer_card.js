const CARD_TAG = "k-printer-card";
const EDITOR_TAG = "k-printer-card-editor";

const clamp = (v, a, b) => Math.min(Math.max(v, a), b);
const mdi = (name) => `mdi:${name}`;
const normStr = (x) => String(x ?? "").toLowerCase();

// Theme persistence utilities
const THEME_STORAGE_KEY = "k-printer-card-themes";

// Color conversion utilities
function rgbaToHex(rgba) {
  if (!rgba || rgba.startsWith('#')) return rgba;
  
  // Handle rgba() format
  const match = rgba.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*[\d.]+)?\)/);
  if (match) {
    const r = parseInt(match[1]);
    const g = parseInt(match[2]);
    const b = parseInt(match[3]);
    return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`;
  }
  
  // Handle CSS variables
  if (rgba.startsWith('var(')) {
    return '#000000'; // fallback
  }
  
  return '#000000';
}

function hexToRgba(hex, alpha = 1) {
  if (!hex || !hex.startsWith('#')) return hex;
  
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/**
 * Save theme configuration to localStorage for persistence across card updates
 * @param {string} cardId - Unique identifier for the card
 * @param {Object} theme - Theme configuration object
 */
function saveThemeToStorage(cardId, theme) {
  try {
    const themes = JSON.parse(localStorage.getItem(THEME_STORAGE_KEY) || "{}");
    themes[cardId] = theme;
    localStorage.setItem(THEME_STORAGE_KEY, JSON.stringify(themes));
  } catch (e) {
    console.warn("Failed to save theme to localStorage:", e);
  }
}

/**
 * Load theme configuration from localStorage
 * @param {string} cardId - Unique identifier for the card
 * @returns {Object|null} Theme configuration or null if not found
 */
function loadThemeFromStorage(cardId) {
  try {
    const themes = JSON.parse(localStorage.getItem(THEME_STORAGE_KEY) || "{}");
    return themes[cardId] || null;
  } catch (e) {
    console.warn("Failed to load theme from localStorage:", e);
    return null;
  }
}

/**
 * Generate a unique card ID based on configuration
 * @param {Object} config - Card configuration
 * @returns {string} Unique card identifier
 */
function generateCardId(config) {
  // Generate a unique ID based on the card configuration
  const key = `${config.name || "printer"}-${config.status || "unknown"}`;
  return btoa(key).replace(/[^a-zA-Z0-9]/g, '').substring(0, 16);
}

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
      // Theme customization options
      theme: {
        // Button backgrounds
        pause_bg: "rgba(252, 109, 9, .90)",
        resume_bg: "rgba(76, 175, 80, .90)",
        stop_bg: "rgba(244, 67, 54, .95)",
        light_on_bg: "rgba(255, 235, 59, .95)",
        light_off_bg: "rgba(150,150,150,.35)",
        // Button icon colors
        pause_icon: "#fff",
        resume_icon: "#fff",
        stop_icon: "#fff",
        light_icon_on: "#000",
        light_icon_off: "#000",
        // Status icon and progress circle
        status_icon: "auto", // auto, or specific color
        progress_ring: "auto", // auto, or specific color
        status_bg: "auto", // auto (transparent), or specific color
        // Telemetry colors
        telemetry_icon: "auto", // auto (inherit theme), or specific color
        telemetry_text: "auto" // auto (inherit theme), or specific color
      }
    };
  }
  static getConfigElement() { 
    const editor = document.createElement(EDITOR_TAG);
    return editor;
  }

  setConfig(config) {
    const defaultConfig = KPrinterCard.getStubConfig();
    this._cfg = { ...defaultConfig, ...(config || {}) };
    
    // Generate card ID for theme persistence
    this._cardId = generateCardId(this._cfg);
    
    // Load saved theme if no theme is provided in config
    if (!config?.theme) {
      const savedTheme = loadThemeFromStorage(this._cardId);
      if (savedTheme) {
        this._cfg.theme = { ...defaultConfig.theme, ...savedTheme };
      }
    } else {
      // Deep merge theme configuration
      this._cfg.theme = { ...defaultConfig.theme, ...config.theme };
      // Save theme to storage
      saveThemeToStorage(this._cardId, this._cfg.theme);
    }
    
    if (!this._root) {
      this._root = this.attachShadow({ mode: "open" });
    }
    
    // Always re-render when config changes to apply new theme
      this._render();
    
    // Apply theme after render to ensure DOM is ready
    this._applyTheme();
  }
  _applyTheme() {
    if (!this._root || !this._cfg.theme) {
      return;
    }
    
    // Re-render with updated CSS to apply theme changes
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._root) {
      // Apply theme first, then update
      this._applyTheme();
      this._update();
    }
  }
  getCardSize() { return 3; }

  _render() {
    if (!this._root) return;

    // Ensure theme is always properly initialized
    const defaultConfig = KPrinterCard.getStubConfig();
    this._cfg.theme = { ...defaultConfig.theme, ...(this._cfg.theme || {}) };
    
    // Apply theme variables to CSS custom properties
    const theme = this._cfg.theme;
    
    // Theme CSS custom properties - embedded directly in CSS
    const themeCSS = `
      :host {
        --pause-bg: ${theme.pause_bg || 'rgba(252, 109, 9, .90)'};
        --resume-bg: ${theme.resume_bg || 'rgba(76, 175, 80, .90)'};
        --stop-bg: ${theme.stop_bg || 'rgba(244, 67, 54, .95)'};
        --light-on-bg: ${theme.light_on_bg || 'rgba(255, 235, 59, .95)'};
        --light-off-bg: ${theme.light_off_bg || 'rgba(150,150,150,.35)'};
        --pause-icon: ${theme.pause_icon || '#fff'};
        --resume-icon: ${theme.resume_icon || '#fff'};
        --stop-icon: ${theme.stop_icon || '#fff'};
        --light-icon-on: ${theme.light_icon_on || '#000'};
        --light-icon-off: ${theme.light_icon_off || '#000'};
        --status-bg: ${theme.status_bg === 'auto' ? 'radial-gradient(var(--card-background-color) 62%, transparent 0)' : (theme.status_bg || 'radial-gradient(var(--card-background-color) 62%, transparent 0)')};
        --telemetry-icon: ${theme.telemetry_icon === 'auto' ? 'var(--secondary-text-color)' : (theme.telemetry_icon || 'var(--secondary-text-color)')};
        --telemetry-text: ${theme.telemetry_text === 'auto' ? 'var(--primary-text-color)' : (theme.telemetry_text || 'var(--primary-text-color)')};
      }
    `;
    

    const style = `
      /* Theme CSS custom properties */
      ${themeCSS}
      
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
        background: var(--status-bg, radial-gradient(var(--card-background-color) 62%, transparent 0));
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
      .chip.danger { --chip-bg: var(--stop-bg, rgba(244, 67, 54, .95)); --chip-fg: var(--stop-icon, #fff); }
      .chip.warn   { --chip-bg: var(--pause-bg, rgba(252, 109, 9, .90));  --chip-fg: var(--pause-icon, #fff); }
      .chip.ok     { --chip-bg: var(--resume-bg, rgba(76, 175, 80, .90));  --chip-fg: var(--resume-icon, #fff); }
      .chip.light-on  { --chip-bg: var(--light-on-bg, rgba(255, 235, 59, .95)); --chip-fg: var(--light-icon-on, #000); }
      .chip.light-off { --chip-bg: var(--light-off-bg, rgba(150,150,150,.35)); --chip-fg: var(--light-icon-off, #000); }

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
        color: var(--telemetry-text, var(--primary-text-color));
      }
      .pill ha-icon { --mdc-icon-size:16px; width:16px; height:16px; color: var(--telemetry-icon, var(--secondary-text-color)); }

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
    const theme = this._cfg.theme || {};
    const iconColor = theme.status_icon === "auto" ? computeColor(status) : theme.status_icon;
    iconEl.style.setProperty("--icon-color", iconColor);
    const ring = this._root.getElementById("ring");
    ring.style.setProperty("--ring-pct", isPrinting || isPaused ? `${pct}%` : "0%");
    const ringColor = theme.progress_ring === "auto" ? computeColor(status) : theme.progress_ring;
    ring.style.setProperty("--ring-color", ringColor);

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

/* Interactive theme editor */
class KPrinterCardEditor extends HTMLElement {
  set hass(hass) { this._hass = hass; if (this._entitiesForm) this._entitiesForm.hass = hass; }
  setConfig(config) { 
    const defaultConfig = KPrinterCard.getStubConfig();
    this._cfg = { ...defaultConfig, ...(config || {}) };
    
    // Always ensure theme is properly initialized
    this._cfg.theme = { ...defaultConfig.theme, ...(this._cfg.theme || {}) };
    
    if (this._root) {
      this._render(); 
    }
  }
  connectedCallback() { if (!this._root) { this._root = this.attachShadow({ mode: "open" }); this._render(); } }
  
  _render() {
    if (!this._root || !this._cfg) return;
    
    try {
    
    const style = `
      :host { display: block; }
      .editor-container { padding: 16px; max-width: 1200px; margin: 0 auto; }
      .tabs { display: flex; border-bottom: 1px solid var(--divider-color); margin-bottom: 16px; }
      .tab { padding: 8px 16px; cursor: pointer; border-bottom: 2px solid transparent; }
      .tab.active { border-bottom-color: var(--primary-color); color: var(--primary-color); }
      .tab-content { display: none; }
      .tab-content.active { display: block; }
      
      .theme-editor { display: block; }
      
      .theme-controls { 
        display: flex; 
        flex-direction: column; 
        gap: 16px;
      }
      
      .control-group {
        background: var(--card-background-color);
        border: 1px solid var(--divider-color);
        border-radius: 8px;
        padding: 12px;
      }
      
      .group-title {
        font-size: 14px;
        font-weight: 600;
        margin-bottom: 8px;
        color: var(--primary-text-color);
      }
      
      .clickable-element {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 8px;
        border: 1px solid transparent;
        border-radius: 4px;
        cursor: pointer;
        margin: 4px 0;
        transition: all 0.2s;
      }
      
      .clickable-element:hover {
        border-color: var(--primary-color);
        background: rgba(var(--rgb-primary-color), 0.1);
      }
      
      .element-preview {
        width: 24px;
        height: 24px;
        border-radius: 4px;
        border: 1px solid var(--divider-color);
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 12px;
      }
      
      .element-label {
        font-size: 12px;
        color: var(--primary-text-color);
        flex: 1;
      }
      
      .color-picker-inline {
        background: var(--card-background-color, #ffffff);
        border: 1px solid var(--divider-color, #ccc);
        border-radius: 8px;
        padding: 12px;
        margin: 8px 0;
        display: none;
        position: relative;
        z-index: 10;
      }
      
      .color-picker-inline.active {
        display: block;
      }
      
      
      .reset-btn { 
        background: var(--error-color);
        color: white;
        border: none;
        padding: 8px 16px;
        border-radius: 4px;
        cursor: pointer;
        margin-top: 16px;
        width: 100%;
      }
      
      .reset-btn:hover {
        opacity: 0.8;
      }
    `;
    
    this._root.innerHTML = `
      <style>${style}</style>
      <div class="editor-container">
        <h2 style="margin: 0 0 16px 0; font-size: 18px; color: var(--primary-text-color);">Creality Printer Card Configuration</h2>
        <div class="tabs">
          <div class="tab active" data-tab="entities">Entities</div>
          <div class="tab" data-tab="theme">Theme</div>
        </div>
        
        <div class="tab-content active" id="entities-tab">
          <ha-form id="entities-form"></ha-form>
        </div>
        
        <div class="tab-content" id="theme-tab">
          <div class="theme-editor">
            <div class="theme-controls">
              <div class="control-group">
                <div class="group-title">Action Buttons</div>
                <div class="clickable-element" data-theme="pause_bg" data-label="Pause Button Background">
                  <div class="element-preview" style="background: ${this._cfg.theme.pause_bg}"></div>
                  <div class="element-label">Pause Button Background</div>
                </div>
                <div class="color-picker-inline" id="color-picker-pause_bg">
                  <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
                    <div class="color-preview" style="width: 30px; height: 30px; border: 2px solid #ccc; border-radius: 4px; background: ${rgbaToHex(this._cfg.theme.pause_bg)}; cursor: pointer;" title="Click to open color picker"></div>
                    <input type="text" class="color-text" value="${rgbaToHex(this._cfg.theme.pause_bg)}" placeholder="#000000" style="flex: 1; padding: 6px; border: 1px solid #ccc; border-radius: 4px; font-family: monospace; font-size: 12px;">
                    <button class="save-color-btn" data-theme="pause_bg" style="padding: 6px 12px; background: var(--primary-color); color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Save</button>
                  </div>
                </div>
                <div class="clickable-element" data-theme="pause_icon" data-label="Pause Button Icon">
                  <div class="element-preview" style="background: ${this._cfg.theme.pause_icon}; color: ${this._cfg.theme.pause_icon}">⏸</div>
                  <div class="element-label">Pause Button Icon</div>
                </div>
                <div class="color-picker-inline" id="color-picker-pause_icon">
                  <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
                    <div class="color-preview" style="width: 30px; height: 30px; border: 2px solid #ccc; border-radius: 4px; background: ${rgbaToHex(this._cfg.theme.pause_icon)}; cursor: pointer;" title="Click to open color picker"></div>
                    <input type="text" class="color-text" value="${rgbaToHex(this._cfg.theme.pause_icon)}" placeholder="#000000" style="flex: 1; padding: 6px; border: 1px solid #ccc; border-radius: 4px; font-family: monospace; font-size: 12px;">
                    <button class="save-color-btn" data-theme="pause_icon" style="padding: 6px 12px; background: var(--primary-color); color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Save</button>
                  </div>
                </div>
                <div class="clickable-element" data-theme="resume_bg" data-label="Resume Button Background">
                  <div class="element-preview" style="background: ${this._cfg.theme.resume_bg}"></div>
                  <div class="element-label">Resume Button Background</div>
                </div>
                <div class="color-picker-inline" id="color-picker-resume_bg">
                  <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
                    <div class="color-preview" style="width: 30px; height: 30px; border: 2px solid #ccc; border-radius: 4px; background: ${rgbaToHex(this._cfg.theme.resume_bg)}; cursor: pointer;" title="Click to open color picker"></div>
                    <input type="text" class="color-text" value="${rgbaToHex(this._cfg.theme.resume_bg)}" placeholder="#000000" style="flex: 1; padding: 6px; border: 1px solid #ccc; border-radius: 4px; font-family: monospace; font-size: 12px;">
                    <button class="save-color-btn" data-theme="resume_bg" style="padding: 6px 12px; background: var(--primary-color); color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Save</button>
                  </div>
                </div>
                <div class="clickable-element" data-theme="resume_icon" data-label="Resume Button Icon">
                  <div class="element-preview" style="background: ${this._cfg.theme.resume_icon}; color: ${this._cfg.theme.resume_icon}">▶</div>
                  <div class="element-label">Resume Button Icon</div>
                </div>
                <div class="color-picker-inline" id="color-picker-resume_icon">
                  <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
                    <div class="color-preview" style="width: 30px; height: 30px; border: 2px solid #ccc; border-radius: 4px; background: ${rgbaToHex(this._cfg.theme.resume_icon)}; cursor: pointer;" title="Click to open color picker"></div>
                    <input type="text" class="color-text" value="${rgbaToHex(this._cfg.theme.resume_icon)}" placeholder="#000000" style="flex: 1; padding: 6px; border: 1px solid #ccc; border-radius: 4px; font-family: monospace; font-size: 12px;">
                    <button class="save-color-btn" data-theme="resume_icon" style="padding: 6px 12px; background: var(--primary-color); color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Save</button>
                  </div>
                </div>
                <div class="clickable-element" data-theme="stop_bg" data-label="Stop Button Background">
                  <div class="element-preview" style="background: ${this._cfg.theme.stop_bg}"></div>
                  <div class="element-label">Stop Button Background</div>
                </div>
                <div class="color-picker-inline" id="color-picker-stop_bg">
                  <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
                    <div class="color-preview" style="width: 30px; height: 30px; border: 2px solid #ccc; border-radius: 4px; background: ${rgbaToHex(this._cfg.theme.stop_bg)}; cursor: pointer;" title="Click to open color picker"></div>
                    <input type="text" class="color-text" value="${rgbaToHex(this._cfg.theme.stop_bg)}" placeholder="#000000" style="flex: 1; padding: 6px; border: 1px solid #ccc; border-radius: 4px; font-family: monospace; font-size: 12px;">
                    <button class="save-color-btn" data-theme="stop_bg" style="padding: 6px 12px; background: var(--primary-color); color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Save</button>
                  </div>
                </div>
                <div class="clickable-element" data-theme="stop_icon" data-label="Stop Button Icon">
                  <div class="element-preview" style="background: ${this._cfg.theme.stop_icon}; color: ${this._cfg.theme.stop_icon}">⏹</div>
                  <div class="element-label">Stop Button Icon</div>
                </div>
                <div class="color-picker-inline" id="color-picker-stop_icon">
                  <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
                    <div class="color-preview" style="width: 30px; height: 30px; border: 2px solid #ccc; border-radius: 4px; background: ${rgbaToHex(this._cfg.theme.stop_icon)}; cursor: pointer;" title="Click to open color picker"></div>
                    <input type="text" class="color-text" value="${rgbaToHex(this._cfg.theme.stop_icon)}" placeholder="#000000" style="flex: 1; padding: 6px; border: 1px solid #ccc; border-radius: 4px; font-family: monospace; font-size: 12px;">
                    <button class="save-color-btn" data-theme="stop_icon" style="padding: 6px 12px; background: var(--primary-color); color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Save</button>
                  </div>
                </div>
                <div class="clickable-element" data-theme="light_on_bg" data-label="Light On Background">
                  <div class="element-preview" style="background: ${this._cfg.theme.light_on_bg}"></div>
                  <div class="element-label">Light On Background</div>
                </div>
                <div class="color-picker-inline" id="color-picker-light_on_bg">
                  <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
                    <div class="color-preview" style="width: 30px; height: 30px; border: 2px solid #ccc; border-radius: 4px; background: ${rgbaToHex(this._cfg.theme.light_on_bg)}; cursor: pointer;" title="Click to open color picker"></div>
                    <input type="text" class="color-text" value="${rgbaToHex(this._cfg.theme.light_on_bg)}" placeholder="#000000" style="flex: 1; padding: 6px; border: 1px solid #ccc; border-radius: 4px; font-family: monospace; font-size: 12px;">
                    <button class="save-color-btn" data-theme="light_on_bg" style="padding: 6px 12px; background: var(--primary-color); color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Save</button>
                  </div>
                </div>
                <div class="clickable-element" data-theme="light_off_bg" data-label="Light Off Background">
                  <div class="element-preview" style="background: ${this._cfg.theme.light_off_bg}"></div>
                  <div class="element-label">Light Off Background</div>
                </div>
                <div class="color-picker-inline" id="color-picker-light_off_bg">
                  <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
                    <div class="color-preview" style="width: 30px; height: 30px; border: 2px solid #ccc; border-radius: 4px; background: ${rgbaToHex(this._cfg.theme.light_off_bg)}; cursor: pointer;" title="Click to open color picker"></div>
                    <input type="text" class="color-text" value="${rgbaToHex(this._cfg.theme.light_off_bg)}" placeholder="#000000" style="flex: 1; padding: 6px; border: 1px solid #ccc; border-radius: 4px; font-family: monospace; font-size: 12px;">
                    <button class="save-color-btn" data-theme="light_off_bg" style="padding: 6px 12px; background: var(--primary-color); color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Save</button>
                  </div>
                </div>
                <div class="clickable-element" data-theme="light_icon_on" data-label="Light Button Icon (On)">
                  <div class="element-preview" style="background: ${this._cfg.theme.light_icon_on}; color: ${this._cfg.theme.light_icon_on}">💡</div>
                  <div class="element-label">Light Button Icon (On)</div>
                </div>
                <div class="color-picker-inline" id="color-picker-light_icon_on">
                  <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
                    <div class="color-preview" style="width: 30px; height: 30px; border: 2px solid #ccc; border-radius: 4px; background: ${rgbaToHex(this._cfg.theme.light_icon_on)}; cursor: pointer;" title="Click to open color picker"></div>
                    <input type="text" class="color-text" value="${rgbaToHex(this._cfg.theme.light_icon_on)}" placeholder="#000000" style="flex: 1; padding: 6px; border: 1px solid #ccc; border-radius: 4px; font-family: monospace; font-size: 12px;">
                    <button class="save-color-btn" data-theme="light_icon_on" style="padding: 6px 12px; background: var(--primary-color); color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Save</button>
                  </div>
                </div>
                <div class="clickable-element" data-theme="light_icon_off" data-label="Light Button Icon (Off)">
                  <div class="element-preview" style="background: ${this._cfg.theme.light_icon_off}; color: ${this._cfg.theme.light_icon_off}">💡</div>
                  <div class="element-label">Light Button Icon (Off)</div>
                </div>
                <div class="color-picker-inline" id="color-picker-light_icon_off">
                  <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
                    <div class="color-preview" style="width: 30px; height: 30px; border: 2px solid #ccc; border-radius: 4px; background: ${rgbaToHex(this._cfg.theme.light_icon_off)}; cursor: pointer;" title="Click to open color picker"></div>
                    <input type="text" class="color-text" value="${rgbaToHex(this._cfg.theme.light_icon_off)}" placeholder="#000000" style="flex: 1; padding: 6px; border: 1px solid #ccc; border-radius: 4px; font-family: monospace; font-size: 12px;">
                    <button class="save-color-btn" data-theme="light_icon_off" style="padding: 6px 12px; background: var(--primary-color); color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Save</button>
                  </div>
                </div>
              </div>
              
              <div class="control-group">
                <div class="group-title">Status Area</div>
                <div class="clickable-element" data-theme="status_icon" data-label="Status Icon Color">
                  <div class="element-preview" style="background: ${this._cfg.theme.status_icon === 'auto' ? 'var(--primary-color)' : this._cfg.theme.status_icon}">🖨</div>
                  <div class="element-label">Status Icon Color</div>
                </div>
                <div class="color-picker-inline" id="color-picker-status_icon">
                  <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
                    <div class="color-preview" style="width: 30px; height: 30px; border: 2px solid #ccc; border-radius: 4px; background: ${this._cfg.theme.status_icon === 'auto' ? '#000000' : rgbaToHex(this._cfg.theme.status_icon)}; cursor: pointer;" title="Click to open color picker"></div>
                    <input type="text" class="color-text" value="${this._cfg.theme.status_icon === 'auto' ? 'auto' : rgbaToHex(this._cfg.theme.status_icon)}" placeholder="#000000" style="flex: 1; padding: 6px; border: 1px solid #ccc; border-radius: 4px; font-family: monospace; font-size: 12px;">
                    <button class="save-color-btn" data-theme="status_icon" style="padding: 6px 12px; background: var(--primary-color); color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Save</button>
                  </div>
                </div>
                <div class="clickable-element" data-theme="progress_ring" data-label="Progress Ring Color">
                  <div class="element-preview" style="background: ${this._cfg.theme.progress_ring === 'auto' ? 'var(--primary-color)' : this._cfg.theme.progress_ring}">⭕</div>
                  <div class="element-label">Progress Ring Color</div>
                </div>
                <div class="color-picker-inline" id="color-picker-progress_ring">
                  <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
                    <div class="color-preview" style="width: 30px; height: 30px; border: 2px solid #ccc; border-radius: 4px; background: ${this._cfg.theme.progress_ring === 'auto' ? '#000000' : rgbaToHex(this._cfg.theme.progress_ring)}; cursor: pointer;" title="Click to open color picker"></div>
                    <input type="text" class="color-text" value="${this._cfg.theme.progress_ring === 'auto' ? 'auto' : rgbaToHex(this._cfg.theme.progress_ring)}" placeholder="#000000" style="flex: 1; padding: 6px; border: 1px solid #ccc; border-radius: 4px; font-family: monospace; font-size: 12px;">
                    <button class="save-color-btn" data-theme="progress_ring" style="padding: 6px 12px; background: var(--primary-color); color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Save</button>
                  </div>
                </div>
                <div class="clickable-element" data-theme="status_bg" data-label="Status Background">
                  <div class="element-preview" style="background: ${this._cfg.theme.status_bg}">🎯</div>
                  <div class="element-label">Status Background</div>
                </div>
                <div class="color-picker-inline" id="color-picker-status_bg">
                  <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
                    <div class="color-preview" style="width: 30px; height: 30px; border: 2px solid #ccc; border-radius: 4px; background: ${this._cfg.theme.status_bg === 'auto' ? 'var(--card-background-color)' : rgbaToHex(this._cfg.theme.status_bg)}; cursor: pointer;" title="Click to open color picker"></div>
                    <input type="text" class="color-text" value="${this._cfg.theme.status_bg === 'auto' ? 'auto' : rgbaToHex(this._cfg.theme.status_bg)}" placeholder="auto or #000000" style="flex: 1; padding: 6px; border: 1px solid #ccc; border-radius: 4px; font-family: monospace; font-size: 12px;">
                    <button class="save-color-btn" data-theme="status_bg" style="padding: 6px 12px; background: var(--primary-color); color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Save</button>
                  </div>
                </div>
              </div>
              
              <div class="control-group">
                <div class="group-title">Telemetry</div>
                <div class="clickable-element" data-theme="telemetry_icon" data-label="Telemetry Icon Color">
                  <div class="element-preview" style="background: ${this._cfg.theme.telemetry_icon}">🌡</div>
                  <div class="element-label">Telemetry Icon Color</div>
                </div>
                <div class="color-picker-inline" id="color-picker-telemetry_icon">
                  <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
                    <div class="color-preview" style="width: 30px; height: 30px; border: 2px solid #ccc; border-radius: 4px; background: ${this._cfg.theme.telemetry_icon === 'auto' ? 'var(--secondary-text-color)' : rgbaToHex(this._cfg.theme.telemetry_icon)}; cursor: pointer;" title="Click to open color picker"></div>
                    <input type="text" class="color-text" value="${this._cfg.theme.telemetry_icon === 'auto' ? 'auto' : rgbaToHex(this._cfg.theme.telemetry_icon)}" placeholder="auto or #000000" style="flex: 1; padding: 6px; border: 1px solid #ccc; border-radius: 4px; font-family: monospace; font-size: 12px;">
                    <button class="save-color-btn" data-theme="telemetry_icon" style="padding: 6px 12px; background: var(--primary-color); color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Save</button>
                  </div>
                </div>
                <div class="clickable-element" data-theme="telemetry_text" data-label="Telemetry Text Color">
                  <div class="element-preview" style="background: ${this._cfg.theme.telemetry_text}">📝</div>
                  <div class="element-label">Telemetry Text Color</div>
                </div>
                <div class="color-picker-inline" id="color-picker-telemetry_text">
                  <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
                    <div class="color-preview" style="width: 30px; height: 30px; border: 2px solid #ccc; border-radius: 4px; background: ${this._cfg.theme.telemetry_text === 'auto' ? 'var(--primary-text-color)' : rgbaToHex(this._cfg.theme.telemetry_text)}; cursor: pointer;" title="Click to open color picker"></div>
                    <input type="text" class="color-text" value="${this._cfg.theme.telemetry_text === 'auto' ? 'auto' : rgbaToHex(this._cfg.theme.telemetry_text)}" placeholder="auto or #000000" style="flex: 1; padding: 6px; border: 1px solid #ccc; border-radius: 4px; font-family: monospace; font-size: 12px;">
                    <button class="save-color-btn" data-theme="telemetry_text" style="padding: 6px 12px; background: var(--primary-color); color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Save</button>
                  </div>
                </div>
              </div>
              
              <button class="reset-btn" id="reset-theme">Reset to Defaults</button>
            </div>
          </div>
        </div>
      </div>
    `;
    
    this._setupTabs();
    this._setupEntitiesForm();
    this._setupThemeEditor();
    
    } catch (error) {
      console.error('Error rendering K-Printer Card Editor:', error);
      this._root.innerHTML = `
        <div style="padding: 16px; color: var(--error-color);">
          <h3>Editor Error</h3>
          <p>There was an error loading the visual editor. You can still edit your configuration using YAML.</p>
          <p>Error: ${error.message}</p>
        </div>
      `;
    }
  }
  
  _setupTabs() {
    const tabs = this._root.querySelectorAll('.tab');
    const contents = this._root.querySelectorAll('.tab-content');
    
    tabs.forEach(tab => {
      tab.addEventListener('click', () => {
        tabs.forEach(t => t.classList.remove('active'));
        contents.forEach(c => c.classList.remove('active'));
        
        tab.classList.add('active');
        const tabId = tab.dataset.tab + '-tab';
        this._root.getElementById(tabId).classList.add('active');
      });
    });
  }
  
  _setupEntitiesForm() {
    this._entitiesForm = this._root.getElementById('entities-form');
    this._entitiesForm.hass = this._hass;
    
    // Helper text mapping for form fields
    const helperText = {
      "name": "Display name for the printer card",
      "camera": "Camera entity for live video feed",
      "status": "Sensor showing current print status",
      "progress": "Sensor showing print progress (0-100%)",
      "time_left": "Sensor showing remaining print time (seconds)",
      "nozzle": "Sensor showing nozzle temperature",
      "bed": "Sensor showing bed temperature",
      "box": "Sensor showing enclosure temperature (optional)",
      "layer": "Sensor showing current print layer",
      "total_layers": "Sensor showing total print layers",
      "light": "Switch entity for printer light control",
      "pause_btn": "Button entity to pause printing",
      "resume_btn": "Button entity to resume printing",
      "stop_btn": "Button entity to stop printing"
    };
    
    this._entitiesForm.schema = [
      { name: "name",         selector: { text: {} } },
      { name: "camera",       selector: { entity: { domain: "camera" } } },
      { name: "status",       selector: { entity: { domain: "sensor" } } },
      { name: "progress",     selector: { entity: { domain: "sensor" } } },
      { name: "time_left",    selector: { entity: { domain: "sensor" } } },
      { name: "nozzle",       selector: { entity: { domain: "sensor" } } },
      { name: "bed",          selector: { entity: { domain: "sensor" } } },
      { name: "box",          selector: { entity: { domain: "sensor" } } },
      { name: "layer",        selector: { entity: { domain: "sensor" } } },
      { name: "total_layers", selector: { entity: { domain: "sensor" } } },
      { name: "light",        selector: { entity: { domain: "switch" } } },
      { name: "pause_btn",    selector: { entity: { domain: "button" } } },
      { name: "resume_btn",   selector: { entity: { domain: "button" } } },
      { name: "stop_btn",     selector: { entity: { domain: "button" } } },
    ];
    
    // Label text mapping for form fields
    const labelText = {
      "name": "Printer Name",
      "camera": "Camera",
      "status": "Print Status Sensor",
      "progress": "Print Progress Sensor (%)",
      "time_left": "Time Left Sensor",
      "nozzle": "Nozzle Temperature Sensor",
      "bed": "Bed Temperature Sensor",
      "box": "Enclosure Temperature Sensor",
      "layer": "Current Layer Sensor",
      "total_layers": "Total Layers Sensor",
      "light": "Light Switch",
      "pause_btn": "Pause Button",
      "resume_btn": "Resume Button",
      "stop_btn": "Stop Button"
    };
    
    // Add label computation using computeLabel if supported
    if (this._entitiesForm.computeLabel) {
      const originalComputeLabel = this._entitiesForm.computeLabel.bind(this._entitiesForm);
      this._entitiesForm.computeLabel = (schema) => {
        return labelText[schema.name] || originalComputeLabel(schema);
      };
    }
    
    // Add helper text using computeHelper if supported
    if (this._entitiesForm.computeHelper) {
      const originalComputeHelper = this._entitiesForm.computeHelper.bind(this._entitiesForm);
      this._entitiesForm.computeHelper = (schema) => {
        return helperText[schema.name] || originalComputeHelper(schema);
      };
    }
    
    this._entitiesForm.data = this._cfg;
    
    this._entitiesForm.addEventListener("value-changed", (ev) => {
      const val = ev.detail?.value || {};
      this._cfg = { ...this._cfg, ...val };
      this._dispatchConfigChange();
    });
  }
  
  _setupThemeEditor() {
    // Setup clickable elements to toggle inline color pickers
    const clickableElements = this._root.querySelectorAll('.clickable-element');
    clickableElements.forEach(element => {
      element.addEventListener('click', () => {
        const themeKey = element.dataset.theme;
        this._toggleColorPicker(themeKey);
      });
    });
    
    // Setup color picker interactions
    this._setupColorPickerInteractions();
    
    // Setup reset button
    this._root.getElementById('reset-theme').addEventListener('click', () => {
      const defaultConfig = KPrinterCard.getStubConfig();
      this._cfg.theme = { ...defaultConfig.theme };
      
      // Clear saved theme from storage
      const cardId = generateCardId(this._cfg);
      try {
        const themes = JSON.parse(localStorage.getItem(THEME_STORAGE_KEY) || "{}");
        delete themes[cardId];
        localStorage.setItem(THEME_STORAGE_KEY, JSON.stringify(themes));
      } catch (e) {
        console.warn("Failed to clear theme from localStorage:", e);
      }
      
      this._updateThemeControls();
      this._dispatchConfigChange();
    });
  }
  
  _toggleColorPicker(themeKey) {
    const picker = this._root.getElementById(`color-picker-${themeKey}`);
    if (!picker) return;
    
    // If this picker is already active, close it
    if (picker.classList.contains('active')) {
      picker.classList.remove('active');
      return;
    }
    
    // Hide all other color pickers
    const allPickers = this._root.querySelectorAll('.color-picker-inline');
    allPickers.forEach(p => {
      p.classList.remove('active');
    });
    
    // Show the clicked color picker
    picker.classList.add('active');
  }
  
  _setupColorPickerInteractions() {
    // Setup color preview clicks to open native color picker
    const colorPreviews = this._root.querySelectorAll('.color-preview');
    colorPreviews.forEach(preview => {
      preview.addEventListener('click', () => {
        const picker = preview.closest('.color-picker-inline');
        if (picker) {
          const textInput = picker.querySelector('.color-text');
          if (textInput) {
            // Create a visible color input that stays in the editor
            const input = document.createElement('input');
            input.type = 'color';
            input.value = textInput.value === 'auto' ? '#000000' : textInput.value;
            
            // Style the input to be visible and positioned within the picker
            input.style.position = 'absolute';
            input.style.left = '0';
            input.style.top = '0';
            input.style.width = '100%';
            input.style.height = '100%';
            input.style.opacity = '0';
            input.style.cursor = 'pointer';
            input.style.zIndex = '10';
            
            // Add the input to the picker container
            picker.style.position = 'relative';
            picker.appendChild(input);
            
            // Focus and click the input
            input.focus();
            input.click();
            
            // Handle color change
            input.addEventListener('change', () => {
              const newColor = input.value;
              textInput.value = newColor;
              preview.style.background = newColor;
              
              // Remove the input after color selection
              if (picker.contains(input)) {
                picker.removeChild(input);
              }
            });
            
            // Handle escape key
            const handleKeyDown = (e) => {
              if (e.key === 'Escape') {
                if (picker.contains(input)) {
                  picker.removeChild(input);
                }
                document.removeEventListener('keydown', handleKeyDown);
              }
            };
            document.addEventListener('keydown', handleKeyDown);
            
            // Handle clicks outside the picker
            const handleClickOutside = (e) => {
              if (!picker.contains(e.target)) {
                if (picker.contains(input)) {
                  picker.removeChild(input);
                }
                document.removeEventListener('click', handleClickOutside);
              }
            };
            
            // Add click outside handler after a delay
            setTimeout(() => {
              document.addEventListener('click', handleClickOutside);
            }, 100);
          }
        }
      });
    });
    
    // Setup text input changes
    const colorTexts = this._root.querySelectorAll('.color-text');
    colorTexts.forEach(textInput => {
      textInput.addEventListener('input', () => {
        const value = textInput.value;
        if (/^#[0-9A-Fa-f]{6}$/.test(value)) {
          const picker = textInput.closest('.color-picker-inline');
          if (picker) {
            const preview = picker.querySelector('.color-preview');
            if (preview) {
              preview.style.background = value;
            }
          }
        }
      });
    });
    
    // Setup save buttons
    const saveButtons = this._root.querySelectorAll('.save-color-btn');
    saveButtons.forEach(button => {
      button.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        
        const themeKey = button.dataset.theme;
        const picker = button.closest('.color-picker-inline');
        if (picker) {
          const textInput = picker.querySelector('.color-text');
          if (textInput) {
            let newValue;
            const inputValue = textInput.value;
            
            // Handle special cases for auto-supported fields
            if ((themeKey === 'status_icon' || themeKey === 'progress_ring' || 
                 themeKey === 'status_bg' || themeKey === 'telemetry_icon' || 
                 themeKey === 'telemetry_text') && inputValue === 'auto') {
              newValue = 'auto';
            } else if (/^#[0-9A-Fa-f]{6}$/.test(inputValue)) {
              newValue = hexToRgba(inputValue, 0.9);
            } else {
              // Keep existing value if input is invalid
              newValue = this._cfg.theme[themeKey];
            }
            
            
            this._cfg.theme = { ...this._cfg.theme, [themeKey]: newValue };
            
            // Save to storage
            const cardId = generateCardId(this._cfg);
            saveThemeToStorage(cardId, this._cfg.theme);
            
            this._updateThemeControls();
            
            // Hide the color picker first
            picker.classList.remove('active');
            
            // Dispatch config change after a small delay to prevent tab switching
            setTimeout(() => {
              this._dispatchConfigChange();
            }, 100);
          }
        }
      });
    });
  }
  
  
  
  _updateThemeControls() {
    // Update all preview elements with new colors
    const clickableElements = this._root.querySelectorAll('.clickable-element');
    clickableElements.forEach(element => {
      const themeKey = element.dataset.theme;
      const preview = element.querySelector('.element-preview');
      const currentValue = this._cfg.theme[themeKey] || '';
      
      if (themeKey === 'status_icon' || themeKey === 'progress_ring') {
        preview.style.background = currentValue === 'auto' ? 'var(--primary-color)' : currentValue;
      } else {
        preview.style.background = currentValue;
      }
      
      if (['pause_icon', 'resume_icon', 'stop_icon', 'light_icon_on', 'light_icon_off'].includes(themeKey)) {
        preview.style.color = currentValue;
      }
    });
    
    // Update color picker previews and text inputs
    const colorPickers = this._root.querySelectorAll('.color-picker-inline');
    colorPickers.forEach(picker => {
      const themeKey = picker.id.replace('color-picker-', '');
      const themeValue = this._cfg.theme[themeKey];
      const preview = picker.querySelector('.color-preview');
      const textInput = picker.querySelector('.color-text');
      
      if (preview && textInput) {
        if (themeKey === 'status_icon' || themeKey === 'progress_ring' || 
            themeKey === 'status_bg' || themeKey === 'telemetry_icon' || 
            themeKey === 'telemetry_text') {
          if (themeValue === 'auto') {
            // Show appropriate theme color for auto values
            if (themeKey === 'status_bg') {
              preview.style.background = 'var(--card-background-color)';
            } else if (themeKey === 'telemetry_icon') {
              preview.style.background = 'var(--secondary-text-color)';
            } else if (themeKey === 'telemetry_text') {
              preview.style.background = 'var(--primary-text-color)';
            } else {
              preview.style.background = '#000000';
            }
            textInput.value = 'auto';
          } else {
            preview.style.background = rgbaToHex(themeValue);
            textInput.value = rgbaToHex(themeValue);
          }
        } else {
          preview.style.background = rgbaToHex(themeValue);
          textInput.value = rgbaToHex(themeValue);
        }
      }
    });
  }
  
  _dispatchConfigChange() {
    clearTimeout(this._t);
    this._t = setTimeout(() => {
      // Preserve current tab state
      const activeTab = this._root.querySelector('.tab.active');
      const activeTabId = activeTab ? activeTab.dataset.tab : 'entities';
      
      this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: this._cfg } }));
      
      // Restore tab state after a brief delay
      setTimeout(() => {
        this._restoreTabState(activeTabId);
      }, 50);
    }, 120);
  }
  
  _restoreTabState(activeTabId) {
    // Switch to the preserved tab
    const tabs = this._root.querySelectorAll('.tab');
    const tabContents = this._root.querySelectorAll('.tab-content');
    
    tabs.forEach(tab => {
      tab.classList.remove('active');
      if (tab.dataset.tab === activeTabId) {
        tab.classList.add('active');
      }
    });
    
    tabContents.forEach(content => {
      content.classList.remove('active');
      if (content.id === `${activeTabId}-tab`) {
        content.classList.add('active');
      }
    });
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
