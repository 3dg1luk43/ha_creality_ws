
const CARD_TAG = "k-cfs-card";
const EDITOR_TAG = "k-cfs-card-editor";

const mdi = (name) => `mdi:${name}`;

class KCFSCard extends HTMLElement {
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
  static getStubConfig() {
    const cfg = {
      name: "CFS",
      compact_view: false,
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
    this._cfg = { ...KCFSCard.getStubConfig(), ...config };
    if (!this._root) {
      this._root = this.attachShadow({ mode: "open" });
    }
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  _render() {
    if (!this._root) return;

    const style = `
      ha-card {
        padding: 16px;
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      .header {
        display: flex;
        justify-content: space-between;
        align-items: center;
      }
      .title {
        font-weight: bold;
        font-size: 1.1em;
      }
      .boxes-container {
        display: flex;
        flex-direction: column;
        gap: 16px;
      }
      .box {
        border: 1px solid var(--divider-color);
        border-radius: 8px;
        padding: 12px;
        background: rgba(var(--rgb-primary-text-color), 0.03);
      }
      .box-header {
        display: flex;
        justify-content: space-between;
        margin-bottom: 8px;
        font-size: 0.9em;
        color: var(--secondary-text-color);
      }
      .slots-grid {
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 8px;
      }
      .slot {
        border: 1px solid var(--divider-color);
        border-radius: 6px;
        padding: 8px;
        display: flex;
        flex-direction: column;
        gap: 4px;
        position: relative;
        cursor: pointer;
        transition: transform 0.1s;
        background: var(--card-background-color);
      }
      .slot:active {
        transform: scale(0.98);
      }
      .slot.selected {
        border-color: var(--primary-color);
        box-shadow: 0 0 0 1px var(--primary-color);
      }
      .slot.selected::after {
        content: '';
        position: absolute;
        inset: 0;
        border-radius: 6px;
        border: 2px solid var(--primary-color);
        animation: pulse 2s infinite;
      }
      @keyframes pulse {
        0% { opacity: 0.6; transform: scale(1); }
        50% { opacity: 0.1; transform: scale(1.05); }
        100% { opacity: 0.6; transform: scale(1); }
      }
      .spool-preview {
        height: 8px;
        border-radius: 4px;
        width: 100%;
      }
      .slot-info {
        font-size: 0.8em;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .compact .box-header { margin-bottom: 4px; }
      .compact .slot { padding: 4px; gap: 2px; }
      .compact .slot-info { font-size: 0.7em; }
    `;

    this._root.innerHTML = `
      <ha-card class="${this._cfg.compact_view ? 'compact' : ''}">
        <style>${style}</style>
        <div class="header">
          <div class="title">${this._cfg.name || 'Creality Filament System'}</div>
          <ha-icon icon="mdi:printer-3d"></ha-icon>
        </div>
        <div class="boxes-container" id="boxes"></div>
      </ha-card>
    `;
  }

  _update() {
    if (!this._root || !this._hass) return;

    const boxesContainer = this._root.getElementById("boxes");
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
    const fmtWithUnit = (eid) => fmtState(gObj(eid));

    const hasExplicitMapping = Object.keys(this._cfg || {}).some((key) => (key.startsWith("box") || key.startsWith("external_")) && this._cfg[key]);
    const boxes = {};
    const external = {
      filament: this._cfg.external_filament,
      color: this._cfg.external_color,
      percent: this._cfg.external_percent,
    };
    const hasExternal = external.filament || external.color || external.percent;

    if (hasExplicitMapping) {
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
          const percentText = fmtState(percentObj);

          slots[slotId] = {
            id: slotId,
            boxId,
            entity_id: filamentEid || colorEid || percentEid,
            name,
            type,
            selected,
            color,
            percentText,
          };
        }

        if (tempEid || humidityEid || slots.some((slot) => slot)) {
          boxes[boxId] = {
            id: boxId,
            temp: fmtWithUnit(tempEid),
            humidity: fmtWithUnit(humidityEid),
            slots,
          };
        }
      }
    }

    const boxValues = Object.values(boxes);
    if (boxValues.length === 0 && !hasExternal) {
      boxesContainer.innerHTML = `<div style="text-align:center; color:var(--secondary-text-color)">No CFS data available</div>`;
      return;
    }

    let html = "";
    if (hasExternal) {
      const filamentObj = gObj(external.filament);
      const colorObj = gObj(external.color);
      const percentObj = gObj(external.percent);
      const name = filamentObj?.state;
      const type = filamentObj?.attributes?.type;
      const selected = filamentObj?.attributes?.selected;
      const rawColor = colorObj?.state || filamentObj?.attributes?.color_hex;
      const color = KCFSCard._sanitizeColor(rawColor);
      const percentText = fmtState(percentObj);
      const externalSlot = {
        id: 0,
        boxId: 0,
        entity_id: external.filament || external.color || external.percent,
        name,
        type,
        selected,
        color,
        percentText,
        hideActions: true,
      };
      html += `
        <div class="box">
          <div class="box-header">
            <span>External Filament</span>
            <span></span>
          </div>
          <div class="slots-grid">
            ${this._renderSlot(externalSlot)}
          </div>
        </div>
      `;
    }
    boxValues.forEach((box) => {
      const tempStr = box.temp || "—";
      const humidityStr = box.humidity || "—";
      const headerParts = [];
      if (tempStr !== "—") headerParts.push(tempStr);
      if (humidityStr !== "—") headerParts.push(humidityStr);
      const headerText = headerParts.length ? headerParts.join(" | ") : "—";
      html += `
        <div class="box">
          <div class="box-header">
            <span>Box ${box.id + 1}</span>
            <span>${headerText}</span>
          </div>
          <div class="slots-grid">
            ${box.slots.map((slot) => this._renderSlot(slot)).join("")}
          </div>
        </div>
      `;
    });

    boxesContainer.innerHTML = html;

    // Attach events
    this._root.querySelectorAll('.slot').forEach(el => {
      el.onclick = () => {
        const eid = el.dataset.eid;
        this.dispatchEvent(new CustomEvent("hass-more-info", {
          detail: { entityId: eid },
          bubbles: true,
          composed: true,
        }));
      };
    });

  }

  _renderSlot(slot) {
    if (!slot) return '<div class="slot empty"></div>';
    
    const isSelected = slot.selected === 1 || slot.selected === true;
    const color = slot.color || '#cccccc';
    const safeName = slot.name && !["unknown", "unavailable"].includes(String(slot.name).toLowerCase()) ? slot.name : "---";
    const safeType = slot.type && !["unknown", "unavailable"].includes(String(slot.type).toLowerCase()) ? slot.type : "---";
    const percentState = slot.percentText;
    const percentValid = percentState && !["unknown", "unavailable", "—"].includes(String(percentState).toLowerCase());
    const percentText = percentValid ? ` - ${percentState}` : "";
    
    return `
      <div class="slot ${isSelected ? 'selected' : ''}" data-eid="${slot.entity_id}">
        <div class="spool-preview" style="background-color: ${color}"></div>
        <div class="slot-info"><b>${safeType}</b></div>
        <div class="slot-info">${safeName === 'N/A' ? 'No Filament' : safeName}${percentText}</div>
      </div>
    `;
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
    this._cfg = { ...KCFSCard.getStubConfig(), ...config };
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
      { name: "compact_view", selector: { boolean: {} } },
    ];
    themeForm.computeLabel = (s) => ({
      compact_view: "Compact View (Hide Actions)",
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
