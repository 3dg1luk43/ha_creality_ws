![Installs](https://img.shields.io/badge/dynamic/json?color=41BDF5&logo=home-assistant&label=Installations&cacheSeconds=15600&url=https://analytics.home-assistant.io/custom_integrations.json&query=$.ha_creality_ws.total)
![Latest](https://img.shields.io/github/v/release/3dg1luk43/ha_creality_ws)
![Hassfest](https://img.shields.io/github/actions/workflow/status/3dg1luk43/ha_creality_ws/hassfest.yml?label=hassfest)
![HACS](https://img.shields.io/github/actions/workflow/status/3dg1luk43/ha_creality_ws/validate.yaml?label=HACS)
[![](https://img.shields.io/static/v1?label=Sponsor&message=%E2%9D%A4&logo=GitHub&color=%23fe8e86)](https://ko-fi.com/3dg1luk43)

# Creality WebSocket Integration for Home Assistant

This custom [Home Assistant](https://www.home-assistant.io/) integration provides **native, low-latency WebSocket control and telemetry** for Creality K‑series and compatible 3D printers. It exposes live state, sensors, controls, a camera stream, and a printer‑local **Current Print Preview** image. A **standalone Lovelace card** (no external card dependencies) is bundled.

---

## Features

* **Direct WebSocket** connection (local, no cloud).
* **Push updates**; no polling.
* **States:** `idle`, `printing`, `paused`, `stopped`, `completed`, `error`, `self-testing`.
* **Optional power switch binding** to a `switch` entity for accurate "Off" handling.
* **Entities:** status, progress, time left, temperatures (nozzle/bed/chamber), current layer/total layers, etc.
* **Image (print preview):** shows the current model image for all supported printers when available; falls back to a tiny placeholder when not applicable.
* **Controls:** pause, resume, stop, light toggle, fan speeds (model / case / side), temperature targets.
* **Camera:** auto-detects stream type by model (MJPEG or WebRTC).
* **Lovelace card**: dependency-free, uses HA fonts, progress ring, contextual chips, telemetry pills.
* **Style Editor**: Built-in theme customization for every card element, with a device picker
  that wires up all the entities in one go.
* **Live print notifications** to any number of phones: a countdown timer and progress bar on the iOS Lock Screen and the Android status bar, the G-code preview as the icon, a camera snapshot when a print ends, and optional Pause/Resume/Stop buttons. Every message can be [written in your own words](#custom-notification-text). See [Notifications](#notifications).

---

## Installation

> **Requires Home Assistant 2026.7.0 or newer.** The live print notifications
> depend on the companion app's Live Activity support, which arrived in that
> release. HACS will not offer this version to an older core, and installing it
> by hand anyway makes setup fail with a message saying so. If you cannot update
> Home Assistant, stay on 0.9.7.

### HACS (recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=3dg1luk43&repository=ha_creality_ws&category=integration)

- This integration is available in HACS by default. Click the badge above to open it directly in HACS.
- If needed, you can add it manually as a custom repository: https://github.com/3dg1luk43/ha_creality_ws
- After install, **Restart** Home Assistant.

### Installing Pre-release Builds

To install a specific pre-release version via HACS:

1. Open **HACS**.
2. Navigate to the **Creality WebSocket Integration**.
3. Click the three-dot menu (⋮) at the top right and select **Redownload**.
4. In the version dropdown, toggle **Show beta versions** if needed, then select the latest pre-release version.
5. Click **Download**.
6. **Restart** Home Assistant.
7. Hard refresh your browser (Ctrl+F5) to ensure the frontend card updates.

### Manual

1. Copy `custom_components/ha_creality_ws` into `<config>/custom_components/`.
2. **Restart** Home Assistant.

## Configuration

The **Configure** dialog is a menu of pages -- Camera, Notifications, Power switch, Connection & performance. **Pressing Submit on a page saves that page** and applies it. There is nothing to confirm afterwards and no final "save" step: close the dialog whenever you are finished.

Notification settings are applied **without reloading the integration**: the WebSocket stays up, no entity goes unavailable and the camera stream is not restarted, so you can tune notification text during a print. A card already on a phone is rebuilt from the new settings on the very next telemetry frame, and a phone you removed from the targets has its card taken away. Every other page still reloads the integration, because a new IP address or camera mode has to.

### 1) Add the integration (UI)

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=ha_creality_ws)

- Or go to **Settings → Devices & Services**
- Click **Add Integration**
- Search for **Creality WebSocket Integration**
- Enter the printer hostname/IP and a friendly name
- Zeroconf is supported; devices may auto-discover via mDNS

### 2) Optional: bind a power switch

If your printer power is controlled by a smart plug/switch, enable power detection so the integration can accurately reflect "Off" status and zero sensors when power is disabled.

* **Settings → Devices & Services →** your printer **→ Configure**
* Enable the **Power Switch** toggle
* Select your smart plug/switch entity (supports `switch`, `input_boolean`, and `light` domains)
* Submit

**Behavior:**
- When **enabled**: The integration monitors your switch entity. If it's OFF, the printer status shows as "Off" and all sensors zero out. Reconnection attempts use adaptive backoff (respects power state).
- When **disabled**: The integration doesn't monitor any switch. All sensors update normally based on WebSocket telemetry.
- **Safe defaults**: Existing users will auto-migrate on first load. If you previously had a power switch configured, it will be re-enabled automatically.

### 3) Optional: camera mode

If auto-detection doesn't choose your preferred stream, you can force it under the integration's Configure dialog:

- **Camera Mode**: 
  - `auto` (default) - Automatically detect based on printer model
  - `mjpeg` - Force direct MJPEG stream
  - `webrtc` - Force WebRTC streaming

Native WebRTC works out of the box on the installation types that ship go2rtc -- Home Assistant OS, Supervised and Container -- where the bundled binary in every supported core (2026.7+) is new enough for Creality's streams. **On a Home Assistant Core install there is no bundled binary**, so Home Assistant only manages go2rtc if you give it one (`go2rtc: url:` in `configuration.yaml`); until then use the go2rtc host/port fields in the options to point the integration at your own. Those fields also exist for pointing at a stand-alone go2rtc on any install, and for the RTSP port its stream pipeline uses.

> **If you point these at a go2rtc on another machine,** be aware that the RTSP hop Home Assistant uses to pull the stream is plain `rtsp://` -- go2rtc has no RTSPS output, so that traffic is unencrypted on your network. Left at the default it is loopback only (`127.0.0.1`) and never leaves the host. Only send it across a network you trust, or put it through a TLS-terminating proxy or tunnel.

### Dependencies

The integration automatically installs the following Python packages:
- `websockets>=10.4` - For WebSocket communication with printers
- `go2rtc-client>=0.1.0` - For WebRTC camera stream coordination

**Camera Dependencies:**
- **K1 family & Ender 3 V3 family cameras**: No additional dependencies required (MJPEG streaming)
- **K2 family cameras (WebRTC):**
  - Native WebRTC is available on every supported core that ships go2rtc (OS, Supervised, Container), since the 2026.7 minimum is already past the 2025.11 release that bundled a Creality-compatible go2rtc. A Core install has no bundled binary and needs one configured, as above.
  - Pointing the integration at a stand-alone **go2rtc >= 1.9.11** is still possible via the Options dialog (host/port). On the installation types that ship the binary it is no longer required; on a Core install it remains the way to get WebRTC, either through the Options dialog or by giving Home Assistant a `go2rtc: url:` to manage.

---

## Lovelace Card

This repository **bundles** standalone cards. The integration serves the card files directly from its own `www/` directory (at `/ha_creality_ws/k_printer_card.js` and `/ha_creality_ws/k_cfs_card.js`) and **auto-registers** the Lovelace resources **in storage mode**.

### Card screenshots

Below are example screenshots of the card interface states:

Idle

![Idle](img/k1c_idle.png)

Off

![Off](img/k1c_off.png)

Printing

![Printing](img/k1c_printing.png)

Processing

![Processing](img/k1c_processing.png)

Color picker

![Color picker](img/color-picker.png)

### Resource registration

* **Storage mode (default)**
  The integration registers the resource automatically, with a `?v=` token
  derived from the card file's own contents and the release version. It changes
  when a card changes and stays put otherwise, so an update always reaches the
  browser and an ordinary restart does not throw away a cached copy:

  ```
  /ha_creality_ws/k_printer_card.js   (type: module)
  /ha_creality_ws/k_cfs_card.js       (type: module)
  ```

  If you ever remove/re-add the integration or migrate dashboards, verify it under:
  **Settings → Dashboards → ⋮ → Resources**.

* **YAML mode**
  Add this to your configuration:

    ```yaml
  lovelace:
    resources:
      - url: /ha_creality_ws/k_printer_card.js
        type: module
  ```
  
  The card is served from the integration's own `www/` directory, not from `<config>/www/`.

### Forcing Storage mode (if you previously used YAML)

If you want to switch to storage mode explicitly:

```yaml
# configuration.yaml
lovelace:
  mode: storage
```

Restart HA after changing this.

### After an update

Restart Home Assistant. That is what re-registers the resource with its new
`?v=` token, and the browser fetches the new card on the next dashboard load.

If you still see the old card, it is cached under a URL that has not changed:

* Desktop: **Ctrl+F5** (Windows/Linux), **⌘+Shift+R** (macOS)
* Mobile app: **App Settings → Reload resources**, or force close and reopen.

Your card configuration is carried over automatically. Colors saved by earlier
versions of the Style Editor are normalized on load, and anything the color
picker cannot represent -- a `var(--your-color)` or a named color written by
hand in YAML -- is left exactly as you wrote it.

---

## Image: Current Print Preview

The integration adds an `image` entity named "Current Print Preview" for all supported printers:

- Tries the printer‑local PNG at `/downloads/original/current_print_image.png` (HTTPS first, then HTTP), with short timeouts.
- Only fetches when it makes sense (e.g., self‑testing or printing with a file name); otherwise shows a small placeholder PNG.
- Caches the last successful image to avoid flicker; never calls any cloud service.
- Records each attempted printer‑local HTTP(S) URL in diagnostics to aid support.

Tip: You can use the built‑in Image card or any card that supports `image` entities to display it on dashboards.

---

## Notifications

Configure under **Settings → Devices & Services → Creality → Configure → Notifications**. It is one page: the targets at the top, then three groups -- **What to notify me about**, **Buttons, pictures and tapping**, and **Custom text**. The last two start folded away unless you have changed something in them.

### Targets

Pick **one or more** notify targets. Both kinds work:

- `notify.mobile_app_*` -- the Home Assistant companion app. Only these can show the live print card, the G‑code preview, action buttons or a tap target.
- Anything else (`notify.signal_messenger`, `notify.persistent_notification`, a notify *entity*, …) -- receives the **message and title only**.

That split is deliberate rather than a limitation: several notify platforms reject payload keys they do not recognise and fail the whole call, `notify.send_message` has no payload field at all, and the image URLs below only authenticate from inside the companion app.

If you previously set the single **Notification Device**, it is migrated automatically the first time the integration loads -- there is nothing to do. The old setting is left on disk, so downgrading keeps working.

### The live print card

Enable **Live print card**. During a print you get one card that updates in place, on the iOS Lock Screen and in the Dynamic Island, and in the Android status bar and notification shade:

- a **countdown timer** to the estimated finish, which ticks on the phone itself
- a **progress bar**, and the percentage, layer and remaining time as text
- the **G‑code preview** as the notification icon
- **Pause / Resume / Stop** buttons, if you enable them

The text is the numbers and not the file name: the title is already the printer, iOS carries the job name as the activity's subtitle, and a long `.gcode` name pushed the numbers off the end of an Android status bar. Write your own text if you want it back -- see [Custom notification text](#custom-notification-text).

It ends by itself when the print finishes, and is replaced by a completion notification carrying a **camera snapshot** of the bed. That one is a plain confirmation: no progress bar and no countdown, because there is nothing left to track. A print that is cancelled or aborted gets its own notification saying where it stopped, rather than the card simply vanishing.

**Notify when a print ends** covers both outcomes -- finished *and* stopped. Being told a print completed is only half the story if you are not also told when it didn't.

A stop is reported whoever performed it: the printer's own screen, the Creality app, this integration's Stop button or the button on the notification itself. None of those announces itself in the printer's telemetry -- what arrives is an ordinary frame in the same state the printer reports while warming up -- so the integration watches for the transition instead, and the notification names the file and the percentage the print had reached. A stop the printer states outright is announced immediately; the ambiguous form is held for about 15 seconds first, so a single odd frame mid-print cannot tell you your print was stopped when it wasn't.

**Requirements:** Home Assistant **2026.7.0 or newer** (this is the integration's minimum), plus **iOS 17.2+** or **Android 16+**. On older phones the same notification still arrives and still replaces itself in place -- you lose the timer and the bar, not the notification.

**Update rate.** A state change (start, pause, resume, finish) is pushed immediately, because a deliberate user action has to show up at once. Everything else is rate limited: the card refreshes on a five-minute clock, a whole percent of progress can force an earlier refresh, and nothing is sent less than 30 seconds apart. That is not a compromise: the push relay allows about 500 notifications per device per day, iOS throttles frequent Live Activity updates and eventually drops them, and the countdown needs no pushes at all because it runs on the phone.

**Long prints.** iOS ends any Live Activity after **8 hours** -- an Apple limit, not something an app can extend. Past that the card keeps updating as an ordinary notification -- you lose the ticking timer, not the remaining time, which is written into the text either way -- and you still get the completion notification. Android has no such limit.

### Tapping the notification

| | Default | With a dashboard path set |
|---|---|---|
| **Android** | opens the printer camera's more‑info dialog, with the live feed | opens that dashboard view |
| **iOS** | opens the app | opens that dashboard view |

Set **Dashboard path to open on tap** to something like `/lovelace/printer`. It is the only way to make an iOS tap go anywhere specific, because the `entityId:` deep link Android uses is Android‑only.

### Pictures

- **G‑code preview** -- used as the notification icon. Skipped when the printer has no preview to serve, rather than sending the placeholder, which renders as an empty grey box.
- **Camera snapshot** -- attached when a print ends or fails. Skipped on printers whose camera cannot produce a still image at all: K2‑family cameras using direct WebRTC signalling have no snapshot endpoint, so there is nothing to attach.

### Buttons

**Show Pause/Resume/Stop buttons** is off by default. Pause and Resume go through exactly the same path as the corresponding button entities. **Stop** is marked destructive and requires device authentication, so a mis‑tap on a lock screen cannot end a long print.

### Custom notification text

The **Custom text** group at the bottom of the Notifications page lets you write the wording yourself, for any of the six notifications the integration composes: the live card, print finished, print stopped, finishing soon, printer error and filament runout. Leave a field empty -- or clear it again -- to keep the built-in, translated text.

Placeholders are wrapped in single braces:

| Placeholder | Value |
|---|---|
| `{device}` | the printer's name, the same one used as the notification title |
| `{filename}` | the print file, without its directory |
| `{progress}` | percent complete, a whole number |
| `{layer}`, `{total_layers}` | the current layer and the job's total |
| `{eta}` | time remaining, e.g. `1h 04m` |
| `{elapsed}` | how long the job has been running |
| `{filament}` | filament used so far, in metres |
| `{nozzle}`, `{bed}` | current temperatures, in whole degrees |
| `{state}` | `printing`, `paused`, `stopped`, ... |
| `{error_code}`, `{error_key}` | the printer's error numbers |
| `{minutes}` | minutes left |

**Not every notification can fill every one of them**, so each field lists the ones it accepts, and anything else is refused when you submit the page. The last two are the obvious cases -- only the error notification knows an error code, only the finishing-soon reminder knows how many minutes are left -- but the two that matter in practice are:

- **Print finished** has no `{eta}`: the print is over, so there is no time remaining to report.
- **Print stopped** has only `{device}`, `{filename}`, `{progress}`, `{nozzle}` and `{bed}`. A stop is not something the printer announces, and by the frame it becomes visible the progress, the job clock, the layer and the filament length have all been reset to zero. The file name and the percentage are the ones from the last moment the job was seen running; the rest are not offered rather than reported as zero.

Every value comes from the printer's own telemetry, read from the single frame that triggered the notification. There is no placeholder for a value the printer does not report -- filament *weight*, for one, which would take a density it never sends.

**Values that are not always there.** The printer reports no estimate in the first minute of a job, no layer count on some firmwares, and no filament length on a job that never started. A placeholder it has not reported renders as nothing, which on its own leaves `3DBenchy.gcode  left`. Wrap the optional part in **square brackets** and the whole bracketed section disappears while any placeholder inside it is unknown:

```
{filename} -- {progress}%[ -- {eta} left][ -- layer {layer}/{total_layers}]
```

- `3DBenchy.gcode -- 42% -- 1h 04m left -- layer 126/300` while everything is known
- `3DBenchy.gcode -- 42%` in the first minute, with no dangling separator

Brackets that contain no placeholder are your own text and stay as you typed them, so `[PRINTER] done` renders literally. A `0` is a value, not a missing one: `[{progress}%]` renders `0%` rather than vanishing.

The brackets around an optional part are syntax and are not shown. For a bracket you want to *keep*, double it -- `{error_key} [[{error_code}]]` renders `1 [7]`, while `{error_key} [{error_code}]` renders `1 7` because there the brackets mean "optional".

Anything not recognised is refused when you submit the page, so a typo like `{filament_grams}` is reported there rather than quietly delivering the built-in text forever. If one gets in another way (a hand-edited `.storage`), the notification still arrives -- with the built-in wording, and a warning in the log.

**The title is always the printer's name.** Android fixes a live card's title when the activity starts and cannot change it afterwards, so job detail belongs in the text. For full control over title, text and delivery, use the bus events below.

### Language

Notification text is translated -- messages, status labels, button captions and the Android notification-channel names all come from the integration's translation files.

It follows the **server** language (**Settings → System → General**), not each person's profile language. That is not an oversight: an integration is never told which user a `notify` call is for, so the server language is the only one available to it. For a household that needs different languages per person, use the bus events below.

### Building your own notifications

The integration fires plain bus events whether or not you configure a notify target:

| Event | When |
|---|---|
| `ha_creality_ws_print_started` | a new job appears |
| `ha_creality_ws_print_finished` | progress reaches 100% |
| `ha_creality_ws_print_stopped` | a job ends without finishing -- cancelled, or aborted by the printer |
| `ha_creality_ws_print_error` | the printer reports a new error code |

Each carries `entry_id`, `host`, `device_name`, `filename`, `progress`, `layer`, `total_layers`, `left_seconds` and `err_code`.

This is the supported way to get notification text in a language other than your server's. An integration is never told *which user* a notification is for, so any message it composes itself can only follow the Home Assistant server language -- build your own text in an automation instead.

---

## Card Usage

The card's element tag is **`custom:k-printer-card`**.

The quickest way to add one is the visual editor: pick your printer under
**Printer device** on the **Entities** tab and every field below it fills in
from that device. Only fields you left empty are filled, so entities you chose
yourself are kept; **Fill all fields from device** replaces the lot. The power
switch is the one thing never filled in, since it is your own smart plug rather
than something the integration provides.

Renaming an entity later does not break the picker: it matches on the role Home
Assistant records for each entity, not on the entity id.

Add via UI (Manual card) or YAML:

```yaml
type: custom:k-printer-card
name: "K1C Printer"
camera: camera.k1c_printer_camera
status: sensor.k1c_print_status
progress: sensor.k1c_print_progress
time_left: sensor.k1c_print_time_left
nozzle: sensor.k1c_nozzle_temperature
bed: sensor.k1c_bed_temperature
box: sensor.k1c_box_temperature
layer: sensor.k1c_working_layer
total_layers: sensor.k1c_total_layers
light: switch.k1c_light
power: switch.printer_power  # Optional
pause_btn: button.k1c_pause_print
resume_btn: button.k1c_resume_print
stop_btn: button.k1c_stop_print
```

**Behavior:**

* Header icon color + conic progress ring reflect state and progress.
* Chips:

  * **Pause** shown when `printing|resuming|pausing`.
  * **Resume** shown when `paused`.
  * **Stop** shown when `printing|paused|self-testing`.
  * **Light** toggles the configured `switch`/`light` entity; shows/hides based on Power state and printer status.
  * **Power** (optional) offers a snappy, short-lived optimistic toggle; pinned to the far right when configured.

**Configuration Notes:**
- `power` is optional; omit it to hide the power chip.
- Light chip visibility automatically reacts to power state and printer status without reload.
- Light strictly mirrors entity state (doesn't use optimistic overrides) to avoid desync.
- Tapping the header opens **more-info** for `camera` (fallbacks: `status`, `progress`).

---

## Fans

The three fans the printer exposes are available as full `fan` entities, so they can
be turned on/off and set to any speed, not just monitored:

- `fan.<host>_model_fan` (part-cooling fan)
- `fan.<host>_case_fan` (case / chamber exhaust fan)
- `fan.<host>_side_fan` (auxiliary / side fan)

Speed is set as a percentage and sent to the printer as `M106 P<channel> S<0-255>`
over the same WebSocket, so no extra configuration is needed. The printer's own
temperature-driven fan control keeps running; a manual command overrides it until
the printer decides otherwise.

The legacy `number.*_fan` entities are **gone** as of 0.9.8. If you are upgrading
from an older release and had automations pointing at them, move those to the
`fan` entities above.

Example: force the case fan to full when the chamber gets too hot.

```yaml
automation:
  - alias: Chamber too hot -> case fan to max
    triggers:
      - trigger: numeric_state
        entity_id: sensor.k2_chamber_temperature
        above: 55
    actions:
      - action: fan.set_percentage
        target:
          entity_id: fan.k2_case_fan
        data:
          percentage: 100
```

---

## Filament Estimates

Alongside `used_material_length`, which the printer streams as it prints, the
integration asks the printer what the slicer estimated for the file it is
running and exposes that as three sensors:

| Entity | Unit | Meaning |
| --- | --- | --- |
| `sensor.<host>_expected_material_length` | cm | Filament the slicer expects the whole job to use |
| `sensor.<host>_expected_material_weight` | g | Weight the slicer expects the whole job to use |
| `sensor.<host>_filament_consumption` | % | Used divided by expected |

The percentage is deliberately not the same thing as print progress -- filament
use is not linear in time or in layers -- and it is not capped at 100%, because
a job running past its estimate is worth seeing rather than hiding.

The expected length and weight come from the slicer, via the printer; nothing
here is derived from an assumed filament diameter or density.

**Attributes** on the expected-length sensor: `material`, `color`, `slicer`,
`estimated_time_s` and `gcode_file`, each passed through exactly as the printer
words it.

Some caveats, all of them the printer's behaviour rather than choices made here:

- The metadata is fetched when the running file changes, never polled. The
  printer has no way to be asked about a single file -- the reply describes
  every G-code file it holds, which is ~150 KiB for a couple of hundred files.
- Firmware that does not answer the request leaves these three sensors
  uncreated rather than permanently unknown.
- **Expected weight is blank for files the printer did not slice itself.**
  Creality Print output and the sample models shipped on the printer arrive
  with a length but no weight, so the weight sensor is unknown for those while
  the length sensor still works.
- **Multi-material jobs are not broken down per filament.** A CFS job packs
  several values into these fields, and this has not been verified against CFS
  hardware, so the weight sensor reports unknown rather than guessing which
  number is the total. See [issue #122](https://github.com/3dg1luk43/ha_creality_ws/issues/122).

---

## CFS (Creality Filament System)

If your printer reports CFS data, the integration creates sensors for each CFS box and slot. It also exposes a dedicated set of sensors for the **external filament** (single slot).

### CFS Sensors

**Box sensors (type 0 / CFS):**

- `sensor.<host>_cfs_box_<box>_temp`
- `sensor.<host>_cfs_box_<box>_humidity`

**Slot sensors (type 0 / CFS):**

- `sensor.<host>_cfs_box_<box>_slot_<slot>_filament`
- `sensor.<host>_cfs_box_<box>_slot_<slot>_color`
- `sensor.<host>_cfs_box_<box>_slot_<slot>_percent`

**External filament (type 1 / single slot):**

- `sensor.<host>_cfs_external_filament`
- `sensor.<host>_cfs_external_color`
- `sensor.<host>_cfs_external_percent`

**Slot attributes** (on both the box-slot and external filament sensors):

| Attribute | Meaning |
| --- | --- |
| `vendor`, `type`, `name` | As reported by the printer / RFID tag |
| `color_hex` | Colour normalised to `#rrggbb` |
| `color_hex_raw` | The printer's original value, before normalisation |
| `rfid` | The printer's material id, exactly as reported |
| `spool_key` | Derived id, stable per material **and** colour |
| `state`, `selected` | Slot state and whether the printer is using it |
| `box_id`, `slot_id` | The ids the **printer** uses for this slot |
| `min_temp`, `max_temp` | Printing temperature range, °C (`null` if the printer omits them) |
| `pressure` | Pressure advance (`null` if the printer omits it) |

Creality RFID tags store the colour as *seven* hex characters: a padding character
followed by the real `RRGGBB`. `color_hex` therefore keeps the **last** six digits,
so the tag's `0ffffff` becomes `#ffffff` -- the `#` is added by the normalisation,
not reported by the printer. `color_hex_raw` keeps the printer's value verbatim.

`rfid` is a material/filament id rather than a tag serial, so two spools of the same
material and vendor share it even when their colours differ. `spool_key` combines it
with the normalised colour to tell those apart, which is what external trackers such
as spoolman-sync need. It is a *derived* key: the telemetry carries no per-tag serial,
so two genuinely identical spools still produce the same key.

`box_id` and `slot_id` are the printer's own ids, which is what `set_cfs_material`
needs to address a slot. Not every printer reports `min_temp`/`max_temp`/`pressure` on
every slot -- CFS box slots often omit them where the external slot has them -- so treat
`null` as "unknown", not as zero.

### CFS Card

The CFS card is a native UI card with a visual editor. It renders one tile per slot and a dedicated tile for the external filament.

- Card type: **Creality CFS Card**
- Element tag: **`custom:k-cfs-card`**
- Configuration is **entity mapping only** via the visual editor (no YAML flow required)

**Editor fields:**

- **Display Mode**: `Full`, `Compact` or `Box (visual)`
- **External Filament**: map `external_filament`, `external_color`, `external_percent`
- **Box 1–4**: map temperature/humidity and up to 4 slots per box

**Display modes:**

| Mode | Looks like |
| --- | --- |
| `full` | One ring tile per slot, with a unit selector when more than one box is mapped |
| `compact` | A single row of mini spools per box |
| `box` | A photo of the CFS unit with a spool overlay per bay. Requires a mapped four-slot box; anything else falls back to `full` |

Dashboards using the old `compact_view: true` are migrated automatically the first
time they load, and the legacy key is dropped when you next edit the card.

**Behavior:**

- Each slot tile opens **more-info** for the mapped filament entity
- Percent is rendered from the mapped percent sensor and formatted by Home Assistant
- Filament color uses the mapped color sensor (or color hex from filament attributes)
- **Active filament** shows a pulsing green dot indicator when currently selected by the printer
- **Humidity levels** are color-coded following Creality standards:
  - **Green** (< 40%): Ideal humidity range
  - **Orange** (40-59%): Attention required
  - **Red** (≥ 60%): Critical humidity level

#### Editing filament from the card

Each slot tile has an edit button (hover on a desktop; always visible on touch)
that opens a dialog for the material's type, name, vendor, colour, temperature
range and pressure advance. Saving calls
[`set_cfs_material`](#writing-filament-data-back-to-the-printer) and then asks the
printer to re-report, so the tile updates once the change has actually landed.

- The dialog states which **box and slot** it will write to. If the card cannot
  get that from the printer it says the target was inferred -- check it before saving.
- Editing is **disabled while the printer is busy** (printing, paused, processing
  or self-testing).
- A card whose entities come from **more than one printer** cannot edit, because
  there would be no way to tell which machine to write to.
- **Multi-colour spools** show their colour as read-only: the printer reports two
  values and a single colour cannot represent them.
- **Colour presets**: Creality's standard palette plus your own, saved in your
  browser rather than in the dashboard config. Right-click one of your own
  presets to delete it.

### CFS Card screenshots

Full view

![CFS Full](img/cfs_full.png)

Compact view

![CFS Compact](img/cfs_compact.png)

### Writing filament data back to the printer

`ha_creality_ws.set_cfs_material` writes filament metadata to one CFS slot. The
CFS card's edit dialog calls it, and it is also usable from automations and
Developer Tools.

```yaml
action: ha_creality_ws.set_cfs_material
data:
  device_id: <your printer>
  box_id: 1          # as the printer reports it -- see the box_id attribute
  slot_id: 2         # 0-based within the box
  type: PETG
  name: Hyper PETG
  vendor: Creality
  color: "#ff00aa"   # six hex digits
  min_temp: 230
  max_temp: 260
  pressure: 0.03
```

**Only the fields you supply are changed.** The printer merges the payload into
the slot it already holds, so anything you leave out keeps its current value.
That matters most for `rfid`: leaving it empty omits the field entirely, so the
existing tag association is preserved.

Other things worth knowing:

- `box_id` is the printer's own id, not a position on the card. Read it from the
  `box_id` attribute of any slot sensor -- it is usually `1` for the first CFS unit.
- `color` is a **six-digit hex string**, not an RGB list. Multi-colour spools
  cannot be written and must be left empty.
- `max_temp` must not be below `min_temp`; the service rejects the call rather
  than quietly adjusting it.
- The service **refuses to write while the printer is busy** (printing, paused,
  processing or self-testing).

> **A note on how well this is understood.** Creality does not document the
> `modifyMaterial` command. The payload shape comes from @buzato's work in
> [#75](https://github.com/3dg1luk43/ha_creality_ws/pull/75), tested against real
> CFS hardware there, and is verified here against the bundled printer simulator.
> Two details remain unconfirmed on real hardware: the printer *streams* colours
> as seven hex characters but appears to accept six on write, and the `rfid`
> field name is inferred from the telemetry rather than from any dump that
> confirms it. Each write logs both the outgoing payload and what the printer
> reports afterwards, so if something looks wrong please open an issue with that
> section of your debug log.

---

## Style Editor

The card includes a built-in **Style Editor** that allows you to customize the appearance of all card elements. Access it through the card editor's **Theme** tab.

### Features

* **Home Assistant color pickers**: The same control the rest of the UI uses
* **Opacity**: Every background has its own opacity slider
* **Live Preview**: See changes applied immediately to the card
* **Theme Persistence**: Customizations are saved and persist across integration updates
* **Automatic colors**: An Automatic switch per field, on by default, that lets the card work the color out for itself
* **Reset to Defaults**: One-click reset to restore original styling

### Customizable Elements

#### Action Button Colors
- **Pause Button**: Background (with opacity) and icon color
- **Resume Button**: Background (with opacity) and icon color
- **Stop Button**: Background (with opacity) and icon color

#### Toggle Button Colors

These buttons have an on and an off state, and each gets its own pair:

- **Light Button**: Background and icon, on and off
- **Power Button**: Background and icon, on and off
- **Custom Button**: Background and icon, on and off

#### Status Elements
- **Status Icon**: Color, automatic by default
- **Progress Ring**: Color, automatic by default
- **Status Background**: Background color and opacity, automatic by default

#### Telemetry Elements
- **Telemetry Icons**: Color, automatic by default
- **Telemetry Text**: Color, automatic by default

### Usage

1. **Open Card Editor**: Click the card's menu (⋮) → **Edit**
2. **Switch to Theme Tab**: Click the **Theme** tab in the editor
3. **Customize Colors**:
   - Click a color swatch to open Home Assistant's color picker
   - Drag the opacity slider next to a background to make it translucent
   - For the fields that have one, turn **Automatic** off to reveal a color
     picker, and back on to hand the choice back to the card
4. **Reset**: Use the **Reset to Defaults** button to restore original styling

Changes apply as you make them; there is no separate save step inside the theme
tab.

### Automatic Colors

Five fields are automatic unless you set them, and "automatic" means two
different things depending on the field:

| Field | What automatic does |
| --- | --- |
| Status Icon | Follows the print state: orange while paused, red on error, green when idle or finished |
| Progress Ring | Follows the print state, matching the status icon |
| Status Background | Blends into the card background |
| Telemetry Icons | Uses the Home Assistant theme's secondary text color |
| Telemetry Text | Uses the Home Assistant theme's primary text color |

The first two follow your printer, so they change while a job runs. The last
three follow your Home Assistant theme, so they adapt to light and dark mode on
their own. Each carries an **Automatic** switch that says which it is; turning
it off reveals a color picker, and turning it back on discards your color and
returns the field to the card.

### Persistence

Your customizations are automatically saved and will persist across:
- Home Assistant restarts
- Integration updates
- Dashboard reloads

The theme data is stored per card instance, so each card can have its own unique styling.

---

## Camera

The integration auto-detects the printer model and creates the appropriate camera entity. Camera support varies by model:

### Camera Support by Model

**MJPEG Cameras:**
- **K1/K1C/K1 Max**: Camera included. Works with all Home Assistant camera cards.
- **K1 SE**: Camera is optional accessory (gracefully handles when not present).
- **Ender 3 V3 family**: Camera is optional accessory (gracefully handles when not present).
- **Creality Hi**: Camera included. Works with all Home Assistant camera cards.
- **K1C (Classic)**: MJPEG camera included.

**WebRTC Cameras:**
- **K1C (2025)**: Native WebRTC streaming (auto-detected via telemetry).
- **K2 family** (K2, K2 Pro, K2 Plus): Native WebRTC streaming using Home Assistant's built-in go2rtc service.
  - Uses go2rtc for WebRTC streaming
  - Configures go2rtc to connect to the printer's WebRTC signaling endpoint
  - Forwards WebRTC offers/answers between Home Assistant frontend and go2rtc
  - Provides native WebRTC streaming without additional HACS integrations
  - Works with all standard Home Assistant camera cards that support WebRTC

### HLS / recording for go2rtc cameras

The frontend plays go2rtc cameras over WebRTC. Home Assistant's classic stream
pipeline (the `camera/stream` WebSocket command, HLS playback, `camera.record`,
`camera.play_stream` and casting) needs an ingestible URL instead, so the
integration points it at the RTSP endpoint of the same go2rtc instance.

That port is detected automatically: `18554` for Home Assistant's built-in
go2rtc, `8554` for a stand-alone one. If your go2rtc listens elsewhere, set
**go2rtc RTSP Port** under *Configure -> Camera* (0 keeps auto-detection).
"WebRTC direct" cameras bypass go2rtc entirely and therefore have no HLS source.

---

## Troubleshooting

* **"Configuration error" in picker or blank card**
  Hard refresh Lovelace. Verify the resource exists (see *Resource registration*). Ensure the element type is `custom:k-printer-card`.
* **Controls do nothing**
  Confirm the `pause_btn`, `resume_btn`, `stop_btn` entities exist and are `button.*`. The card calls `button.press`.
  Confirm the light entity domain is `switch` or `light`.
* **Sensors stay zeroed when printer is on**
  Enable the **Power Switch** in the integration's Configure dialog. If power is OFF but your printer is actually on, disable the power switch to stop the integration from zeroing sensors.
* **Connection takes too long when power is OFF**
  If you have **Power Switch** enabled and the printer is OFF, the integration waits 60 seconds between connection attempts (to save resources). This is intentional. Enable power detection so the integration can react immediately when power returns.
* **Resource missing in storage mode**
  Remove + re-add the integration or add the resource manually under **Dashboards → Resources** pointing to `/ha_creality_ws/k_printer_card.js`.
* **WebRTC camera not working**
  If K2 family cameras show fallback images instead of live video:
  1. Check go2rtc is there at all. On OS, Supervised and Container installs the bundled binary ships with every supported core (2026.7+); a Core install has none, so go2rtc is whatever you pointed Home Assistant or the integration at. (A stand-alone go2rtc must be **>= 1.9.11**.)
  2. To query it, note that **Home Assistant's managed go2rtc serves no HTTP API by default** -- it talks to it over a unix socket and only opens port `11984` when you set `go2rtc: debug_ui: true` in `configuration.yaml`, which also requires a `username` and `password`. So a `curl` of `11984` failing is the expected result, not a fault. With `debug_ui` on, the API answers **on the Home Assistant host itself** -- from your laptop, `localhost` is your laptop -- and your printer's stream should be listed under `/api/streams`. Turn it back off when you are done. A stand-alone go2rtc you run yourself has its API open already.
  3. Ensure the printer's WebRTC signaling endpoint is accessible from go2rtc.
  4. Verify the printer supports WebRTC (K2 family only).
  5. Check Home Assistant logs for WebRTC negotiation errors.
* **K2 camera shows no image**
  - Check that the printer's WebRTC endpoint is accessible
  - Verify the printer model is correctly detected (check logs for "detected K2 family printer")
  - Ensure Home Assistant's built-in go2rtc service is running
  - Check for WebRTC message format errors in logs
* **Manual camera mode not working**
  - Check logs for "user forced [mode] mode" messages
  - Verify the camera mode is set correctly in the integration's Configure dialog
  - Restart Home Assistant after changing camera mode settings
  - For `webrtc` mode: ensure Home Assistant's built-in go2rtc service is running

---

## Supported Models

### K1 Family
- **K1** - Box temperature sensor only (no control), light, MJPEG camera
- **K1C** - Box temperature sensor only (no control), light, MJPEG camera  
- **K1C (2025)** - Box temperature sensor only (no control), light. **Camera currently NOT supported** (dependency issues with go2rtc).  
- **K1 SE** - No box temperature, no light, optional MJPEG camera
- **K1 Max** - Box temperature sensor only (no control), light, MJPEG camera

### K2 Family
- **K2** - Box temperature sensor & control, light, WebRTC camera
- **K2 Pro** - Box temperature sensor & control, light, WebRTC camera
- **K2 Plus** - Box temperature sensor & control, light, WebRTC camera

### Ender 3 V3 Family
- **Ender 3 V3** - No box temperature, no light, optional MJPEG camera
- **Ender 3 V3 KE** - No box temperature, no light, optional MJPEG camera
- **Ender 3 V3 Plus** - No box temperature, no light, optional MJPEG camera

### Other Models
- **Creality Hi** - No box temperature, light, MJPEG camera

Other K-series models may work but are unverified.

---

## Model Detection Reliability

Printers can take a few telemetry frames before reporting their friendly `model`, `modelVersion`, and `hostname`. To avoid flakiness during onboarding the integration:

* Waits briefly for core fields (`model`, `modelVersion`, `hostname`) before caching device info.
* Falls back to board codes (`modelVersion` codes like F012/F021/F008/F001/F002/F005/F018) to resolve a stable model name when the friendly string is empty.
* Promotes capabilities heuristically if telemetry exposes fields early (e.g., `boxTemp`, `maxBoxTemp`, `targetBoxTemp`, `lightSw`).
* Ensures existing installations keep their cached capabilities and camera mode without regression.

Terminology note: UI labels may say "Chamber" instead of "Box" in some places; entity IDs and protocol fields remain stable for backward compatibility.

This logic reduces first-time setup races and prevents empty model names in the device registry.

---

## Status / Testing

Currently verified on:
- **Creality K1C** - Full functionality including box temperature and light controls (@me)
- **Creality Ender 3 V3 KE** - Full functionality (no box temperature or light controls, optional camera) (@Graf17)
- **Creality K2 Pro / K2 Plus** - WebRTC camera verified with Home Assistant 2025.11 release (native).
  - For older Home Assistant versions, verified with external go2rtc 1.9.11+. (@hawky358 & sammyke007)
- **Creality K1 (Base)** - Box temperature sensor only (no control), light, MJPEG camera
- **Creality Hi** - Fully working (forum user @TheDon)

---

## Diagnostic Service

The integration provides a diagnostic service to help with troubleshooting and understanding what data different printer models send via WebSocket.

### Usage

1. Go to **Developer Tools** → **Services**
2. Select service: `ha_creality_ws.diagnostic_dump`
3. Click **Call Service**
4. **Copy the diagnostic data** from the service response in the UI

The service will return the complete diagnostic data in the response that you can copy and paste directly. The data is also saved to a file in your Home Assistant config directory as a backup.

**Service Response includes:**

- **Complete WebSocket telemetry data** from all connected printers
- **Model detection results** showing how each printer is classified
- **Feature detection results** showing which features are enabled/disabled
- **Printer status information** (availability, power state, etc.)
- **Home Assistant and integration version information**
- **Printer‑local HTTP URLs accessed** (e.g., preview fetch attempts) for support diagnostics

### What's Included

The diagnostic file contains:
- All raw telemetry data received from the printer
- Model detection logic results (K1, K2, Ender 3 V3, etc.)
- Feature detection results (camera type, light, box temperature, etc.)
- Connection status and timing information
- Integration configuration details
 - Cache of local HTTP(S) URLs the integration accessed (no cloud)

### Sharing Diagnostic Data

The diagnostic data can be safely shared with developers for troubleshooting. It contains only telemetry data and configuration information - no sensitive personal data.

**How to share:**
1. Call the service as described above
2. Copy the `diagnostic_data` field from the service response
3. Paste it into a text file or share directly with developers

**Service Response Format:**
```json
{
  "diagnostic_data": "{...complete JSON data...}",
  "file_path": "/config/creality_diagnostic_20241220_143022.json",
  "data_size": 12345,
  "printers_count": 1
}
```

---

## License

GNU Affero General Public License v3.0. See `LICENSE`.

One bundled file is **not** covered by that grant: `custom_components/ha_creality_ws/www/cfs_box.webp` is a
Creality product render, used by the CFS card's `box` display mode to depict the
hardware this integration controls. See `NOTICE` for the details, including how
to produce a build with no third-party material in it.
