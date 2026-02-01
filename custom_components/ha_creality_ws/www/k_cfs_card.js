
const CARD_TAG = "k-cfs-card";
const EDITOR_TAG = "k-cfs-card-editor";

const mdi = (name) => `mdi:${name}`;

// ===== CREALITY STANDARD COLORS & PRESETS SYSTEM =====
// Official Creality filament color palette
const CREALITY_STANDARD_COLORS = {
  "Red": "#e53935",
  "Green": "#06c84f",
  "Blue": "#1e88e5",
  "Yellow": "#fdd835",
  "Orange": "#fb8c00",
  "Purple": "#8e24aa",
  "Pink": "#ec407a",
  "White": "#ffffff",
  "Black": "#000000",
  "Gray": "#9e9e9e",
  "Cyan": "#00acc1",
  "Lime": "#9ccc65",
};

// Storage keys for presets
const PRESETS_STORAGE_KEY = "k-cfs-color-presets";

/**
 * Color Presets Manager - Handles saving, loading, and managing custom color presets
 */
class ColorPresetsManager {
  constructor() {
    this.presets = this._loadPresets();
  }

  _loadPresets() {
    try {
      const stored = localStorage.getItem(PRESETS_STORAGE_KEY);
      return stored ? JSON.parse(stored) : {};
    } catch (e) {
      console.warn("Failed to load color presets from localStorage:", e);
      return {};
    }
  }

  _savePresets() {
    try {
      localStorage.setItem(PRESETS_STORAGE_KEY, JSON.stringify(this.presets));
    } catch (e) {
      console.warn("Failed to save color presets to localStorage:", e);
    }
  }

  savePreset(name, color) {
    if (!name || !color) return false;
    const sanitizedColor = this._sanitizeColor(color);
    this.presets[name] = sanitizedColor;
    this._savePresets();
    return true;
  }

  deletePreset(name) {
    if (this.presets[name]) {
      delete this.presets[name];
      this._savePresets();
      return true;
    }
    return false;
  }

  renamePreset(oldName, newName) {
    if (!this.presets[oldName] || !newName) return false;
    this.presets[newName] = this.presets[oldName];
    delete this.presets[oldName];
    this._savePresets();
    return true;
  }

  getPresets() {
    return { ...this.presets };
  }

  getStandardColors() {
    return { ...CREALITY_STANDARD_COLORS };
  }

  getAllColors() {
    return {
      ...this.getStandardColors(),
      ...this.getPresets()
    };
  }

  _sanitizeColor(value) {
    const raw = String(value || "").trim();
    if (!raw || ["unknown", "unavailable", "—"].includes(raw.toLowerCase())) {
      return "#cccccc";
    }
    const hex = raw.startsWith("#") ? raw.slice(1) : raw;
    if (hex.length === 6 && /^[0-9a-fA-F]+$/.test(hex)) {
      return `#${hex.toLowerCase()}`;
    }
    if (hex.length === 3 && /^[0-9a-fA-F]+$/.test(hex)) {
      return `#${hex.toLowerCase()}`;
    }
    if (hex.length === 7 && hex.startsWith("0") && /^[0-9a-fA-F]+$/.test(hex)) {
      return `#${hex.slice(1).toLowerCase()}`;
    }
    return "#cccccc";
  }
}

class KCFSCard extends HTMLElement {
  constructor() {
    super();
    this._selectedCFS = 0; // Track selected CFS tab in normal mode
    this._editingSlot = null; // Track which slot is being edited
    // Calculate base path for resources
    this._basePath = this._getBasePath();
    // Initialize presets manager
    this._presetsManager = new ColorPresetsManager();
  }

  _getBasePath() {
    // Try to find the script's path from document scripts
    const scripts = document.getElementsByTagName('script');
    for (let script of scripts) {
      if (script.src && script.src.includes('k_cfs_card.js')) {
        const fullPath = script.src.substring(0, script.src.lastIndexOf('/'));
        // Remove query parameters if present
        return fullPath.split('?')[0];
      }
    }
    // Fallback to integration path
    return '/ha_creality_ws';
  }

  static _sanitizeColor(value) {
    const raw = String(value || "").trim();
    if (!raw || ["unknown", "unavailable", "—"].includes(raw.toLowerCase())) {
      return "#cccccc";
    }
    const hex = raw.startsWith("#") ? raw.slice(1) : raw;
    if (hex.length === 6 && /^[0-9a-fA-F]+$/.test(hex)) {
      return `#${hex.toLowerCase()}`;
    }
    if (hex.length === 3 && /^[0-9a-fA-F]+$/.test(hex)) {
      return `#${hex.toLowerCase()}`;
    }
    if (hex.length === 7 && hex.startsWith("0") && /^[0-9a-fA-F]+$/.test(hex)) {
      return `#${hex.slice(1).toLowerCase()}`;
    }
    return "#cccccc";
  }

  static _parsePercent(percentObj) {
    if (!percentObj) return null;
    const state = percentObj.state;
    if (state === undefined || state === null) return null;
    const s = String(state);
    if (s === "unknown" || s === "unavailable") return null;
    const n = Number(s);
    if (Number.isNaN(n) || !Number.isFinite(n)) return null;
    return Math.max(0, Math.min(100, n));
  }

  static _getHumidityColor(humidityStr) {
    if (!humidityStr || humidityStr === "—") return '#64b5f6'; // default blue
    
    const match = String(humidityStr).match(/(\d+\.?\d*)/);
    if (!match) return '#64b5f6';
    
    const value = parseFloat(match[1]);
    if (value < 40) return '#4caf50';   // Green (0-39%) - Ideal
    if (value < 60) return '#ff9800';   // Orange (40-59%) - Attention
    return '#f44336';                    // Red (60-100%) - Critical
  }

  static getStubConfig() {
    const cfg = {
      name: "CFS",
      view_mode: "full",  // Options: "full", "compact", "box"
      compact_view: false,  // Kept for backward compatibility
      show_type_in_mini: false,
      debug_mode: false,
      external_filament: "",
      external_color: "",
      external_percent: "",
    };

    for (let box = 0; box < 4; box += 1) {
      cfg[`box${box}_temp`] = "";
      cfg[`box${box}_humidity`] = "";
      for (let slot = 0; slot < 4; slot += 1) {
        cfg[`box${box}_slot${slot}_filament`] = "";
        cfg[`box${box}_slot${slot}_color`] = "";
        cfg[`box${box}_slot${slot}_percent`] = "";
      }
    }

    return cfg;
  }

  static getConfigElement() {
    return document.createElement(EDITOR_TAG);
  }

  setConfig(config) {
    // Create a mutable copy of config for migration
    const migratedConfig = { ...config };
    
    // Migrate old compact_view to new view_mode for backward compatibility
    if (migratedConfig.compact_view !== undefined && migratedConfig.view_mode === undefined) {
      migratedConfig.view_mode = migratedConfig.compact_view ? "compact" : "full";
    }
    
    this._cfg = { ...KCFSCard.getStubConfig(), ...migratedConfig };
    if (!this._root) {
      this._root = this.attachShadow({ mode: "open" });
    }
    // Initialize cache for data comparison
    this._dataCache = null;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._updateIfChanged();
  }

  _updateIfChanged() {
    if (!this._root || !this._hass) return;

    // Extract current data
    const currentData = this._extractData();
    
    // Compare with cached data
    if (this._dataCache && this._deepEqual(currentData, this._dataCache)) {
      // Data hasn't changed, skip re-render
      if (this._cfg.debug_mode) {
        console.log('[KCFSCard] Update skipped - data unchanged');
      }
      return;
    }

    // Data changed, update cache and re-render
    if (this._cfg.debug_mode) {
      console.log('[KCFSCard] Data changed, re-rendering', {
        old: this._dataCache,
        new: currentData
      });
    }
    this._dataCache = currentData;
    this._update();
  }

  _deepEqual(obj1, obj2) {
    // Handle null/undefined
    if (obj1 === obj2) return true;
    if (obj1 == null || obj2 == null) return false;
    if (typeof obj1 !== 'object' || typeof obj2 !== 'object') return obj1 === obj2;

    // Handle arrays
    if (Array.isArray(obj1) && Array.isArray(obj2)) {
      if (obj1.length !== obj2.length) return false;
      for (let i = 0; i < obj1.length; i++) {
        if (!this._deepEqual(obj1[i], obj2[i])) return false;
      }
      return true;
    }

    // Handle objects
    const keys1 = Object.keys(obj1);
    const keys2 = Object.keys(obj2);
    if (keys1.length !== keys2.length) return false;

    for (const key of keys1) {
      if (!keys2.includes(key)) return false;
      if (!this._deepEqual(obj1[key], obj2[key])) return false;
    }

    return true;
  }

  _extractData() {
    // Extract all relevant data for comparison
    const states = this._hass.states || {};
    const data = { boxes: [], external: null };

    // Extract box data
    for (let boxId = 0; boxId < 4; boxId++) {
      const tempEid = this._cfg[`box${boxId}_temp`];
      const humidityEid = this._cfg[`box${boxId}_humidity`];
      
      const tempObj = tempEid ? states[tempEid] : undefined;
      const humidityObj = humidityEid ? states[humidityEid] : undefined;

      const boxData = {
        id: boxId,
        temp: tempObj?.state,
        humidity: humidityObj?.state,
        slots: []
      };

      // Extract slot data
      for (let slotId = 0; slotId < 4; slotId++) {
        const filamentEid = this._cfg[`box${boxId}_slot${slotId}_filament`];
        const colorEid = this._cfg[`box${boxId}_slot${slotId}_color`];
        const percentEid = this._cfg[`box${boxId}_slot${slotId}_percent`];

        if (!filamentEid && !colorEid && !percentEid) {
          boxData.slots.push(null);
          continue;
        }

        const filamentObj = filamentEid ? states[filamentEid] : undefined;
        const colorObj = colorEid ? states[colorEid] : undefined;
        const percentObj = percentEid ? states[percentEid] : undefined;

        boxData.slots.push({
          name: filamentObj?.state,
          type: filamentObj?.attributes?.type,
          selected: filamentObj?.attributes?.selected,
          color: colorObj?.state || filamentObj?.attributes?.color_hex,
          percent: percentObj?.state,
          vendor: filamentObj?.attributes?.vendor,
          min_temp: filamentObj?.attributes?.min_temp,
          max_temp: filamentObj?.attributes?.max_temp,
          pressure: filamentObj?.attributes?.pressure
        });
      }

      data.boxes.push(boxData);
    }

    // Extract external data
    const externalFilamentEid = this._cfg.external_filament;
    const externalColorEid = this._cfg.external_color;
    const externalPercentEid = this._cfg.external_percent;

    if (externalFilamentEid || externalColorEid || externalPercentEid) {
      const filamentObj = externalFilamentEid ? states[externalFilamentEid] : undefined;
      const colorObj = externalColorEid ? states[externalColorEid] : undefined;
      const percentObj = externalPercentEid ? states[externalPercentEid] : undefined;

      data.external = {
        name: filamentObj?.state,
        type: filamentObj?.attributes?.type,
        color: colorObj?.state || filamentObj?.attributes?.color_hex,
        percent: percentObj?.state,
        vendor: filamentObj?.attributes?.vendor,
        min_temp: filamentObj?.attributes?.min_temp,
        max_temp: filamentObj?.attributes?.max_temp,
        pressure: filamentObj?.attributes?.pressure
      };
    }

    return data;
  }

  _render() {
    if (!this._root) return;

    // Migrate old config to new view_mode
    if (this._cfg.compact_view !== undefined && this._cfg.view_mode === undefined) {
      this._cfg.view_mode = this._cfg.compact_view ? "compact" : "full";
    }
    const viewMode = this._cfg.view_mode || "full";
    const isCompact = viewMode === "compact";  // For backward compatibility

    const style = `
      ha-card {
        padding: 20px;
        background: rgba(var(--rgb-card-background-color), 0.95);
        backdrop-filter: blur(10px);
        -webkit-backdrop-filter: blur(10px);
        border: 1px solid rgba(var(--rgb-primary-text-color), 0.08);
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
      }

      /* === FULL MODE === */
      .full-mode {}

      .header {
        display: flex;
        justify-content: space-between;
        align-items: flex-end;
        margin-bottom: 24px;
      }
      .title-section {
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .title {
        font-size: 20px;
        font-weight: 700;
        letter-spacing: -0.5px;
      }
      .subtitle {
        font-size: 12px;
        color: var(--secondary-text-color);
        font-weight: 500;
      }
      .env-info {
        font-size: 11px;
        background: rgba(var(--rgb-primary-text-color), 0.08);
        padding: 6px 10px;
        border-radius: 12px;
        color: var(--secondary-text-color);
      }

      .unit-selector {
        display: flex;
        gap: 8px;
        margin-bottom: 20px;
        background: rgba(var(--rgb-primary-text-color), 0.05);
        padding: 4px;
        border-radius: 14px;
        width: fit-content;
      }
      .unit-btn {
        padding: 6px 16px;
        border-radius: 10px;
        font-size: 12px;
        font-weight: 600;
        cursor: pointer;
        transition: all 0.3s ease;
        border: none;
        color: var(--secondary-text-color);
        background: transparent;
      }
      .unit-btn.active {
        background: rgba(var(--rgb-primary-text-color), 0.1);
        color: var(--primary-text-color);
        box-shadow: 0 4px 10px rgba(0, 0, 0, 0.15);
      }

      .spool-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 12px;
      }

      .spool-card {
        background: rgba(var(--rgb-primary-text-color), 0.04);
        border-radius: 18px;
        padding: 16px;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        position: relative;
        border: 1px solid transparent;
        transition: all 0.3s ease;
        cursor: pointer;
      }

      .spool-card:hover {
        background: rgba(var(--rgb-primary-text-color), 0.06);
      }

      .spool-card.active {
        background: rgba(var(--rgb-primary-text-color), 0.08);
        border: 1px solid rgba(var(--rgb-primary-color), 0.3);
        box-shadow: 0 0 20px rgba(var(--rgb-primary-color), 0.2);
      }

      .ring-container {
        width: 80px;
        height: 80px;
        border-radius: 50%;
        position: relative;
        display: flex;
        justify-content: center;
        align-items: center;
        margin-bottom: 10px;
      }

      .ring-outer {
        width: 100%;
        height: 100%;
        border-radius: 50%;
        position: absolute;
        background: conic-gradient(
          var(--spool-color) var(--spool-pct),
          rgba(var(--rgb-primary-text-color), 0.08) 0
        );
      }

      .ring-inner {
        width: 66px;
        height: 66px;
        background: var(--card-background-color);
        border-radius: 50%;
        position: relative;
        z-index: 2;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
      }

      .spool-label {
        font-size: 10px;
        color: var(--secondary-text-color);
        text-transform: uppercase;
      }
      .spool-pct {
        font-size: 16px;
        font-weight: 700;
      }

      .material-name {
        font-size: 13px;
        font-weight: 600;
        text-align: center;
      }
      .color-name {
        font-size: 11px;
        color: var(--secondary-text-color);
        text-align: center;
      }

      .status-badge {
        position: absolute;
        top: 8px;
        right: 8px;
        width: 8px;
        height: 8px;
        background: var(--success-color, #4caf50);
        border-radius: 50%;
        box-shadow: 0 0 8px var(--success-color, #4caf50);
        animation: pulse-badge 2s infinite;
      }

      @keyframes pulse-badge {
        0%, 100% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.7; transform: scale(1.1); }
      }

      /* === COMPACT MODE === */
      .compact-mode {
        padding: 14px;
      }

      .compact-mode .header {
        margin-bottom: 12px;
      }

      .cfs-rows {
        display: flex;
        flex-direction: column;
        gap: 10px;
      }

      .cfs-row {
        display: flex;
        align-items: center;
        gap: 12px;
      }

      .cfs-label {
        width: 48px;
        font-size: 11px;
        color: var(--secondary-text-color);
        font-weight: 600;
      }

      .spools-inline {
        display: flex;
        gap: 10px;
        flex: 1;
      }

      .spool-mini {
        width: 34px;
        height: 34px;
        border-radius: 50%;
        position: relative;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 10px;
        font-weight: 600;
        cursor: pointer;
        transition: transform 0.2s;
      }

      .spool-mini:hover {
        transform: scale(1.05);
      }

      .spool-mini::before {
        content: '';
        position: absolute;
        inset: 0;
        border-radius: 50%;
        background: conic-gradient(
          var(--spool-color) var(--spool-pct),
          rgba(var(--rgb-primary-text-color), 0.08) 0
        );
      }

      .spool-mini::after {
        content: '';
        position: absolute;
        inset: 4px;
        background: var(--card-background-color);
        border-radius: 50%;
        z-index: 1;
      }

      .spool-mini span {
        position: relative;
        z-index: 2;
      }

      .spool-mini.active {
        box-shadow: 0 0 10px var(--spool-color);
      }

      .spool-mini.active::after {
        inset: 3px;
      }

      .spool-mini-wrapper {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 2px;
      }

      .spool-mini-type {
        font-size: 8px;
        color: var(--secondary-text-color);
        text-transform: uppercase;
        font-weight: 600;
        max-width: 40px;
        text-align: center;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      .env-mini {
        width: 56px;
        display: flex;
        flex-direction: column;
        align-items: flex-end;
        font-size: 10px;
        line-height: 1.4;
        gap: 2px;
      }

      .env-mini .temp {
        color: #ffb74d;
        font-weight: 600;
      }

      .env-mini .hum {
        font-weight: 600;
        /* Cor aplicada dinamicamente via inline style */
      }

    
  /* === BOX VIEW MODE === */
  .box-mode {
    padding: 16px;
  }

  .box-view-container {
    position: relative;
    width: 100%;
    max-width: 600px;
    margin: 0 auto;
    padding-top: 60%; /* Aspect ratio for the CFS box */
  }

  .box-image {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    object-fit: contain;
    border-radius: 12px;
  }

  .box-overlays {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
  }

  .spool-overlay {
    position: absolute;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    transition: all 0.3s ease;
  }

  .spool-overlay:hover {
    transform: scale(1.05);
  }

  .spool-overlay .edit-btn-mini {
    position: absolute;
    top: 2px;
    right: 2px;
    width: 20px;
    height: 20px;
    background: rgba(var(--rgb-card-background-color), 0.95);
    border: 1px solid rgba(var(--rgb-primary-text-color), 0.1);
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    opacity: 0;
    transition: opacity 0.2s ease, transform 0.2s ease, background 0.2s ease;
    z-index: 10;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.3);
  }

  .spool-overlay:hover .edit-btn-mini {
    opacity: 1;
  }

  .spool-overlay .edit-btn-mini:hover {
    background: rgba(var(--rgb-primary-color), 0.15);
    transform: scale(1.15);
    border-color: var(--primary-color);
  }

  .spool-overlay .edit-btn-mini.disabled {
    opacity: 0.3 !important;
    cursor: not-allowed;
    background: rgba(var(--rgb-primary-text-color), 0.05);
  }

  .spool-overlay .edit-btn-mini.disabled:hover {
    transform: none;
    border-color: rgba(var(--rgb-primary-text-color), 0.1);
  }

  .spool-overlay .edit-btn-mini ha-icon {
    --mdc-icon-size: 12px;
    color: var(--primary-color);
    pointer-events: none;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .spool-overlay .edit-btn-mini.disabled ha-icon {
    color: var(--secondary-text-color);
  }

  /* Position overlays over each of the 4 spools */
  .spool-overlay:nth-child(1) { left: 15%; top: 10%; width: 15%; height: 40%; }
  .spool-overlay:nth-child(2) { left: 33%; top: 10%; width: 15%; height: 40%; }
  .spool-overlay:nth-child(3) { left: 51%; top: 10%; width: 15%; height: 40%; }
  .spool-overlay:nth-child(4) { left: 69%; top: 10%; width: 15%; height: 40%; }

  .spool-indicator {
    width: 100%;
    height: 100%;
    background: linear-gradient(to bottom, 
      transparent 0%, 
      var(--spool-color, rgba(255,255,255,0.1)) 30%, 
      var(--spool-color, rgba(255,255,255,0.2)) 50%, 
      transparent 100%
    );
    border-radius: 8px;
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 8px 4px;
    backdrop-filter: blur(2px);
  }

  .spool-overlay.active .spool-indicator {
    box-shadow: 0 0 20px var(--spool-color, rgba(255,255,255,0.5));
    animation: pulse-spool 2s infinite;
  }

  @keyframes pulse-spool {
    0%, 100% { opacity: 0.9; }
    50% { opacity: 1; }
  }

  .spool-type-badge {
    font-size: 10px;
    font-weight: 700;
    color: white;
    text-shadow: 0 1px 3px rgba(0,0,0,0.5);
    background: rgba(0,0,0,0.3);
    padding: 2px 6px;
    border-radius: 4px;
    margin-bottom: 4px;
    text-transform: uppercase;
  }

  .spool-percent-badge {
    font-size: 14px;
    font-weight: 700;
    color: white;
    text-shadow: 0 1px 3px rgba(0,0,0,0.5);
  }

  .box-env-info {
    position: absolute;
    bottom: 13%;
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    gap: 12px;
    background: rgba(0, 0, 0, 0.5);
    backdrop-filter: blur(4px);
    padding: 8px 16px;
    border-radius: 12px;
    font-size: 11px;
    color: white;
  }

  .box-env-temp {
    color: #ffb74d;
    font-weight: 600;
  }

  .box-env-hum {
    font-weight: 600;
  }


  /* === EXTERNAL SECTION === */
      .external-section {
        margin-top: 16px;
        padding-top: 14px;
        border-top: 1px solid rgba(var(--rgb-primary-text-color), 0.08);
      }

      .external-normal {
        background: rgba(var(--rgb-primary-text-color), 0.03);
        border-radius: 16px;
        padding: 12px 16px;
        display: flex;
        align-items: center;
        gap: 12px;
      }

      .ext-icon {
        width: 30px;
        height: 30px;
        background: var(--primary-color);
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 10px;
        font-weight: bold;
        color: white;
      }

      .ext-info {
        flex-grow: 1;
      }

      .ext-name {
        font-size: 12px;
        font-weight: 600;
      }

      .ext-bar {
        height: 4px;
        background: rgba(var(--rgb-primary-text-color), 0.1);
        border-radius: 2px;
        margin-top: 6px;
        overflow: hidden;
      }

      .ext-fill {
        height: 100%;
        background: var(--primary-color);
        transition: width 0.3s ease;
      }

      .ext-percent {
        font-size: 12px;
        color: var(--secondary-text-color);
        font-weight: 600;
      }

      .external-compact {
        display: flex;
        align-items: center;
        gap: 10px;
      }

      .ext-dot {
        width: 34px;
        height: 34px;
        border-radius: 50%;
        background: conic-gradient(var(--primary-color) 100%, rgba(var(--rgb-primary-text-color), 0.1) 0);
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 10px;
        font-weight: 600;
        color: white;
      }

      .ext-compact-info {
        flex: 1;
      }

      .ext-compact-info div:first-child {
        font-size: 12px;
        font-weight: 600;
      }

      .ext-compact-info div:last-child {
        font-size: 10px;
        color: var(--secondary-text-color);
      }

      .no-data {
        text-align: center;
        color: var(--secondary-text-color);
        padding: 20px;
      }

      /* === EDIT BUTTON === */
      .edit-btn {
        position: absolute;
        top: 8px;
        left: 8px;
        width: 28px;
        height: 28px;
        background: rgba(var(--rgb-card-background-color), 0.95);
        border: 1px solid rgba(var(--rgb-primary-text-color), 0.1);
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        opacity: 0;
        transition: opacity 0.2s ease, transform 0.2s ease, background 0.2s ease;
        z-index: 10;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
      }

      .spool-card:hover .edit-btn {
        opacity: 1;
      }

      .edit-btn:hover {
        background: rgba(var(--rgb-primary-color), 0.15);
        transform: scale(1.15);
        border-color: var(--primary-color);
      }

      .edit-btn.disabled {
        opacity: 0.3 !important;
        cursor: not-allowed;
        background: rgba(var(--rgb-primary-text-color), 0.05);
      }

      .edit-btn.disabled:hover {
        transform: none;
        border-color: rgba(var(--rgb-primary-text-color), 0.1);
      }

      .edit-btn ha-icon {
        --mdc-icon-size: 16px;
        color: var(--primary-color);
        pointer-events: none;
        display: flex;
        align-items: center;
        justify-content: center;
      }

      .edit-btn.disabled ha-icon {
        color: var(--secondary-text-color);
      }

      /* === EDIT BUTTON FOR MINI SPOOLS & EXTERNAL === */
      .spool-mini-wrapper,
      .spool-mini-container,
      .external-normal,
      .external-compact {
        position: relative;
      }

      .edit-btn-mini {
        position: absolute;
        top: -6px;
        right: -6px;
        width: 20px;
        height: 20px;
        background: rgba(var(--rgb-card-background-color), 0.95);
        border: 1px solid rgba(var(--rgb-primary-text-color), 0.1);
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        opacity: 0;
        transition: opacity 0.2s ease, transform 0.2s ease, background 0.2s ease;
        z-index: 10;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.15);
      }

      .spool-mini-wrapper:hover .edit-btn-mini,
      .spool-mini-container:hover .edit-btn-mini,
      .external-normal:hover .edit-btn-mini,
      .external-compact:hover .edit-btn-mini {
        opacity: 1;
      }

      .edit-btn-mini:hover {
        background: rgba(var(--rgb-primary-color), 0.15);
        transform: scale(1.15);
        border-color: var(--primary-color);
      }

      .edit-btn-mini.disabled {
        opacity: 0.3 !important;
        cursor: not-allowed;
        background: rgba(var(--rgb-primary-text-color), 0.05);
      }

      .edit-btn-mini.disabled:hover {
        transform: none;
        border-color: rgba(var(--rgb-primary-text-color), 0.1);
      }

      .edit-btn-mini ha-icon {
        --mdc-icon-size: 12px;
        color: var(--primary-color);
        pointer-events: none;
        display: flex;
        align-items: center;
        justify-content: center;
      }

      .edit-btn-mini.disabled ha-icon {
        color: var(--secondary-text-color);
      }

      /* Adjust external button position for compact mode */
      .external-compact .edit-btn-mini {
        top: 4px;
        right: 4px;
      }

      /* === EDIT DIALOG === */
      .edit-dialog-overlay {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0, 0, 0, 0.7);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 1000;
        backdrop-filter: blur(4px);
      }

      .edit-dialog {
        background: var(--card-background-color);
        border-radius: 24px;
        padding: 24px;
        max-width: 500px;
        width: 90%;
        max-height: 80vh;
        overflow-y: auto;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
      }

      .dialog-header {
        font-size: 20px;
        font-weight: 700;
        margin-bottom: 20px;
        display: flex;
        justify-content: space-between;
        align-items: center;
      }

      .dialog-close {
        cursor: pointer;
        opacity: 0.6;
        transition: opacity 0.2s;
      }

      .dialog-close:hover {
        opacity: 1;
      }

      .form-field {
        margin-bottom: 16px;
      }

      .form-label {
        display: block;
        font-size: 12px;
        font-weight: 600;
        color: var(--secondary-text-color);
        margin-bottom: 6px;
      }

      .form-input {
        width: 100%;
        padding: 10px;
        border: 1px solid rgba(var(--rgb-primary-text-color), 0.2);
        border-radius: 8px;
        background: rgba(var(--rgb-primary-text-color), 0.05);
        color: var(--primary-text-color);
        font-size: 14px;
        box-sizing: border-box;
      }

      .form-input:focus {
        outline: none;
        border-color: var(--primary-color);
      }

      .form-row {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 12px;
      }

      .dialog-actions {
        display: flex;
        gap: 12px;
        margin-top: 20px;
      }

      .dialog-btn {
        flex: 1;
        padding: 12px;
        border: none;
        border-radius: 12px;
        font-size: 14px;
        font-weight: 600;
        cursor: pointer;
        transition: all 0.3s ease;
      }

      .dialog-btn-cancel {
        background: rgba(var(--rgb-primary-text-color), 0.1);
        color: var(--primary-text-color);
      }

      .dialog-btn-save {
        background: var(--primary-color);
        color: white;
      }

      .dialog-btn:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
      }

      .dialog-btn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
        transform: none;
      }

      .dialog-form {
        display: flex;
        flex-direction: column;
        width: 100%;
      }
    `;

    this._root.innerHTML = `
      <ha-card class="${viewMode}-mode">
        <style>${style}</style>
        <div id="content"></div>
      </ha-card>
    `;
  }

  _update() {
    if (!this._root || !this._hass) return;

    const contentContainer = this._root.getElementById("content");
    if (!contentContainer) return;

    const states = this._hass.states || {};
    const gObj = (eid) => (eid ? states?.[eid] : undefined);
    const fmtState = (st) => {
      if (!st) return "—";
      const v = st.state;
      if (v === undefined || v === null) return "—";
      const s = String(v);
      if (s === "unknown" || s === "unavailable") return "—";
      if (this._hass && typeof this._hass.formatEntityState === "function") {
        try { return this._hass.formatEntityState(st); } catch (_) { }
      }
      const unit = st.attributes?.unit_of_measurement;
      const n = Number(s);
      if (!Number.isNaN(n) && Number.isFinite(n)) {
        const dp = (typeof st.attributes?.display_precision === "number") ? st.attributes.display_precision
          : (typeof st.attributes?.suggested_display_precision === "number") ? st.attributes.suggested_display_precision
            : (unit && /°|c|f/i.test(unit)) ? 1
              : 2;
        const out = n.toFixed(Math.max(0, Math.min(6, dp)));
        return unit ? `${out} ${unit}` : out;
      }
      return unit ? `${s} ${unit}` : s;
    };

    // Collect box data
    const boxes = {};
    for (let boxId = 0; boxId < 4; boxId += 1) {
      const tempEid = this._cfg[`box${boxId}_temp`];
      const humidityEid = this._cfg[`box${boxId}_humidity`];
      const slots = [];

      for (let slotId = 0; slotId < 4; slotId += 1) {
        const filamentEid = this._cfg[`box${boxId}_slot${slotId}_filament`];
        const colorEid = this._cfg[`box${boxId}_slot${slotId}_color`];
        const percentEid = this._cfg[`box${boxId}_slot${slotId}_percent`];
        if (!filamentEid && !colorEid && !percentEid) {
          slots.push(null);
          continue;
        }

        const filamentObj = gObj(filamentEid);
        const colorObj = gObj(colorEid);
        const percentObj = gObj(percentEid);
        const name = filamentObj?.state;
        const type = filamentObj?.attributes?.type;
        const selected = filamentObj?.attributes?.selected;
        const rawColor = colorObj?.state || filamentObj?.attributes?.color_hex;
        const color = KCFSCard._sanitizeColor(rawColor);
        const percent = KCFSCard._parsePercent(percentObj);
        const percentText = fmtState(percentObj);

        slots[slotId] = {
          id: slotId,
          boxId,
          entity_id: filamentEid || colorEid || percentEid,
          name,
          type,
          selected,
          color,
          percent,
          percentText,
        };
      }

      if (tempEid || humidityEid || slots.some((slot) => slot)) {
        const humidityFormatted = fmtState(gObj(humidityEid));
        boxes[boxId] = {
          id: boxId,
          temp: fmtState(gObj(tempEid)),
          humidity: humidityFormatted,
          humidityColor: KCFSCard._getHumidityColor(humidityFormatted),
          slots,
        };
      }
    }

    // Collect external data
    const external = {
      filament: this._cfg.external_filament,
      color: this._cfg.external_color,
      percent: this._cfg.external_percent,
    };
    const hasExternal = external.filament || external.color || external.percent;
    let externalData = null;
    if (hasExternal) {
      const filamentObj = gObj(external.filament);
      const colorObj = gObj(external.color);
      const percentObj = gObj(external.percent);
      const name = filamentObj?.state;
      const type = filamentObj?.attributes?.type;
      const selected = filamentObj?.attributes?.selected;
      const rawColor = colorObj?.state || filamentObj?.attributes?.color_hex;
      const color = KCFSCard._sanitizeColor(rawColor);
      const percent = KCFSCard._parsePercent(percentObj);
      const percentText = fmtState(percentObj);

      externalData = {
        id: 0,
        boxId: -1,
        entity_id: external.filament || external.color || external.percent,
        name,
        type,
        selected,
        color,
        percent,
        percentText,
      };
    }

    const boxValues = Object.values(boxes);
    if (boxValues.length === 0 && !hasExternal) {
      contentContainer.innerHTML = `<div class="no-data">No CFS data available</div>`;
      return;
    }

    // Render based on mode
    const viewMode = this._cfg.view_mode || "full";
    if (viewMode === "compact") {
      contentContainer.innerHTML = this._renderCompactMode(boxValues, externalData);
    } else if (viewMode === "box") {
      contentContainer.innerHTML = this._renderBoxMode(boxValues, externalData);
    } else {
      contentContainer.innerHTML = this._renderNormalMode(boxValues, externalData);
    }

    this._attachEventHandlers();
  }

  _renderNormalMode(boxes, external) {
    // Ensure we have at least one box
    if (boxes.length === 0 && !external) {
      return `<div class="no-data">No CFS data available</div>`;
    }

    // Unit selector (only if we have multiple boxes)
    let unitSelector = '';
    if (boxes.length > 1) {
      unitSelector = `
        <div class="unit-selector">
          ${boxes.map((box, idx) => `
            <button class="unit-btn ${idx === this._selectedCFS ? 'active' : ''}" data-cfs="${idx}">
              CFS ${box.id + 1}
            </button>
          `).join('')}
        </div>
      `;
    }

    // Get the selected box
    const selectedBox = boxes[this._selectedCFS] || boxes[0];
    if (!selectedBox && !external) {
      return `<div class="no-data">No CFS data available</div>`;
    }

    // Header with environment info
    let envInfo = '';
    if (selectedBox) {
      const tempStr = selectedBox.temp !== "—" ? selectedBox.temp : '';
      const humStr = selectedBox.humidity !== "—" ? selectedBox.humidity : '';
      
      if (tempStr || humStr) {
        const tempHtml = tempStr ? `<span class="env-temp">${tempStr}</span>` : '';
        const humHtml = humStr ? `<span class="env-hum" style="color: ${selectedBox.humidityColor}">${humStr}</span>` : '';
        const separator = tempStr && humStr ? ' <span style="color: var(--divider-color)">•</span> ' : '';
        envInfo = `<div class="env-info">${tempHtml}${separator}${humHtml}</div>`;
      }
    }

    const header = `
      <div class="header">
        <div class="title-section">
          <div class="title">${this._cfg.name || 'Creality CFS'}</div>
        </div>
        ${envInfo}
      </div>
    `;

    // Spool grid
    let spoolGrid = '';
    if (selectedBox) {
      spoolGrid = `
        <div class="spool-grid">
          ${selectedBox.slots.map((slot) => this._renderSpoolCard(slot)).join('')}
        </div>
      `;
    }

    // External section
    let externalSection = '';
    if (external) {
      const safeType = external.type && !["unknown", "unavailable", "—", "-"].includes(String(external.type).toLowerCase()) ? external.type : "—";
      const safeName = external.name && !["unknown", "unavailable", "—", "-"].includes(String(external.name).toLowerCase()) ? external.name : "—";
      const hasFilament = safeType !== "—" && safeName !== "—";
      const pct = hasFilament && external.percent !== null ? external.percent : 0;
      const percentTextDisplay = hasFilament ? (external.percentText || '—') : '—';
      const displayName = hasFilament ? `${safeName} ${safeType}` : '—';
      const color = external.color || '#cccccc';
      
      const isPrinterBusy = this._isPrinterBusy();
      const editBtnClass = isPrinterBusy ? 'edit-btn-mini disabled' : 'edit-btn-mini';
      const editIcon = isPrinterBusy ? 'mdi:lock' : 'mdi:pencil';
      const editBtn = `<div class="${editBtnClass}" data-action="edit"><ha-icon icon="${editIcon}"></ha-icon></div>`;
      
      externalSection = `
        <div class="external-section">
          <div class="external-normal" data-eid="${external.entity_id}" data-box="-1" data-slot="-1">
            ${editBtn}
            <div class="ext-icon" style="background: ${color}">EXT</div>
            <div class="ext-info">
              <div class="ext-name">${displayName}</div>
              <div class="ext-bar">
                <div class="ext-fill" style="width: ${pct}%; background: ${color}"></div>
              </div>
            </div>
            <div class="ext-percent">${percentTextDisplay}</div>
          </div>
        </div>
      `;
    }

    return `${unitSelector}${header}${spoolGrid}${externalSection}`;
  }

  _renderCompactMode(boxes, external) {
    if (boxes.length === 0 && !external) {
      return `<div class="no-data">No CFS data available</div>`;
    }

    // CFS rows
    let cfsRows = '';
    if (boxes.length > 0) {
      cfsRows = `
        <div class="cfs-rows">
          ${boxes.map((box) => this._renderCFSRow(box)).join('')}
        </div>
      `;
    }

    // External section
    let externalSection = '';
    if (external) {
      const safeType = external.type && !["unknown", "unavailable", "—", "-"].includes(String(external.type).toLowerCase()) ? external.type : "—";
      const safeName = external.name && !["unknown", "unavailable", "—", "-"].includes(String(external.name).toLowerCase()) ? external.name : "—";
      const hasFilament = safeType !== "—" && safeName !== "—";
      const percentTextDisplay = hasFilament ? (external.percentText || '—') : '—';
      const displayName = hasFilament ? `${safeName} ${safeType}` : '—';
      const color = external.color || '#cccccc';
      
      const isPrinterBusy = this._isPrinterBusy();
      const editBtnClass = isPrinterBusy ? 'edit-btn-mini disabled' : 'edit-btn-mini';
      const editIcon = isPrinterBusy ? 'mdi:lock' : 'mdi:pencil';
      const editBtn = `<div class="${editBtnClass}" data-action="edit"><ha-icon icon="${editIcon}"></ha-icon></div>`;
      
      externalSection = `
        <div class="external-section">
          <div class="external-compact" data-eid="${external.entity_id}" data-box="-1" data-slot="-1">
            ${editBtn}
            <div class="ext-dot" style="background: conic-gradient(${color} 100%, rgba(var(--rgb-primary-text-color), 0.1) 0)">EXT</div>
            <div class="ext-compact-info">
              <div>${displayName}</div>
              <div>${percentTextDisplay}</div>
            </div>
          </div>
        </div>
      `;
    }

    return `${cfsRows}${externalSection}`;
  }

  _renderCFSRow(box) {
    const tempStr = box.temp !== "—" ? box.temp : '';
    const humStr = box.humidity !== "—" ? box.humidity : '';

    let envHtml = '';
    if (tempStr || humStr) {
      envHtml = `
        <div class="env-mini">
          ${tempStr ? `<div class="temp">${tempStr}</div>` : ''}
          ${humStr ? `<div class="hum" style="color: ${box.humidityColor}">${humStr}</div>` : ''}
        </div>
      `;
    }

    return `
      <div class="cfs-row">
        <div class="cfs-label">CFS ${box.id + 1}</div>
        <div class="spools-inline">
          ${box.slots.map((slot) => this._renderSpoolMini(slot)).join('')}
        </div>
        ${envHtml}
      </div>
    `;
  }

  _renderSpoolCard(slot) {
    if (!slot) {
      return `<div class="spool-card"></div>`;
    }

    const isActive = slot.selected === 1 || slot.selected === true;
    const color = slot.color || '#cccccc';
    const safeType = slot.type && !["unknown", "unavailable", "—", "-"].includes(String(slot.type).toLowerCase()) ? slot.type : "—";
    const safeName = slot.name && !["unknown", "unavailable", "—", "-"].includes(String(slot.name).toLowerCase()) ? slot.name : "—";
    
    // If no filament (type is "—" or name is "—"), show 0% regardless of actual value
    const hasFilament = safeType !== "—" && safeName !== "—";
    const pct = hasFilament && slot.percent !== null ? slot.percent : 0;
    const pctDisplay = hasFilament && slot.percent !== null ? Math.round(slot.percent) : 0;
    const percentTextDisplay = hasFilament ? (slot.percentText || '—') : '—';

    const badge = isActive ? '<div class="status-badge"></div>' : '';
    
    const isPrinterBusy = this._isPrinterBusy();
    const editBtnClass = isPrinterBusy ? 'edit-btn disabled' : 'edit-btn';
    const editIcon = isPrinterBusy ? 'mdi:lock' : 'mdi:pencil';
    const editBtn = `<div class="${editBtnClass}" data-action="edit"><ha-icon icon="${editIcon}"></ha-icon></div>`;

    return `
      <div class="spool-card ${isActive ? 'active' : ''}" data-eid="${slot.entity_id}" data-box="${slot.boxId}" data-slot="${slot.id}">
        ${badge}
        ${editBtn}
        <div class="ring-container">
          <div class="ring-outer" style="--spool-color: ${color}; --spool-pct: ${pct}%"></div>
          <div class="ring-inner">
            <span class="spool-pct">${pctDisplay}%</span>
            <span class="spool-label">${safeType}</span>
          </div>
        </div>
        <div class="material-name">${safeName}</div>
        <div class="color-name">${percentTextDisplay}</div>
      </div>
    `;
  }

  _renderSpoolMini(slot) {
    const showType = this._cfg.show_type_in_mini;
    
    if (!slot) {
      if (showType) {
        return `<div class="spool-mini-wrapper"><div class="spool-mini" style="--spool-color: #333; --spool-pct: 0%"><span>—</span></div><div class="spool-mini-type">—</div></div>`;
      }
      return `<div class="spool-mini" style="--spool-color: #333; --spool-pct: 0%"><span>—</span></div>`;
    }

    const isActive = slot.selected === 1 || slot.selected === true;
    const color = slot.color || '#cccccc';
    const safeType = slot.type && !["unknown", "unavailable", "—", "-"].includes(String(slot.type).toLowerCase()) ? slot.type : "—";
    const safeName = slot.name && !["unknown", "unavailable", "—", "-"].includes(String(slot.name).toLowerCase()) ? slot.name : null;
    
    // If no filament (type is "—" or name is empty/dash), show 0% regardless of actual value
    const hasFilament = safeType !== "—" && safeName !== null;
    const pct = hasFilament && slot.percent !== null ? slot.percent : 0;
    const pctDisplay = hasFilament && slot.percent !== null ? Math.round(slot.percent) : 0;

    const isPrinterBusy = this._isPrinterBusy();
    const editBtnClass = isPrinterBusy ? 'edit-btn-mini disabled' : 'edit-btn-mini';
    const editIcon = isPrinterBusy ? 'mdi:lock' : 'mdi:pencil';
    const editBtn = `<div class="${editBtnClass}" data-action="edit"><ha-icon icon="${editIcon}"></ha-icon></div>`;

    if (showType) {
      return `
        <div class="spool-mini-wrapper" data-eid="${slot.entity_id}" data-box="${slot.boxId}" data-slot="${slot.id}">
          ${editBtn}
          <div class="spool-mini ${isActive ? 'active' : ''}" 
               style="--spool-color: ${color}; --spool-pct: ${pct}%">
            <span>${pctDisplay}</span>
          </div>
          <div class="spool-mini-type">${safeType}</div>
        </div>
      `;
    }

    return `
      <div class="spool-mini-container" data-eid="${slot.entity_id}" data-box="${slot.boxId}" data-slot="${slot.id}">
        ${editBtn}
        <div class="spool-mini ${isActive ? 'active' : ''}" 
             style="--spool-color: ${color}; --spool-pct: ${pct}%">
          <span>${pctDisplay}</span>
        </div>
      </div>
    `;
  }

  _renderBoxMode(boxes, external) {
    if (boxes.length === 0 && !external) {
      return `<div class="no-data">No CFS data available</div>`;
    }

    // Unit selector (only if we have multiple boxes)
    let unitSelector = '';
    if (boxes.length > 1) {
      unitSelector = `
        <div class="unit-selector">
          ${boxes.map((box, idx) => `
            <button class="unit-btn ${idx === this._selectedCFS ? 'active' : ''}" data-cfs="${idx}">
              CFS ${box.id + 1}
            </button>
          `).join('')}
        </div>
      `;
    }

    // Get the selected box
    const selectedBox = boxes[this._selectedCFS] || boxes[0];
    if (!selectedBox && !external) {
      return `<div class="no-data">No CFS data available</div>`;
    }

    // Header
    const header = `
      <div class="header">
        <div class="title-section">
          <div class="title">${this._cfg.name || 'Creality CFS'}</div>
        </div>
      </div>
    `;

    // Box view with image and overlays
    let boxView = '';
    if (selectedBox) {
      // Environment info for box view
      const tempStr = selectedBox.temp !== "—" ? selectedBox.temp : '';
      const humStr = selectedBox.humidity !== "—" ? selectedBox.humidity : '';
      let envInfo = '';
      
      if (tempStr || humStr) {
        const tempHtml = tempStr ? `<span class="box-env-temp">${tempStr}</span>` : '';
        const humHtml = humStr ? `<span class="box-env-hum" style="color: ${selectedBox.humidityColor}">${humStr}</span>` : '';
        const separator = tempStr && humStr ? ' <span style="color: rgba(255,255,255,0.5)">•</span> ' : '';
        envInfo = `<div class="box-env-info">${tempHtml}${separator}${humHtml}</div>`;
      }

      boxView = `
        <div class="box-view-container">
          <img src="${this._basePath}/cfs_box.png" alt="CFS Box" class="box-image" />
          <div class="box-overlays">
            ${selectedBox.slots.map((slot, idx) => this._renderSpoolOverlay(slot, idx)).join('')}
            ${envInfo}
          </div>
        </div>
      `;
    }

    // External section
    let externalSection = '';
    if (external) {
      const safeType = external.type && !["unknown", "unavailable", "—", "-"].includes(String(external.type).toLowerCase()) ? external.type : "—";
      const safeName = external.name && !["unknown", "unavailable", "—", "-"].includes(String(external.name).toLowerCase()) ? external.name : "—";
      const hasFilament = safeType !== "—" && safeName !== "—";
      const pct = hasFilament && external.percent !== null ? external.percent : 0;
      const percentTextDisplay = hasFilament ? (external.percentText || '—') : '—';
      const displayName = hasFilament ? `${safeName} ${safeType}` : '—';
      const color = external.color || '#cccccc';
      
      const isPrinterBusy = this._isPrinterBusy();
      const editBtnClass = isPrinterBusy ? 'edit-btn-mini disabled' : 'edit-btn-mini';
      const editIcon = isPrinterBusy ? 'mdi:lock' : 'mdi:pencil';
      const editBtn = `<div class="${editBtnClass}" data-action="edit"><ha-icon icon="${editIcon}"></ha-icon></div>`;
      
      externalSection = `
        <div class="external-section">
          <div class="external-normal" data-eid="${external.entity_id}" data-box="-1" data-slot="-1">
            ${editBtn}
            <div class="ext-icon" style="background: ${color}">EXT</div>
            <div class="ext-info">
              <div class="ext-name">${displayName}</div>
              <div class="ext-bar">
                <div class="ext-fill" style="width: ${pct}%; background: ${color}"></div>
              </div>
            </div>
            <div class="ext-percent">${percentTextDisplay}</div>
          </div>
        </div>
      `;
    }

    return `${unitSelector}${header}${boxView}${externalSection}`;
  }

  _renderSpoolOverlay(slot, idx) {
    if (!slot) {
      return `<div class="spool-overlay" data-slot-index="${idx}"></div>`;
    }

    const isActive = slot.selected === 1 || slot.selected === true;
    const color = slot.color || 'rgba(255,255,255,0.1)';
    const safeType = slot.type && !["unknown", "unavailable", "—", "-"].includes(String(slot.type).toLowerCase()) ? slot.type : "—";
    const safeName = slot.name && !["unknown", "unavailable", "—", "-"].includes(String(slot.name).toLowerCase()) ? slot.name : null;
    
    const hasFilament = safeType !== "—" && safeName !== null;
    const pct = hasFilament && slot.percent !== null ? slot.percent : 0;
    const pctDisplay = hasFilament && slot.percent !== null ? Math.round(slot.percent) : 0;

    const isPrinterBusy = this._isPrinterBusy();
    const editBtnClass = isPrinterBusy ? 'edit-btn-mini disabled' : 'edit-btn-mini';
    const editIcon = isPrinterBusy ? 'mdi:lock' : 'mdi:pencil';
    const editBtn = `<div class="${editBtnClass}" data-action="edit"><ha-icon icon="${editIcon}"></ha-icon></div>`;

    return `
      <div class="spool-overlay ${isActive ? 'active' : ''}" 
           data-eid="${slot.entity_id}" 
           data-box="${slot.boxId}" 
           data-slot="${slot.id}"
           data-slot-index="${idx}">
        ${editBtn}
        <div class="spool-indicator" style="--spool-color: ${color}">
          ${hasFilament ? `
            <div class="spool-type-badge">${safeType}</div>
            <div class="spool-percent-badge">${pctDisplay}%</div>
          ` : ''}
        </div>
      </div>
    `;
  }

  _attachEventHandlers() {
    // Unit selector buttons
    this._root.querySelectorAll('.unit-btn').forEach(btn => {
      btn.onclick = () => {
        const cfsIdx = parseInt(btn.dataset.cfs, 10);
        if (!isNaN(cfsIdx)) {
          this._selectedCFS = cfsIdx;
          this._update();
        }
      };
    });

    // Edit buttons - intercept before spool card click (normal mode)
    this._root.querySelectorAll('.edit-btn').forEach(btn => {
      btn.onclick = (e) => {
        e.stopPropagation();
        
        // Check if disabled
        if (btn.classList.contains('disabled')) {
          this._showToast('Cannot edit material while printer is busy');
          return;
        }
        
        const card = btn.closest('.spool-card, .external-normal');
        if (card) {
          const boxId = parseInt(card.dataset.box, 10);
          const slotId = parseInt(card.dataset.slot, 10);
          const eid = card.dataset.eid;
          this._showEditDialog(boxId, slotId, eid);
        }
      };
    });

    // Edit buttons for mini spools and external compact
    this._root.querySelectorAll('.edit-btn-mini').forEach(btn => {
      btn.onclick = (e) => {
        e.stopPropagation();
        
        // Check if disabled
        if (btn.classList.contains('disabled')) {
          this._showToast('Cannot edit material while printer is busy');
          return;
        }
        
        const container = btn.closest('.spool-mini-wrapper, .spool-mini-container, .external-compact, .spool-overlay, .external-normal');
        if (container) {
          const boxId = parseInt(container.dataset.box, 10);
          const slotId = parseInt(container.dataset.slot, 10);
          const eid = container.dataset.eid;
          this._showEditDialog(boxId, slotId, eid);
        }
      };
    });

    // Spool cards and mini spools - show more info
    this._root.querySelectorAll('.spool-card, .spool-mini, .spool-mini-wrapper .spool-mini, .external-normal, .external-compact').forEach(el => {
      const eid = el.dataset.eid;
      if (!eid) return;
      
      el.onclick = () => {
        this.dispatchEvent(new CustomEvent("hass-more-info", {
          detail: { entityId: eid },
          bubbles: true,
          composed: true,
        }));
      };
    });
  }

  _isPrinterBusy() {
    if (!this._hass) return false;
    
    // Try to find the print status sensor
    // It should be in the same integration, typically sensor.<name>_print_status
    const states = this._hass.states;
    
    // Look for the print status entity by checking all entities
    for (const entityId in states) {
      if (entityId.includes('_print_status') && entityId.startsWith('sensor.')) {
        const entity = states[entityId];
        // Check if this entity belongs to the same device/integration
        // We can assume it's the right one if it exists and matches the pattern
        const state = entity.state?.toLowerCase();
        
        // Block editing during these states
        const busyStates = ['printing', 'paused', 'self-testing', 'processing'];
        if (busyStates.includes(state)) {
          return true;
        }
      }
    }
    
    return false;
  }

  _showEditDialog(boxId, slotId, entityId) {
    if (!this._hass) return;

    // Double-check if printer is busy
    if (this._isPrinterBusy()) {
      this._showToast('Cannot edit material while printer is busy');
      return;
    }

    // Get current values from entity
    const entity = this._hass.states[entityId];
    const currentType = entity?.attributes?.type || "PLA";
    const currentName = entity?.state || "Ender-PLA";
    let currentColor = entity?.attributes?.color_hex || "#06c84f";
    const currentVendor = entity?.attributes?.vendor || "Creality";
    const currentMinTemp = entity?.attributes?.min_temp || 190;
    const currentMaxTemp = entity?.attributes?.max_temp || 240;
    const currentPressure = entity?.attributes?.pressure || 0.04;

    // Sanitize color to ensure it's valid hex
    currentColor = KCFSCard._sanitizeColor(currentColor);

    // Create modal overlay
    const overlay = document.createElement('div');
    overlay.style.position = 'fixed';
    overlay.style.top = '0';
    overlay.style.left = '0';
    overlay.style.width = '100%';
    overlay.style.height = '100%';
    overlay.style.backgroundColor = 'rgba(0, 0, 0, 0.5)';
    overlay.style.zIndex = '1000';
    overlay.style.display = 'flex';
    overlay.style.alignItems = 'center';
    overlay.style.justifyContent = 'center';
    overlay.style.backdropFilter = 'blur(4px)';
    
    // Create dialog container
    const dialog = document.createElement('div');
    dialog.style.backgroundColor = 'var(--card-background-color)';
    dialog.style.borderRadius = '12px';
    dialog.style.padding = '24px';
    dialog.style.maxWidth = '500px';
    dialog.style.width = '90%';
    dialog.style.maxHeight = '90vh';
    dialog.style.overflow = 'auto';
    dialog.style.boxShadow = '0 8px 32px rgba(0, 0, 0, 0.3)';
    
    // Create dialog header
    const header = document.createElement('div');
    header.className = 'dialog-header';
    
    const headerText = document.createElement('span');
    // Check if it's external filament (boxId and slotId are -1)
    if (boxId === -1 && slotId === -1) {
      headerText.textContent = 'Edit Material - External Filament';
    } else {
      headerText.textContent = `Edit Material - Box ${boxId + 1} Slot ${slotId + 1}`;
    }
    
    const closeBtn = document.createElement('ha-icon');
    closeBtn.setAttribute('icon', 'mdi:close');
    closeBtn.className = 'dialog-close';
    closeBtn.onclick = () => {
      if (document.body.contains(overlay)) {
        document.body.removeChild(overlay);
      }
    };
    
     header.appendChild(headerText);
     header.appendChild(closeBtn);
     
     // Create form content
     const form = this._renderEditForm(boxId, slotId, {
       entity_id: entityId,
       type: currentType,
       name: currentName,
       color: currentColor,
       vendor: currentVendor,
       minTemp: currentMinTemp,
       maxTemp: currentMaxTemp,
       pressure: currentPressure
     }, overlay);
    
    dialog.appendChild(header);
    dialog.appendChild(form);
    overlay.appendChild(dialog);
    
    // Close on overlay click (but not dialog click)
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) {
        document.body.removeChild(overlay);
      }
    });
    
    // Close on Escape key
    const escapeHandler = (e) => {
      if (e.key === 'Escape') {
        if (document.body.contains(overlay)) {
          document.body.removeChild(overlay);
        }
        document.removeEventListener('keydown', escapeHandler);
      }
    };
    document.addEventListener('keydown', escapeHandler);
    
    document.body.appendChild(overlay);
  }

  _renderEditForm(boxId, slotId, values, overlay) {
    const container = document.createElement('div');
    container.className = 'dialog-form';
    
    // Type field
    const typeField = document.createElement('div');
    typeField.className = 'form-field';
    
    const typeLabel = document.createElement('label');
    typeLabel.className = 'form-label';
    typeLabel.textContent = 'Material Type';
    
    const typeInput = document.createElement('input');
    typeInput.className = 'form-input';
    typeInput.type = 'text';
    typeInput.value = values.type;
    typeInput.id = 'input-type';
    typeInput.placeholder = 'PLA, PETG, ABS, etc.';
    
    typeField.appendChild(typeLabel);
    typeField.appendChild(typeInput);
    
    // Name field
    const nameField = document.createElement('div');
    nameField.className = 'form-field';
    
    const nameLabel = document.createElement('label');
    nameLabel.className = 'form-label';
    nameLabel.textContent = 'Material Name';
    
    const nameInput = document.createElement('input');
    nameInput.className = 'form-input';
    nameInput.type = 'text';
    nameInput.value = values.name;
    nameInput.id = 'input-name';
    nameInput.placeholder = 'Ender-PLA';
    
    nameField.appendChild(nameLabel);
    nameField.appendChild(nameInput);
    
    // Vendor field
    const vendorField = document.createElement('div');
    vendorField.className = 'form-field';
    
    const vendorLabel = document.createElement('label');
    vendorLabel.className = 'form-label';
    vendorLabel.textContent = 'Vendor/Brand';
    
    const vendorInput = document.createElement('input');
    vendorInput.className = 'form-input';
    vendorInput.type = 'text';
    vendorInput.value = values.vendor;
    vendorInput.id = 'input-vendor';
    vendorInput.placeholder = 'Creality';
    
    vendorField.appendChild(vendorLabel);
    vendorField.appendChild(vendorInput);
    
     // Color field with presets
     const colorField = document.createElement('div');
     colorField.className = 'form-field';
     
     const colorLabel = document.createElement('label');
     colorLabel.className = 'form-label';
     colorLabel.textContent = 'Color (with Presets)';
     colorField.appendChild(colorLabel);

     // Color presets container
     const presetsContainer = document.createElement('div');
     presetsContainer.style.marginBottom = '10px';
     presetsContainer.style.display = 'flex';
     presetsContainer.style.flexWrap = 'wrap';
     presetsContainer.style.gap = '8px';
     presetsContainer.style.alignItems = 'center';

     // Add preset buttons (Creality standards + custom)
     const allColors = this._presetsManager.getAllColors();
     const standardColors = this._presetsManager.getStandardColors();
     const customPresets = this._presetsManager.getPresets();

     // Group buttons: Standard first, then custom
     const addPresetButtons = (colors, isCustom = false) => {
       Object.entries(colors).forEach(([name, hex]) => {
         const btn = document.createElement('button');
         btn.type = 'button';
         btn.style.width = '28px';
         btn.style.height = '28px';
         btn.style.padding = '0';
         btn.style.border = '2px solid transparent';
         btn.style.borderRadius = '4px';
         btn.style.background = hex;
         btn.style.cursor = 'pointer';
         btn.style.transition = 'all 0.2s';
         btn.title = `${name}${isCustom ? ' (Custom)' : ''}`;
         btn.onclick = () => {
           colorInput.value = hex;
           // Highlight selected button
           document.querySelectorAll('[data-preset-btn]').forEach(b => {
             b.style.borderColor = 'transparent';
           });
           btn.style.borderColor = 'var(--primary-text-color)';
         };
         btn.setAttribute('data-preset-btn', '1');
         presetsContainer.appendChild(btn);
       });
     };

     addPresetButtons(standardColors, false);

     // Separator for custom presets
     if (Object.keys(customPresets).length > 0) {
       const separator = document.createElement('div');
       separator.style.flexBasis = '100%';
       separator.style.height = '1px';
       separator.style.margin = '4px 0';
       separator.style.background = 'rgba(var(--rgb-primary-text-color), 0.1)';
       presetsContainer.appendChild(separator);
       addPresetButtons(customPresets, true);
     }

     colorField.appendChild(presetsContainer);

     // Color input + preset management
     const colorInputContainer = document.createElement('div');
     colorInputContainer.style.display = 'grid';
     colorInputContainer.style.gridTemplateColumns = '1fr auto';
     colorInputContainer.style.gap = '8px';
     colorInputContainer.style.alignItems = 'center';

     const colorInput = document.createElement('input');
     colorInput.className = 'form-input';
     colorInput.type = 'color';
     colorInput.value = values.color;
     colorInput.id = 'input-color';
     colorInput.style.height = '40px';
     colorInput.style.cursor = 'pointer';
     colorInputContainer.appendChild(colorInput);

     // Save as preset button
     const savePresetBtn = document.createElement('button');
     savePresetBtn.type = 'button';
     savePresetBtn.style.padding = '8px 12px';
     savePresetBtn.style.background = 'var(--primary-color)';
     savePresetBtn.style.color = 'white';
     savePresetBtn.style.border = 'none';
     savePresetBtn.style.borderRadius = '4px';
     savePresetBtn.style.cursor = 'pointer';
     savePresetBtn.style.fontSize = '12px';
     savePresetBtn.textContent = '💾 Save';
     savePresetBtn.title = 'Save current color as custom preset';
     savePresetBtn.onclick = () => {
       const presetName = prompt('Enter preset name:', 'My Color');
       if (presetName) {
         this._presetsManager.savePreset(presetName, colorInput.value);
         this._showToast(`Preset "${presetName}" saved!`);
         // Refresh the form to show new preset
         setTimeout(() => {
           if (document.body.contains(overlay)) {
             document.body.removeChild(overlay);
           }
           this._showEditDialog(boxId, slotId, values.entity_id || undefined);
         }, 500);
       }
     };
     colorInputContainer.appendChild(savePresetBtn);

     colorField.appendChild(colorInputContainer);
    
    // Temperature row
    const tempRow = document.createElement('div');
    tempRow.className = 'form-row';
    
    const minTempField = document.createElement('div');
    minTempField.className = 'form-field';
    
    const minTempLabel = document.createElement('label');
    minTempLabel.className = 'form-label';
    minTempLabel.textContent = 'Min Temp (°C)';
    
    const minTempInput = document.createElement('input');
    minTempInput.className = 'form-input';
    minTempInput.type = 'number';
    minTempInput.value = values.minTemp;
    minTempInput.id = 'input-mintemp';
    minTempInput.min = '150';
    minTempInput.max = '300';
    minTempInput.step = '1';
    
    minTempField.appendChild(minTempLabel);
    minTempField.appendChild(minTempInput);
    
    const maxTempField = document.createElement('div');
    maxTempField.className = 'form-field';
    
    const maxTempLabel = document.createElement('label');
    maxTempLabel.className = 'form-label';
    maxTempLabel.textContent = 'Max Temp (°C)';
    
    const maxTempInput = document.createElement('input');
    maxTempInput.className = 'form-input';
    maxTempInput.type = 'number';
    maxTempInput.value = values.maxTemp;
    maxTempInput.id = 'input-maxtemp';
    maxTempInput.min = '150';
    maxTempInput.max = '350';
    maxTempInput.step = '1';
    
    maxTempField.appendChild(maxTempLabel);
    maxTempField.appendChild(maxTempInput);
    
    tempRow.appendChild(minTempField);
    tempRow.appendChild(maxTempField);
    
    // Pressure field
    const pressureField = document.createElement('div');
    pressureField.className = 'form-field';
    
    const pressureLabel = document.createElement('label');
    pressureLabel.className = 'form-label';
    pressureLabel.textContent = 'Pressure Advance';
    
    const pressureInput = document.createElement('input');
    pressureInput.className = 'form-input';
    pressureInput.type = 'number';
    pressureInput.value = values.pressure;
    pressureInput.id = 'input-pressure';
    pressureInput.min = '0';
    pressureInput.max = '1';
    pressureInput.step = '0.01';
    
     pressureField.appendChild(pressureLabel);
     pressureField.appendChild(pressureInput);
     
      // Buttons
     const buttonRow = document.createElement('div');
     buttonRow.className = 'dialog-actions';
     buttonRow.style.position = 'sticky';
     buttonRow.style.bottom = '-24px'; // Compensa o padding do dialog
     buttonRow.style.background = 'var(--card-background-color)';
     buttonRow.style.padding = '16px 0 0 0';
     buttonRow.style.marginTop = '16px';
     buttonRow.style.borderTop = '1px solid rgba(var(--rgb-primary-text-color), 0.1)';
     buttonRow.style.zIndex = '10';
     
     const cancelBtn = document.createElement('button');
     cancelBtn.className = 'dialog-btn dialog-btn-cancel';
     cancelBtn.textContent = 'Cancel';
     cancelBtn.onclick = () => {
       if (document.body.contains(overlay)) {
         document.body.removeChild(overlay);
       }
     };
     
     const saveBtn = document.createElement('button');
     saveBtn.className = 'dialog-btn dialog-btn-save';
     saveBtn.textContent = 'Save';
     saveBtn.id = 'btn-save';
     saveBtn.onclick = async () => {
       console.log('Save button clicked');
       saveBtn.disabled = true;
       saveBtn.textContent = 'Saving...';
       
       try {
         const formData = {
           type: typeInput.value,
           name: nameInput.value,
           vendor: vendorInput.value,
           color: colorInput.value,
           min_temp: parseFloat(minTempInput.value),
           max_temp: parseFloat(maxTempInput.value),
           pressure: parseFloat(pressureInput.value),
         };
         
         console.log('Form data:', formData);
         await this._saveMaterial(boxId, slotId, formData);
         
         if (document.body.contains(overlay)) {
           document.body.removeChild(overlay);
         }
       } catch (error) {
         console.error('Error:', error);
         saveBtn.disabled = false;
         saveBtn.textContent = 'Save';
         this._showToast(`Error: ${error.message}`);
       }
     };
     
     buttonRow.appendChild(cancelBtn);
     buttonRow.appendChild(saveBtn);
    
     // Assemble form
     container.appendChild(typeField);
     container.appendChild(nameField);
     container.appendChild(vendorField);
     container.appendChild(colorField);
     container.appendChild(tempRow);
     container.appendChild(pressureField);
     
     // Preset management section
     const hasCustomPresets = Object.keys(customPresets).length > 0;
     if (hasCustomPresets) {
       const presetMgmtSection = document.createElement('div');
       presetMgmtSection.style.marginTop = '16px';
       presetMgmtSection.style.paddingTop = '16px';
       presetMgmtSection.style.borderTop = '1px solid rgba(var(--rgb-primary-text-color), 0.1)';
       
       const mgmtLabel = document.createElement('label');
       mgmtLabel.className = 'form-label';
       mgmtLabel.textContent = 'Manage Custom Presets';
       presetMgmtSection.appendChild(mgmtLabel);
       
       const presetsList = document.createElement('div');
       presetsList.style.display = 'grid';
       presetsList.style.gap = '8px';
       
       Object.entries(customPresets).forEach(([name, color]) => {
         const presetItem = document.createElement('div');
         presetItem.style.display = 'grid';
         presetItem.style.gridTemplateColumns = '24px 1fr auto auto';
         presetItem.style.alignItems = 'center';
         presetItem.style.gap = '8px';
         presetItem.style.padding = '8px';
         presetItem.style.background = 'rgba(var(--rgb-primary-text-color), 0.05)';
         presetItem.style.borderRadius = '4px';
         
         const colorBox = document.createElement('div');
         colorBox.style.width = '24px';
         colorBox.style.height = '24px';
         colorBox.style.background = color;
         colorBox.style.borderRadius = '4px';
         colorBox.style.border = '1px solid rgba(var(--rgb-primary-text-color), 0.2)';
         presetItem.appendChild(colorBox);
         
         const nameLabel = document.createElement('span');
         nameLabel.textContent = name;
         nameLabel.style.fontSize = '12px';
         presetItem.appendChild(nameLabel);
         
         const renameBtn = document.createElement('button');
         renameBtn.type = 'button';
         renameBtn.textContent = '✏️';
         renameBtn.style.padding = '4px 8px';
         renameBtn.style.background = 'var(--primary-color)';
         renameBtn.style.color = 'white';
         renameBtn.style.border = 'none';
         renameBtn.style.borderRadius = '3px';
         renameBtn.style.cursor = 'pointer';
         renameBtn.style.fontSize = '12px';
         renameBtn.onclick = () => {
           const newName = prompt('New preset name:', name);
           if (newName && newName !== name) {
             this._presetsManager.renamePreset(name, newName);
             this._showToast(`Preset renamed to "${newName}"`);
             // Refresh dialog
             setTimeout(() => {
               if (document.body.contains(overlay)) {
                 document.body.removeChild(overlay);
               }
               this._showEditDialog(boxId, slotId, values.entity_id || undefined);
             }, 300);
           }
         };
         presetItem.appendChild(renameBtn);
         
         const deleteBtn = document.createElement('button');
         deleteBtn.type = 'button';
         deleteBtn.textContent = '🗑️';
         deleteBtn.style.padding = '4px 8px';
         deleteBtn.style.background = 'var(--error-color)';
         deleteBtn.style.color = 'white';
         deleteBtn.style.border = 'none';
         deleteBtn.style.borderRadius = '3px';
         deleteBtn.style.cursor = 'pointer';
         deleteBtn.style.fontSize = '12px';
         deleteBtn.onclick = () => {
           if (confirm(`Delete preset "${name}"?`)) {
             this._presetsManager.deletePreset(name);
             this._showToast(`Preset "${name}" deleted`);
             // Refresh dialog
             setTimeout(() => {
               if (document.body.contains(overlay)) {
                 document.body.removeChild(overlay);
               }
               this._showEditDialog(boxId, slotId, values.entity_id || undefined);
             }, 300);
           }
         };
         presetItem.appendChild(deleteBtn);
         
         presetsList.appendChild(presetItem);
       });
       
       presetMgmtSection.appendChild(presetsList);
       container.appendChild(presetMgmtSection);
     }
     
     container.appendChild(buttonRow);
     
     return container;
   }

  async _saveMaterial(boxId, slotId, formData) {
    if (!this._hass) {
      console.error('_saveMaterial: hass not available');
      return;
    }

    console.log('_saveMaterial called with:', { boxId, slotId, formData });

    try {
      // Get device_id from the first entity in this integration
      // We need to find the device associated with this integration
      const deviceId = await this._getDeviceId();
      
      console.log('Device ID found:', deviceId);
      
      if (!deviceId) {
        alert('Could not find device ID. Please check your configuration.');
        return;
      }

      // For external filament: boxId = -1, slotId = -1 -> send box_id: 0, slot_id: 0
      // For CFS filaments: boxId = 0-3, slotId = 0-3 -> send box_id: boxId+1, slot_id: slotId
      const isExternal = (boxId === -1 && slotId === -1);
      
      // NORMALIZATION: Validate and normalize metadata fields to prevent duplicates
      // Ensure no empty or invalid fields are sent to firmware
      const normalizedData = this._normalizeFilamentMetadata({
        type: formData.type,
        name: formData.name,
        vendor: formData.vendor,
        color: formData.color,
        min_temp: formData.min_temp,
        max_temp: formData.max_temp,
        pressure: formData.pressure,
      });

      const serviceData = {
        device_id: deviceId,
        box_id: isExternal ? 0 : (boxId + 1),
        slot_id: isExternal ? 0 : slotId,
        ...normalizedData
      };

      console.log('Calling service ha_creality_ws.set_cfs_material with normalized data:', serviceData);
      
      await this._hass.callService('ha_creality_ws', 'set_cfs_material', serviceData);
      
      console.log('Service call completed successfully');
      
      // Show success feedback
      this._showToast('Material settings saved successfully!');
      
      // Request updated CFS info after a short delay
      setTimeout(async () => {
        console.log('Requesting CFS info update...');
        await this._hass.callService('ha_creality_ws', 'request_cfs_info', { device_id: deviceId });
      }, 1000);
      
    } catch (error) {
      console.error('Failed to save material:', error);
      alert(`Failed to save material: ${error.message}`);
      throw error;
    }
  }

  /**
   * Normalize filament metadata to prevent duplicates and invalid data
   * NORMALIZATION RULES:
   * 1. Trim and validate all string fields
   * 2. Remove empty/invalid fields
   * 3. Ensure type and name don't exceed safe lengths (40 chars)
   * 4. Validate temperature ranges
   * 5. Validate pressure range (0-1)
   * 6. Ensure color is valid hex format
   */
  _normalizeFilamentMetadata(data) {
    const normalized = {};

    // Type field - max 20 chars
    if (data.type && String(data.type).trim()) {
      normalized.type = String(data.type).trim().substring(0, 20).toUpperCase();
    } else {
      normalized.type = "PLA";
    }

    // Name field - max 40 chars, prevent duplication with type
    if (data.name && String(data.name).trim()) {
      let name = String(data.name).trim().substring(0, 40);
      // Avoid duplicating type in name if they're similar
      if (name.toUpperCase() === normalized.type) {
        name = `${normalized.type}-Filament`;
      }
      normalized.name = name;
    } else {
      normalized.name = `${normalized.type}-Filament`;
    }

    // Vendor field - optional, max 30 chars
    if (data.vendor && String(data.vendor).trim()) {
      normalized.vendor = String(data.vendor).trim().substring(0, 30);
    } else {
      normalized.vendor = "Creality";
    }

    // Color field - must be valid hex
    if (data.color) {
      const sanitized = KCFSCard._sanitizeColor(data.color);
      normalized.color = sanitized;
    } else {
      normalized.color = "#cccccc";
    }

    // Temperature fields - must be within reasonable bounds
    const minTemp = parseInt(data.min_temp) || 190;
    const maxTemp = parseInt(data.max_temp) || 240;
    normalized.min_temp = Math.max(150, Math.min(300, minTemp));
    normalized.max_temp = Math.max(normalized.min_temp, Math.min(350, maxTemp));

    // Pressure field - must be 0-1
    const pressure = parseFloat(data.pressure) || 0.04;
    normalized.pressure = Math.max(0, Math.min(1, pressure));

    console.log('Metadata normalized:', { original: data, normalized });
    return normalized;
  }

  async _getDeviceId() {
    console.log('_getDeviceId: Starting device ID lookup...');
    
    // Try to get device_id from any sensor entity in this card's config
    const states = this._hass.states;
    
    // Check all configured entities to find one with a device_id
    for (let box = 0; box < 4; box++) {
      for (let slot = 0; slot < 4; slot++) {
        const filamentEid = this._cfg[`box${box}_slot${slot}_filament`];
        if (filamentEid && states[filamentEid]) {
          const entity = states[filamentEid];
          console.log(`Checking entity ${filamentEid}:`, entity);
          
          // Get device_id from entity registry (if available via hass.entities)
          if (entity.attributes?.device_id) {
            console.log('Found device_id in attributes:', entity.attributes.device_id);
            return entity.attributes.device_id;
          }
        }
      }
    }
    
    console.log('device_id not found in entity attributes, trying entity registry...');
    
    // Fallback: try to get it from entity registry
    try {
      const entities = await this._hass.callWS({ type: 'config/entity_registry/list' });
      console.log('Entity registry entries:', entities.length);
      
      // Find any ha_creality_ws entity and get its device_id
      for (const entityEntry of entities) {
        if (entityEntry.platform === 'ha_creality_ws' && entityEntry.device_id) {
          console.log('Found device_id from entity registry:', entityEntry.device_id, 'for entity:', entityEntry.entity_id);
          
          // Double check this device has one of our configured entities
          for (let box = 0; box < 4; box++) {
            for (let slot = 0; slot < 4; slot++) {
              const filamentEid = this._cfg[`box${box}_slot${slot}_filament`];
              if (filamentEid === entityEntry.entity_id) {
                console.log('Confirmed device_id matches configured entity');
                return entityEntry.device_id;
              }
            }
          }
        }
      }
      
      // If we still haven't found it, return the first ha_creality_ws device
      for (const entityEntry of entities) {
        if (entityEntry.platform === 'ha_creality_ws' && entityEntry.device_id) {
          console.log('Using first available ha_creality_ws device:', entityEntry.device_id);
          return entityEntry.device_id;
        }
      }
    } catch (e) {
      console.error('Failed to get device_id from entity registry:', e);
    }
    
    console.error('Could not find device_id');
    return null;
  }

  _showToast(message) {
    // Simple toast notification
    const toast = document.createElement('div');
    toast.textContent = message;
    toast.style.cssText = `
      position: fixed;
      bottom: 20px;
      left: 50%;
      transform: translateX(-50%);
      background: var(--primary-color);
      color: white;
      padding: 12px 24px;
      border-radius: 8px;
      z-index: 10000;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
    `;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
  }

  getCardSize() {
    return 3;
  }
}

customElements.define(CARD_TAG, KCFSCard);

class KCFSCardEditor extends HTMLElement {
  set hass(hass) {
    this._hass = hass;
    if (this._form) {
      this._form.hass = hass;
    }
  }

  setConfig(config) {
    // Create a mutable copy of config for migration
    const migratedConfig = { ...config };
    
    // Migrate old compact_view to new view_mode for backward compatibility
    if (migratedConfig.compact_view !== undefined && migratedConfig.view_mode === undefined) {
      migratedConfig.view_mode = migratedConfig.compact_view ? "compact" : "full";
    }
    
    this._cfg = { ...KCFSCard.getStubConfig(), ...migratedConfig };
    this._render();
  }

  connectedCallback() {
    this._render();
  }

  _render() {
    if (!this._root) {
      this._root = this.attachShadow({ mode: "open" });
    }

    const style = `
      .editor-container { padding: 16px; }
      .tabs { display: flex; border-bottom: 1px solid var(--divider-color); margin-bottom: 16px; }
      .tab { padding: 8px 16px; cursor: pointer; border-bottom: 2px solid transparent; }
      .tab.active { border-bottom-color: var(--primary-color); color: var(--primary-color); }
      .tab-content { display: none; }
      .tab-content.active { display: block; }
      .input-helper { font-size: 0.9em; color: var(--secondary-text-color); margin-top: 4px; padding: 0 8px; }
    `;

    this._root.innerHTML = `
      <style>${style}</style>
      <div class="editor-container">
        <div class="tabs">
          <div class="tab active" data-tab="entities">Entities</div>
          <div class="tab" data-tab="theme">Theme</div>
        </div>
        <div class="tab-content active" id="entities-tab">
          <ha-form id="form"></ha-form>
        </div>
        <div class="tab-content" id="theme-tab">
          <ha-form id="theme-form"></ha-form>
        </div>
      </div>
    `;

    this._setupTabs();
    this._setupEntitiesForm();
    this._setupThemeForm();
  }

  _setupTabs() {
    const tabs = this._root.querySelectorAll(".tab");
    const contents = this._root.querySelectorAll(".tab-content");
    tabs.forEach((tab) => {
      tab.onclick = () => {
        tabs.forEach((t) => t.classList.remove("active"));
        contents.forEach((c) => c.classList.remove("active"));
        tab.classList.add("active");
        this._root.getElementById(`${tab.dataset.tab}-tab`).classList.add("active");
      };
    });
  }

  _setupEntitiesForm() {
    this._form = this._root.getElementById("form");
    this._form.hass = this._hass;
    this._form.data = this._cfg;
    const schema = [
      { name: "name", selector: { text: {} } },
      { name: "external_filament", selector: { entity: { domain: "sensor" } } },
      { name: "external_color", selector: { entity: { domain: "sensor" } } },
      { name: "external_percent", selector: { entity: { domain: "sensor" } } },
    ];

    for (let box = 0; box < 4; box += 1) {
      schema.push({ name: `box${box}_temp`, selector: { entity: { domain: "sensor" } } });
      schema.push({ name: `box${box}_humidity`, selector: { entity: { domain: "sensor" } } });
      for (let slot = 0; slot < 4; slot += 1) {
        schema.push({ name: `box${box}_slot${slot}_filament`, selector: { entity: { domain: "sensor" } } });
        schema.push({ name: `box${box}_slot${slot}_color`, selector: { entity: { domain: "sensor" } } });
        schema.push({ name: `box${box}_slot${slot}_percent`, selector: { entity: { domain: "sensor" } } });
      }
    }

    this._form.schema = schema;
    this._form.computeLabel = (s) => {
      if (s.name === "name") return "Card Title";
      if (s.name === "external_filament") return "External Filament";
      if (s.name === "external_color") return "External Color";
      if (s.name === "external_percent") return "External Percent";

      const boxMatch = s.name.match(/^box(\d+)_(temp|humidity)$/);
      if (boxMatch) {
        const [, boxId, metric] = boxMatch;
        return `Box ${Number(boxId) + 1} ${metric === "temp" ? "Temperature" : "Humidity"}`;
      }

      const slotMatch = s.name.match(/^box(\d+)_slot(\d+)_(filament|color|percent)$/);
      if (slotMatch) {
        const [, boxId, slotId, metric] = slotMatch;
        const labelMap = {
          filament: "Filament",
          color: "Color",
          percent: "Remaining Percent",
        };
        return `Box ${Number(boxId) + 1} Slot ${Number(slotId) + 1} ${labelMap[metric]}`;
      }

      return s.name;
    };
    if (this._form.computeHelper) {
      this._form.computeHelper = () => "";
    }

    this._form.addEventListener("value-changed", (ev) => {
      this._cfg = { ...this._cfg, ...ev.detail.value };
      this._dispatchConfigChange();
    });
  }

  _setupThemeForm() {
    const themeForm = this._root.getElementById("theme-form");
    themeForm.hass = this._hass;
    themeForm.data = this._cfg;
    themeForm.schema = [
      { 
        name: "view_mode", 
        selector: { 
          select: { 
            options: [
              { value: "full", label: "Full View" },
              { value: "compact", label: "Compact View" },
              { value: "box", label: "Box View (Visual)" }
            ]
          } 
        } 
      },
      { name: "show_type_in_mini", selector: { boolean: {} } },
      { name: "compact_view", selector: { boolean: {} } },  // Legacy support
    ];
    themeForm.computeLabel = (s) => ({
      view_mode: "Display Mode",
      show_type_in_mini: "Show Filament Type in Mini Mode",
      compact_view: "Legacy Compact View (deprecated, use Display Mode)",
    }[s.name] || s.name);

    themeForm.addEventListener("value-changed", (ev) => {
      this._cfg = { ...this._cfg, ...ev.detail.value };
      this._dispatchConfigChange();
    });
  }

  _dispatchConfigChange() {
    this.dispatchEvent(new CustomEvent("config-changed", {
      detail: { config: this._cfg },
      bubbles: true,
      composed: true,
    }));
  }
}

customElements.define(EDITOR_TAG, KCFSCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "k-cfs-card",
  name: "Creality CFS Card",
  preview: true,
  description: "A card to control the Creality Filament System (CFS)"
});
