# Release Notes: Version 0.7.0

## Overview
Version 0.7.0 brings significant improvements to connection reliability, camera integration, and user interface customization. This release focuses on robustness for network changes, better WebRTC camera support via `go2rtc-client`, and enhanced control over card appearance and functionality.

---

## ✨ Major Features

### 1. **Robust Network Management with MAC-Based Discovery**
- Caches printer MAC during Zeroconf discovery to automatically detect and handle IP changes from DHCP reassignments or network migrations
- Automatically updates existing entries when MAC is detected at a new IP (no duplicates)
- Host/IP now editable from integration options without recreating entry

### 2. **Enhanced WebRTC Camera via go2rtc-client**
- Uses official `go2rtc-client` Python library for robust stream configuration
- Validates `go2rtc` server version on startup
- Graceful fallbacks when `go2rtc` is unavailable

### 3. **Intelligent Power-Off Detection**
- Pauses connection attempts when printer power is OFF (instead of continuous retry)
- Auto-resets retry backoff when power returns for faster reconnection
- mDNS rediscovery respects power state to avoid unnecessary queries
- Resets backoff if connection stays stable for 5+ seconds before disconnect

### 4. **Card Customization & Enhanced Controls**
- New custom button targeting any entity type (buttons, scripts, automations, switches, lights)
- All buttons (pause, resume, stop, light, power, custom) support custom MDI icons
- Smart domain handling for different entity types
- Option to hide chamber temperature pill for printers without chamber sensors

### 5. **Expanded Entity Domain Support**
- Power & light controls now support `input_boolean` and `light` domains (in addition to `switch`)

---

## 🐛 Bug Fixes & Improvements

- Fixed `UnboundLocalError` in WebSocket reconnection timing logic
- Improved `go2rtc` client error handling with descriptive messages
- Refactored card event handling using event delegation
- Enhanced Zeroconf flow with MAC address extraction and validation

---

## 📦 Dependencies

**New Requirement:**
- `go2rtc-client>=0.1.0` - Official Python client for `go2rtc` WebRTC streaming

**Existing:**
- `websockets>=10.4` (unchanged)

**Component:**
- `go2rtc` component (ensures proper initialization order)

---

## 🔧 Configuration Changes

### Integration Options
- **Host/IP**: Edit printer IP without recreating entry
- **Hide Chamber Temperature**: Toggle chamber temp pill visibility on card

### Card Configuration Keys

| Key | Purpose | Default |
|-----|---------|---------|
| `hide_chamber_temp` | Hide chamber temperature pill | N/A |
| `pause_btn_icon` | Custom pause button icon | `mdi:pause` |
| `resume_btn_icon` | Custom resume button icon | `mdi:play` |
| `stop_btn_icon` | Custom stop button icon | `mdi:stop` |
| `light_btn_icon` | Custom light button icon | `mdi:lightbulb` |
| `power_btn_icon` | Custom power button icon | `mdi:power` |
| `custom_btn` | Entity ID for custom button | (none) |
| `custom_btn_icon` | Custom button icon | `mdi:gesture-tap` |
| `custom_btn_hidden` | Hide the custom button | false |

---

## 📋 Upgrade Notes

**No Breaking Changes** — Full backward compatibility with existing configurations.

### For All Users
1. Restart Home Assistant after updating through HACS
2. Hard refresh browser (`Ctrl+F5`) to clear cached card assets
3. Existing setup will work unchanged; no configuration required

### Optional Enhancements
- **Dynamic IPs**: MAC caching automatically handles DHCP reassignments
- **Power Awareness**: Bind a power switch entity to enable smart connection pausing
- **Custom Buttons**: Configure custom button icons and actions in card config

---

## 🔍 Technical Summary

**Key Changes:**
- Coordinator now tracks power state and passes callback to WebSocket client for intelligent connection pausing
- MAC address extraction added to Zeroconf discovery flow
- WebRTC camera refactored to use `go2rtc-client` library with improved initialization
- Card event handling refactored to use event delegation for better maintainability
- Config flow enhanced with IP update from options, MAC-based entry matching

---

## 📝 Support Notes

### Known Limitations
- MAC address extraction depends on printer firmware; some models may not expose this
- WebRTC camera requires `go2rtc` component enabled (default in HA 2024.11+)
- mDNS logging only in debug mode to avoid log spam

### Debugging
- Enable debug logging: `logger: custom_components.ha_creality_ws: debug`
- Verify Zeroconf discovery in Integrations page

---
