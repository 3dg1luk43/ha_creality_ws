
const CARD_TAG = "k-cfs-card";
const EDITOR_TAG = "k-cfs-card-editor";

const mdi = (name) => `mdi:${name}`;

class KCFSCard extends HTMLElement {
  static getStubConfig() {
    return {
      name: "CFS",
      printer_host: "",
      compact_view: false,
    };
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
        background: #ccc;
      }
      .slot-info {
        font-size: 0.8em;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .slot-actions {
        display: flex;
        gap: 4px;
        margin-top: 4px;
      }
      .slot-actions button {
        flex: 1;
        font-size: 0.7em;
        padding: 4px;
        cursor: pointer;
        border: 1px solid var(--divider-color);
        border-radius: 4px;
        background: var(--secondary-background-color);
        color: var(--primary-text-color);
      }
      .slot-actions button:hover {
        background: rgba(var(--rgb-primary-text-color), 0.1);
      }
      
      .compact .box-header { margin-bottom: 4px; }
      .compact .slot { padding: 4px; gap: 2px; }
      .compact .slot-info { font-size: 0.7em; }
      .compact .slot-actions { display: none; }
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
    const printerHost = this._cfg.printer_host;
    
    // Find all CFS sensors for this printer
    const states = this._hass.states;
    const boxSensors = Object.keys(states).filter(eid => 
      eid.startsWith("sensor.") && 
      eid.includes("cfs_box_") && 
      (printerHost ? eid.includes(printerHost.replace(/\./g, '_')) : true)
    ).sort();

    if (boxSensors.length === 0) {
      boxesContainer.innerHTML = `<div style="text-align:center; color:var(--secondary-text-color)">No CFS data available</div>`;
      return;
    }

    // Group slots by box
    const boxes = {};
    boxSensors.forEach(eid => {
      const match = eid.match(/cfs_box_(\d+)/);
      if (match) {
        const boxId = parseInt(match[1]);
        if (!boxes[boxId]) boxes[boxId] = { id: boxId, slots: [] };
        
        const stateObj = states[eid];
        boxes[boxId].temp = stateObj.attributes.temperature;
        boxes[boxId].humidity = stateObj.attributes.humidity;
      }
    });

    const slotSensors = Object.keys(states).filter(eid => 
      eid.startsWith("sensor.") && 
      eid.includes("cfs_slot_") && 
      (printerHost ? eid.includes(printerHost.replace(/\./g, '_')) : true)
    ).sort();

    slotSensors.forEach(eid => {
      const match = eid.match(/cfs_slot_(\d+)_(\d+)/);
      if (match) {
        const boxId = parseInt(match[1]);
        const slotId = parseInt(match[2]);
        if (boxes[boxId]) {
          const stateObj = states[eid];
          boxes[boxId].slots[slotId] = {
            id: slotId,
            boxId: boxId,
            name: stateObj.state,
            type: stateObj.attributes.type,
            color: stateObj.attributes.color,
            percent: stateObj.attributes.percent,
            selected: stateObj.attributes.selected,
            entity_id: eid
          };
        }
      }
    });

    let html = '';
    Object.values(boxes).forEach(box => {
      html += `
        <div class="box">
          <div class="box-header">
            <span>Box ${box.id}</span>
            <span>
              ${box.temp !== undefined ? `${box.temp}°C` : ''} 
              ${box.humidity !== undefined ? `| ${box.humidity}%` : ''}
            </span>
          </div>
          <div class="slots-grid">
            ${box.slots.map(slot => this._renderSlot(slot)).join('')}
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

    this._root.querySelectorAll('.load-btn').forEach(el => {
      el.onclick = (e) => {
        e.stopPropagation();
        this._callService('cfs_load', el.dataset.box, el.dataset.slot);
      };
    });

    this._root.querySelectorAll('.unload-btn').forEach(el => {
      el.onclick = (e) => {
        e.stopPropagation();
        this._callService('cfs_unload', el.dataset.box, el.dataset.slot);
      };
    });
  }

  _renderSlot(slot) {
    if (!slot) return '<div class="slot empty"></div>';
    
    const isSelected = slot.selected === 1 || slot.selected === true;
    const color = slot.color || '#cccccc';
    
    return `
      <div class="slot ${isSelected ? 'selected' : ''}" data-eid="${slot.entity_id}">
        <div class="spool-preview" style="background-color: ${color}"></div>
        <div class="slot-info"><b>${slot.type || '---'}</b></div>
        <div class="slot-info">${slot.name === 'N/A' ? 'No Filament' : slot.name}</div>
        <div class="slot-actions">
          <button class="load-btn" data-box="${slot.boxId}" data-slot="${slot.id}">Load</button>
          <button class="unload-btn" data-box="${slot.boxId}" data-slot="${slot.id}">Unload</button>
        </div>
      </div>
    `;
  }

  async _callService(service, box, slot) {
    if (!this._hass) return;
    try {
      await this._hass.callService("ha_creality_ws", service, {
        box_id: parseInt(box),
        slot_id: parseInt(slot)
      });
    } catch (err) {
      console.error("Error calling service:", err);
    }
  }

  getCardSize() {
    return 3;
  }
}

customElements.define(CARD_TAG, KCFSCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "k-cfs-card",
  name: "Creality CFS Card",
  preview: true,
  description: "A card to control the Creality Filament System (CFS)"
});
