# Lovelace Cards and Their Serving: Technical Reference

**Files assessed:**
- `custom_components/ha_creality_ws/www/k_printer_card.js`: 2061 lines (CRLF)
- `custom_components/ha_creality_ws/www/k_cfs_card.js`: 2705 lines (LF)
- `custom_components/ha_creality_ws/www/i18n/en.json`, `es.json`: 2 sections, 109 + 54 keys each
- `custom_components/ha_creality_ws/frontend.py`: 370 lines
- `tools/tests/js/*.mjs` (11 suites, 2 harnesses), `tools/tests/test_cfs_card.py`, `test_printer_card_editor.py`, `test_printer_card_layout.py`, `test_card_install.py`

Assessment date: 2026-10-02, branch `0.9.9` (manifest version 0.9.9). HA behaviour quoted below was checked against the compiled frontend `home-assistant-frontend 20260826.7` and core 2026.9.3 (the local test container), not against docs.

Line anchors are `file:line`; the file name alone is used when the path is unambiguous.

---

## 1. Architecture Overview

### Elements

| Tag | Class | File | Registered at |
|---|---|---|---|
| `k-printer-card` | `KPrinterCard` | `k_printer_card.js:324` | `k_printer_card.js:1326` |
| `k-printer-card-editor` | `KPrinterCardEditor` | `k_printer_card.js:1603` | `k_printer_card.js:2051` |
| `k-cfs-card` | `KCFSCard` | `k_cfs_card.js:280` | `k_cfs_card.js:2522` |
| `k-cfs-card-editor` | `KCFSCardEditor` | `k_cfs_card.js:2524` | `k_cfs_card.js:2697` |

Card picker entries are pushed onto `window.customCards` at `k_printer_card.js:2053-2061` (wrapped in try) and `k_cfs_card.js:2699-2705` (not wrapped). Both set `preview: true`. Both are pushed once per page (R28) and filled in English from the bundled dictionary, then re-labelled in the page's language once `i18n/<lang>.json` loads; the picker reads the entry when it opens (R33, checked in Chromium with a Spanish profile).

### Key properties (both cards)

- Plain `HTMLElement` subclasses, no Lit, no build step, no imports. Each file is a self-contained ES module loaded as a Lovelace resource of type `module`.
- Shadow DOM (`attachShadow({ mode: "open" })`): `k_printer_card.js:459`, `k_cfs_card.js:444`.
- All markup is template strings assigned to `innerHTML`. The CSS is an inline `<style>` element inside the same template, so every shell rebuild re-parses the whole stylesheet.
- Registration goes through `defineOnce(tag, cls)` (`k_printer_card.js:1317`, `k_cfs_card.js:2513`): `customElements.get` first, `define` in try/catch. This exists because a page can evaluate the same module twice (two resource entries, or a tab open across a restart picking up a new `?v=`).
- No code is shared between the two files. Each has its own copy of the i18n loader, `defineOnce`, `mdi()`, the `fmtState` number formatter and editor tab CSS (see section 10).

### Request flow

```
HA start / entry setup
  __init__.py:416-421  CrealityCardRegistration(hass).async_register()   (every async_setup_entry)
    frontend.py        static paths + Lovelace resource "?v=<hash>"
Browser
  Lovelace loads /ha_creality_ws/k_printer_card.js?v=<hash>  (Cache-Control: max-age 31d)
  Lovelace loads /ha_creality_ws/k_cfs_card.js?v=<hash>
  first `set hass` on a card instance -> fetch /ha_creality_ws/i18n/en.json (+ <lang>.json)
  CFS box view -> /ha_creality_ws/cfs_box.webp
```

---

## 2. Serving and Resource Registration (`frontend.py`)

### When it runs

`CrealityCardRegistration.async_register()` (`frontend.py:222-301`) is awaited from `async_setup_entry` (`__init__.py:416-421`), so it runs once per config entry at startup and again on every entry reload or options change that reloads. Failures are caught and logged at warning (`__init__.py:420-421`). Nothing is undone on unload or removal; resources and static paths are deliberately left in place (`__init__.py:1066-1067`).

### Static paths

| URL | Source | `cache_headers` | Line |
|---|---|---|---|
| `/ha_creality_ws/k_printer_card.js` | `www/k_printer_card.js` | True (`public, max-age=2678400`) | `frontend.py:236` |
| `/ha_creality_ws/k_cfs_card.js` | `www/k_cfs_card.js` | True | `frontend.py:236` |
| `/ha_creality_ws/cfs_box.webp` | `www/cfs_box.webp` | True | `frontend.py:269-278` |
| `/ha_creality_ws/i18n` (directory) | `www/i18n/` | False | `frontend.py:280-289` |

`www/` itself is never registered as a directory (guarded by `test_the_www_directory_is_not_served_wholesale`), so `www/ha_creality_ws.code-workspace` is not served.

`_register_static_path` (`frontend.py:60-101`) deduplicates per process through `hass.data["ha_creality_ws_static_paths"]` (a `set`), because `async_register_static_paths` raises on a duplicate route and this runs per entry. The marker is added before the registration task runs and removed again if it fails, so a failure stays retryable on the next reload. Registration itself runs in a `hass.async_create_task`, so the route exists slightly after `async_register` returns.

`cache_headers=False` selects aiohttp's plain `StaticResource` (core `http/server.py:354-365`), which sends `ETag` and `Last-Modified` but no `Cache-Control`. That is not "do not cache": browsers may apply heuristic freshness (typically 10 percent of the file's age) to such a response. See trap T7.

### Cache buster: `card_version(card_name)` (`frontend.py:23-57`)

`sha256(manifest.version || card bytes)[:10]`. Computed per card in the executor (`frontend.py:233`). Properties:

- Stable across restarts for unchanged bytes (was `time.time()` before; see `test_the_version_is_stable_for_an_unchanged_card`).
- Changes when either the card file or the manifest version changes, so every release re-busts both cards even when a card is byte-identical.
- Missing card file returns the literal `"missing"` (stable, so the resource entry does not churn).
- Only the two card modules carry a version. `i18n/*.json` and `cfs_box.webp` are requested by bare path.

### Lovelace resource helpers

All three helpers lazy-import Lovelace internals and read `hass.data.get("lovelace")` (a `LovelaceData`, keyed by `HassKey("lovelace")` in core) and its `.resources` collection.

**`_init_resource(hass, url, ver)`** (`frontend.py:104-156`), once per card:

1. `await resources.async_get_info()` (loads storage on first use).
2. First item whose `url` starts with the bare card URL: if it already ends with `ver`, no-op; otherwise `async_update_item(id, {res_type: "module", url: url?v=ver})` in storage mode, or in-place mutation of the YAML item. Returns after the first match; later duplicates are never touched.
3. No match: storage mode creates a `module` resource; YAML mode calls `add_extra_js_url(hass, url?v=ver)` instead (frontend `UrlManager` is a frozenset, so repeated calls are harmless).

The method docstring on `CrealityCardRegistration.async_register` (`frontend.py:223-227`) still says "We do NOT auto-create or modify Lovelace resources"; the code does both.

**`_migrate_local_resources(hass, local_prefix, new_url, ver)`**, FIXED (R28): it now **removes** storage-mode resources equal to `/local/ha_creality_ws/<card>.js` (with or without a query) and the malformed `/ha_creality_ws/<card>.js/?...` entries older versions wrote, since `_init_resource` maintains the correct entry; a YAML-mode resource is only warned about. Verified on the test box. Historical behaviour, from the suffix logic written for a directory prefix:

| Legacy resource | Result |
|---|---|
| `/local/ha_creality_ws/k_printer_card.js` | suffix `""`, skipped; the resource stays and now 404s (the `/config/www` copy was just deleted at `frontend.py:238-248`) |
| `/local/ha_creality_ws/k_printer_card.js?v=1` | rewritten to `/ha_creality_ws/k_printer_card.js/?v=1?v=<ver>` (trailing slash, double query): 404 |

Either way the card still works, because `_init_resource` has already created a correct entry; the legacy entry remains as a 404.

**`_expand_base_resource(hass, base, card_versions)`** (`frontend.py:303-371`) rewrites any entry that is exactly `/ha_creality_ws/` (optionally with a query) into the first card URL and creates the others if absent. Takes the versions as a parameter so no hashing happens on the loop.

**Cleanup of old copies** (`frontend.py:238-248`): `Path.exists()` and `unlink()` on `<config>/www/ha_creality_ws/<card>.js` run on the event loop, as do `asset_path.exists()` (`:271`) and `i18n_path.exists()` (`:281`).

### Upgrade flow

HACS replaces the files, the user restarts, `card_version` changes, `_init_resource` rewrites the first matching resource to the new `?v=`, and the next dashboard load fetches the new module (old URL stays cached for up to 31 days, which no longer matters because it is no longer referenced). Open tabs keep the old module until reloaded. Translations are not covered by this flow (T7).

---

## 3. Shared Module-Level Machinery (duplicated in both files)

### i18n loader and lookup

| Piece | Printer | CFS |
|---|---|---|
| URL base | `k_printer_card.js:4` | `k_cfs_card.js:7-8` |
| `_loadI18n(lang)` (fetch + per-module cache) | `:7-19` | `:11-23` |
| `_resolveLang(hass)` | `:20-22` | `:24-26` |
| `_translate(hass, section, fallbackDict, key, vars)` | `:23-37` | `:27-41` |
| `_requestI18n(instance, hass, onLoaded)` | `:38-43` | `:42-47` |
| Bundled fallback dict | `CARD_TRANSLATIONS` `:1190-1302` | `CFS_TRANSLATIONS` `:220-278` |

Language: `hass.locale.language`, then `hass.language`, then `"en"`. `_requestI18n` fetches `en.json` always and `<short>.json` (the part before `-`) when it is not `en`; it is gated by `instance._i18nRequested`, so it runs once per element instance and never again after a language change on the same instance.

Lookup order in `_translate`:

1. `_i18nData[lang][section][key]`. Only short tags are ever fetched, so for `es-419` this step never hits and step 2 does.
2. `_i18nData[short][section][key]`.
3. `_i18nData.en[section][key]`.
4. Bundled dict: `fallbackDict[lang]`, `[short]`, `.en`. Only `en` exists in either bundled dict.
5. The key itself.

Placeholders are `{name}`, replaced with `text.replace(new RegExp("\\{name\\}", "g"), value)`. The value is a replacement string, so `$&`, `$1`, `` $` `` in it are interpreted (T8).

Failure handling: a failed or non-OK fetch resets `_i18nPromises[lang]` to `null`, so the next card instance fetches again. A locale with no file (anything but `en`/`es`, e.g. `cs`, `de`) therefore produces one 404 per card instance created.

Each module has its own `_i18nData`, so a page with both cards fetches `en.json` (and `es.json`) twice.

The bundled English dicts are a second copy of `i18n/en.json`. Drift is guarded by `test_the_bundled_fallback_says_the_same_words_as_i18n` (CFS) and `test_the_bundled_fallback_matches_i18n_en` (printer, also checks the key set).

`_resolveLanguage()` exists on all four classes (`k_printer_card.js:478`, `:1605`; `k_cfs_card.js:463`, `:2526`) and is never called.

---

## 4. `k-printer-card`

### Config schema (`getStubConfig`, `k_printer_card.js:325-390`)

`setConfig` merges `{...getStubConfig(), ..._migrateConfig(config)}` (`:436`), so every key below is always present on `this._cfg`.

| Key | Type | Default | Notes |
|---|---|---|---|
| `name` | string | `""` since R33 (was `"3D Printer"`) | Rendered via `textContent`; empty or the legacy `"3D Printer"` (`isUnnamed`) shows the translated `default_name` and is renamed from the device by "fill from device". Also part of `generateCardId`. |
| `device` | device id | `""` | Editor only. The card never reads it at runtime (`:328-331`). |
| `camera` | entity id | `""` | Only used as the more-info target. |
| `status` | entity id | `""` | Raw state drives icon, colour, chips; display via `formatEntityState`. Also part of `generateCardId`. |
| `progress` | entity id | `""` | `Number(state)` clamped 0-100. |
| `time_left` | entity id | `""` | Scaled to seconds by its `unit_of_measurement` (`DURATION_UNIT_SECONDS`, `:262-276`), floored, `h:mm:ss` / `m:ss` / `Ns` (`:277-285`). |
| `nozzle`, `bed`, `box` | entity id | `""` | `formatEntityState`, then `splitUnit` (`:199-214`). `box` pill hides when unset or absent from `hass.states` (`:1182-1186`). |
| `power` | entity id (`switch`, `input_boolean`) | `""` | Never auto-filled. 2 s optimistic state for `switch.`/`light.` ids (`:940-946`, `:984-987`). |
| `show_power_button` | bool | `true` | Power chip needs this and a non-empty `power`. |
| `layer`, `total_layers` | entity id | `""` | Rendered `layer/total`, `-` for missing/unknown/unavailable. |
| `light` | entity id (`light`, `switch`) | `""` | Chip hidden when the entity is missing or `power` reads `off`. |
| `pause_btn`, `resume_btn`, `stop_btn` | entity id (`button`) | `""` | Pressed via `button.press`. |
| `custom_btn` | any entity id | `""` | See actions below. |
| `custom_btn_icon` | icon | `""` (renders `mdi:gesture-tap`) | |
| `custom_btn_hidden` | bool | `false` | |
| `button_order` | string[] | `pause, resume, stop, light, power, custom` | Deduplicated, unknown keys dropped, missing standard keys (not `custom`) appended (`:1132-1140`). Editor edits it as comma text (`:1570-1577`, `:1896-1903`). |
| `pause_btn_icon` .. `power_btn_icon` | icon | `""` | Fallbacks `mdi:pause`, `mdi:play`, `mdi:stop`, `mdi:lightbulb`, `mdi:power`. |
| `hide_box_temp` | bool | `false` | |
| `theme` | object | below | Deep-merged with the stub theme in `setConfig` (`:453`) and again in every `_render` (`:506-507`). |

`theme` keys (all CSS colour strings; `auto` where noted):

| Key | Default | CSS variable |
|---|---|---|
| `pause_bg` / `pause_icon` | `rgba(252, 109, 9, .90)` / `#fff` | `--pause-bg` / `--pause-icon` |
| `resume_bg` / `resume_icon` | `rgba(76, 175, 80, .90)` / `#fff` | `--resume-bg` / `--resume-icon` |
| `stop_bg` / `stop_icon` | `rgba(244, 67, 54, .95)` / `#fff` | `--stop-bg` / `--stop-icon` |
| `light_on_bg` / `light_icon_on` | `rgba(255, 235, 59, .95)` / `#000` | `--light-on-bg` / `--light-icon-on` |
| `light_off_bg` / `light_icon_off` | `rgba(150,150,150,.35)` / `#000` | `--light-off-bg` / `--light-icon-off` |
| `power_on_bg` / `power_icon_on` | `rgba(76, 175, 80, .90)` / `#fff` | `--power-on-bg` / `--power-icon-on` |
| `power_off_bg` / `power_icon_off` | `rgba(150,150,150,.35)` / `#000` | `--power-off-bg` / `--power-icon-off` |
| `custom_bg` / `custom_icon` | `rgba(33, 150, 243, .90)` / `#fff` | `--custom-bg` / `--custom-icon` |
| `custom_off_bg` / `custom_icon_off` | `rgba(150,150,150,.35)` / `#000` | `--custom-off-bg` / `--custom-icon-off` |
| `custom_on_bg` | not in stub; YAML only | `--custom-on-bg` (falls back to `custom_bg`, `:529`) |
| `status_icon` | `auto` (state colour, `computeColor` `:313-322`) | inline `--icon-color` on `#icon` |
| `progress_ring` | `auto` (state colour) | inline `--ring-color` on `#ring` |
| `status_bg` | `auto` (`radial-gradient(var(--card-background-color) 62%, transparent 0)`) | `--status-bg` |
| `telemetry_icon` | `auto` (`--secondary-text-color`) | `--telemetry-icon` |
| `telemetry_text` | `auto` (`--primary-text-color`) | `--telemetry-text` |

Theme values are interpolated verbatim into the `<style>` block (`:513-538`) and the editor never escapes them (T12).

### Config migration (`_migrateConfig` / `_migrateTheme`, `:413-431`)

Not shape migration (no key was ever renamed). Normalises colours the old editor saved through `hexToRgba(hex, 0.9)`: every parseable colour is re-formatted, and fields without an opacity control are forced to alpha 1. `var(...)`, named colours and `hsl()` pass through untouched. `parseColor` (`:138-170`) accepts `#rgb`, `#rgba`, `#rrggbb`, `#rrggbbaa`, `rgb()`, `rgba()`; `formatColor` (`:179-187`) writes hex when opaque and `rgba(r, g, b, a)` otherwise.

### Theme persistence in localStorage (`:120-255`, `:443-456`)

- Key `k-printer-card-themes`, a map of `cardId -> theme`.
- `cardId = btoa(name + "-" + status)`, non-alphanumerics stripped, first 16 chars (`generateCardId` -> `cardIdForKey(cardKey(cfg))`). Since R7 a key `btoa` rejects (anything above U+00FF) is encoded as UTF-8 bytes first; a Latin-1 key keeps its old id, so a theme stored under it is still found.
- `setConfig` with a `theme` saves it; without a `theme` it loads the stored one. The editor also saves on every emit (`:2043`) and deletes on reset (`:2018-2024`).
- Every config written by the editor contains a full `theme`, so the stored copy only matters for hand-written YAML without one.

Fixed (R7): `btoa` threw `InvalidCharacterError` for any character above U+00FF, so a card named "Tiskárna č.1" was an error card in a real browser and the editor silently dropped the edit (the throw landed in its debounce). The harness `btoa` now throws like a browser's; it encoded anything before, which hid this. Still true: 16 base64 chars cover only the first 12 bytes of the key, so two names that share them share a stored theme. The size memory (below) uses the full key.

### Render pipeline and update triggers

| Trigger | What runs |
|---|---|
| `setConfig(config)` | merge, migrate, localStorage, `attachShadow` once, one `_render()` (the second via `_applyTheme` was removed in R6), resets `_seenStates` so the next hass updates. |
| `set hass(hass)` | `_requestI18n` (once per instance), then `_update()` only if `_relevantChange(hass)`: a different state object for any watched entity (every entity id in the config plus its `switch.`/`light.` twin that `_resolveEntityId` may substitute), a different language, or the formatter appearing. No render, no timer (R6). |
| i18n loaded | `_update()` |
| `connectedCallback` (`:775-783`) | re-attaches the telemetry ResizeObserver if already rendered. |
| `disconnectedCallback` (`:785-799`) | clears the 150 ms timer, the ResizeObserver and a pending rAF. |
| ResizeObserver on `.telemetry` | rAF -> `_updateTelemetryDensity()` then `_updateTelemetryCardSize()` (`:825-836`). |
| Every `_update()` | ends with `_scheduleTelemetrySizeUpdate()` (`:1187`). |

`_relevantChange` is the `shouldUpdate` equivalent (R6). Before it, every `hass` assignment (Home Assistant assigns a new `hass` for any state change anywhere in the instance) rebuilt the whole shadow tree: `<ha-card>`, the stylesheet, six `ha-icon` elements plus one per visible chip, and the listeners, dropping keyboard focus each time; per assignment 1 `_render` + 2 synchronous `_update` + 1 deferred. Pinned by `tools/tests/js/test_printer_render.mjs` and, in a real browser, `tools/testbox/card_check.mjs` (the card's `ha-card` node must survive live telemetry).

`_render()` (`:502-773`) builds the shell (`:667-696`), wires `#more` click/keydown (more-info for `camera || status || progress`) and one delegated click listener on `#chips-container`, resets the telemetry measurement memory, calls `_update()` and re-observes `.telemetry`.

`_update()` (`:973-1188`):

1. Purges expired optimistic overrides.
2. Reads every configured entity once; formats with `hass.formatEntityState` (fallback formatter `:1002-1013`).
3. Writes name, secondary line (`<pct>% <state>` while `printing|paused|processing`), icon, ring percentage and colour.
4. Builds the chips string from `button_order` and assigns `#chips-container.innerHTML` only if the string changed (`:1150-1152`); moot while step "set hass" rebuilds the shell anyway.
5. Writes telemetry text, compares a value signature to reset the unit-retry width (`:1168-1174`), toggles the chamber pill, schedules a size measurement.

Chip visibility (`:1033-1043`):

| Chip | Shown when | Action (`:724-765`) |
|---|---|---|
| Pause | status `printing` | `button.press` on `pause_btn` |
| Resume | status `paused` | `button.press` on `resume_btn` |
| Stop | `printing`, `paused`, `self-testing` | `confirm(confirm_stop)` then `button.press` |
| Light | light entity in `hass.states` and power not `off` | `light`/`switch` `turn_on`/`turn_off`, else `homeassistant.toggle` |
| Power | `show_power_button` and `power` set | `confirm()` only when turning off; stronger text while `printing|paused|processing` |
| Custom | `custom_btn` set and not hidden | `switch`/`light`/`input_boolean`: toggle; `button`/`input_button`: press; anything else: `homeassistant.turn_on` |

Custom chip on/off styling (`:1104-1114`) recognises `switch, light, input_boolean, fan, cover, binary_sensor`, but only the first three toggle on click; a `fan` or `cover` chip can only ever be turned on, and an `automation` is enabled rather than triggered despite the comment at `:755`.

Service calls are not awaited by the click handler and have no error handling in the card; HA's own `callService` raises its toast and rethrows, which surfaces as an unhandled rejection.

### Layout and sizing

Telemetry row CSS (`:620-661`): `.telemetry-wrap` is an inline-size container; pill font, padding, gap and icon size are `clamp(..., Ncqi, ...)` so they scale continuously between about 330 px and 520 px card widths. Pills are `flex: 1 0 auto; white-space: nowrap`.

Density (`_updateTelemetryDensity`, `:867-893`): line count is measured from distinct `offsetTop` values of visible pills. More than one line adds `.compact` (hides unit spans) and remembers the failing width; units are retried only once the row is `TELEMETRY_COMPACT_HYSTERESIS` (8 px) wider, or when the value signature changes.

Card size (`_updateTelemetryCardSize`): `max(3, 2 + lines)`. When it differs from `this._cardSize` and the module-level throttle (`LL_REBUILD_MIN_INTERVAL_MS` = 2000, keyed by the full name/status key) allows, it stores the size in `this._cardSize` **and in the module-level `_measuredCardSize` map**, and dispatches `ll-rebuild`. `getCardSize()` returns `this._cardSize ?? _measuredCardSize.get(key) ?? 3`, and `setConfig` seeds `_cardSize` from the map.

In HA 2026.9, `hui-card` answers `ll-rebuild` by constructing a new card element. Before R6 that instance started at 3, measured the same wrapped row and fired again once the throttle cleared: a rebuild loop (12 elements in 20 s at 300 px in a real browser). Now it inherits the measured size, measures no change and stays; each card is rebuilt at most once while it settles.

No `getGridOptions`/`getLayoutOptions`: in a sections view the card gets HA's default `{columns: 12, rows: "auto"}`.

### Theming and HA variables used

`--primary-text-color`, `--secondary-text-color`, `--card-background-color`, `--primary-color`, `--error-color`, `--success-color` (fallback `#4caf50`), `--info-color` (fallback `#2196f3`). Hard-coded: paused colour `#fc6d09` (`:316`), the chip defaults above, pill background `rgba(127,127,127,.12)` and border `rgba(255,255,255,0.08)` (invisible on light themes), unknown chip `rgba(128,128,128,.14)`.

Chips are `<button>` with `title` and `aria-label`; `.chip` drops the outline for mouse clicks but `.chip:focus-visible` and `.title.click:focus-visible` draw a 2 px `--primary-color` ring (R42). Off-state icon colours default to `auto` (the theme's text colour) instead of `#000`, which was 2.26:1 on a dark theme.

### Entity resolution

The card uses only the entity ids in its config. `_resolveEntityId(eid, domains)` (`:953-971`) retries a missing id under the preferred domains (`light.x` -> `switch.x`), a leftover from older configs. Device scoping exists only in the editor (section 5).

---

## 5. `k-printer-card-editor`

### Architecture

Built once, then only `schema`/`data` are reassigned (`_build` `:1656-1717`, comment `:1648-1655`). Why: `hui-element-editor` echoes every `config-changed` back as `setConfig` (`set value` -> `_updateConfigElement` -> `_configElement.setConfig`), so rebuilding on `setConfig` loses focus and the selected tab.

- The shell is built in `_refresh()` only when both the shadow root and `_cfg` exist (`:1796-1832`), because HA attaches the editor before it calls `setConfig` and `ha-form` reads `schema` unguarded.
- Two tabs (`entities`, `theme`), plain `<div class="tab">` with click handlers (`:1669-1670`, `:1710-1712`); not focusable, no `role="tab"`.
- Seven `ha-form`s: `device-form`, `entities-form`, `layout-form`, `color-form-0..3` (one per `THEME_COLOR_GROUPS` entry, `:1338-1386`).
- `_applyForm` (`:1872-1887`) skips `schema`/`data` assignments whose JSON is unchanged, and `_bindForm` records the value a form just emitted as applied (`:1738-1747`), so the echoed `setConfig` does not reassign `data` under the user's finger.
- `set hass` calls `_refresh()` on every assignment (`:1618-1622`), which reassigns `hass` on all seven forms.
- Build and refresh errors are caught and replaced by an in-place error message (`_showBuildError`, `:1760-1767`), because a throw from the `hass` setter would break HA's own Lit update.

### Schemas

| Form | Builder | Fields |
|---|---|---|
| device | `deviceSchema` `:1465-1472` | `device` selector filtered by `integration: ha_creality_ws` |
| entities | `entitiesSchema` `:1474-1494` | `name` text; entity selectors with domain filters (no integration filter) |
| layout | `layoutSchema` `:1496-1508` | `button_order` text, two booleans, six icon selectors |
| colour groups | `colorSchema` `:1531-1562` | per field: optional `<key>_auto` boolean; `color_rgb`; for alpha fields a flattened grid with `<key>_opacity` slider 0-100 % |

Labels (`_label`, `:1771-1778`): `label_opacity`, `label_color_auto`, `color_<key>` for theme fields, `label_<key>` otherwise, then `humanizeName` as last resort. Helpers (`_helper`, `:1780-1790`): `helper_auto_<key>` on the Automatic switch, `helper_<key>` otherwise. `computeLabel`/`computeHelper` are assigned unconditionally (`:1736-1737`; regression guard `test_label_overrides_are_not_installed_behind_a_feature_check`).

### Colour round trip

`colorData` (`:1585-1600`) splits a stored string into `[r,g,b]` plus 0-100 opacity; automatic fields are omitted. `_onColorChanged` (`:1905-1947`) writes back with `formatColor`; switching Automatic off seeds `field.seed` rather than black; values the picker cannot express (no `rgb` in the form value) are left untouched; toggling Automatic forces an immediate `_refresh()` because the schema shape changes.

### Device prefill (entity auto-discovery)

`DEVICE_ROLE_ENTITIES` (`:74-88`) maps 13 card roles to `{translationKey, domain}`. `entitiesForDevice(hass, deviceId)` (`:98-114`) walks `hass.entities` and keeps entries with that `device_id`, platform `ha_creality_ws` and matching `translation_key` + domain. Picking a device fills blank fields only (`_onDeviceChanged` `:1949-1962`); "Fill all fields from device" overwrites (`:1713-1714`). The card name is filled from `device.name_by_user || device.name` when it is blank or still `"3D Printer"` (`:1971-1984`). `power` is never filled (no such entity in the integration). Status text `Filled {filled} of {total} fields.` excludes the name.

The translation keys are cross-checked against the Python entity classes by `test_every_prefilled_role_is_actually_registered_in_python`.

### `config-changed`

Debounced 120 ms (`_dispatchConfigChange`, `:2031-2037`); `_emitConfig` (`:2039-2049`) saves the theme to localStorage and dispatches `{config: this._cfg}`, `bubbles` + `composed`. A pending emit is flushed in `disconnectedCallback` (`:1636-1644`). The emitted config is the full merged object (every stub key, full theme), not a diff against defaults.

Reset (`_resetTheme`, `:2010-2029`) restores the stub theme and the `LAYOUT_RESET_KEYS` (`:1407-1411`) and deletes the localStorage copy.

---

## 6. `k-cfs-card`

### Config schema (`getStubConfig`, `k_cfs_card.js:414-435`)

62 keys after the merge in `setConfig` (`:442`):

| Key | Type | Default | Notes |
|---|---|---|---|
| `name` | string | `"CFS"` | Header in full mode only; `'Creality CFS'` literal if empty (`:1583`). |
| `view_mode` | `"full"` \| `"compact"` \| `"box"` | `"full"` | Unknown values render as full. |
| `show_type_in_mini` | bool | `false` | Compact mode: material type under each mini spool. |
| `compact_view` | bool (legacy) | absent | `_migrateConfig` (`:322-329`) maps it to `view_mode` and deletes it. |
| `external_filament`, `external_color`, `external_percent` | sensor | `""` | |
| `box{0..3}_temp`, `box{0..3}_humidity` | sensor | `""` | Card positions, not printer box ids. |
| `box{0..3}_slot{0..3}_filament` / `_color` / `_percent` | sensor | `""` | 48 keys. |

No device picker and no auto-discovery: all 51 entity fields are filled by hand.

### Data collection (`_collectData`, `:1250-1412`)

The single reader of `hass.states`. Per configured slot it reads:

- filament sensor: `state` (display label), attributes `type`, `selected`, `color_hex`, `box_id`, `slot_id`, `vendor`, `name`, `rfid`, `min_temp`, `max_temp`, `pressure`;
- colour sensor `state`, falling back to `color_hex` only when the colour state is falsy (`:1296`; an `unknown` state is truthy, so the fallback is skipped);
- percent sensor via `_parsePercent` (`:387-396`) and `formatEntityState`.

Colour parsing (`_parseColor`, `:293-307`): 6-digit, 3-digit, or 7-digit with a leading `0` (the Creality RFID form `#0rrggbb`, issue #113) become `#rrggbb`/`#rgb`; anything else, including multi-colour `"#0ffa800,#0ff97e1"` (`_isMultiColour`, `:346-348`), renders as placeholder `#cccccc` with `colorIsKnown: false`.

Sentinels (`isSentinel`, `:206-211`): `unknown`, `unavailable`, `-`, empty, after trim, case-insensitive.

Printer target (`_resolvePrinterTarget`, `:361-385`): numeric `box_id`/`slot_id` attributes; else the entity id pattern `cfs_box_N_slot_M_` with M decremented (names are 1-based, printer slots 0-based) and flagged as guessed; else card position + 1, guessed. The external spool uses attributes only and is not editable without a numeric `box_id`.

### Render gate and update triggers

| Trigger | What runs |
|---|---|
| `setConfig` (`:441-460`) | merge + migrate, reset snapshot and device-resolution state, bump `_deviceIdGeneration`, `_render()` (shell). |
| `set hass` (`:471-527`) | `_requestI18n`, one retry of a null device id once `hass.entities` knows a configured entity, async `_resolveDeviceId()` if unresolved, then `_updateIfChanged()`. |
| i18n loaded | snapshot reset, `_update()`. |
| unit selector click | `_selectedCFS = n`, `_updateIfChanged()`. |

`_render()` (`:529-1240`) only builds `<ha-card class="<mode>"><style/><div id="content"/></ha-card>`. `_updateIfChanged()` (`:1440-1447`) computes `_fingerprint(data)` = `JSON.stringify([view_mode, show_type_in_mini, _isPrinterBusy(), _deviceIdError, _selectedCFS, data])` (`:1424-1437`) and calls `_update(data)` only when it changed. `_update` (`:1449-1473`) replaces `#content.innerHTML` with one of three renderers and re-binds handlers (`_attachEventHandlers`, `:2404-2448`, property `onclick` assignment, so no accumulation).

Every telemetry value interpolated into markup goes through `esc()` (`:79-86`); colours reaching `style` attributes are whitelisted hex from `_parseColor`/`_sanitizeColor`, and the humidity colour is one of four literals (`:402-412`).

### View modes

| Mode | Renderer | Notes |
|---|---|---|
| full | `_renderNormalMode` `:1540-1626` | unit selector (if more than one box), header with title and temp/humidity, 2x2 `_renderSpoolCard` grid, external row with progress bar. Only the selected box is shown. |
| compact | `_renderCompactMode` `:1628-1644` | one `_renderCFSRow` per box with `_renderSpoolMini` rings and env column; external via `_renderExternalCompact`. |
| box | `_renderBoxMode` `:1485-1538` | `cfs_box.webp` (784x540) with four absolutely positioned bays; requires the selected box to have exactly four configured slots, otherwise falls back to full. |

Clicking a spool, bay or external row opens more-info for its entity (`:2436-2447`). These are `div`s with no role, tabindex or key handler. Unit selector buttons are real `<button>`s without `aria-pressed`.

### Material editing

1. **Device resolution** (`_resolveDeviceId`, `:1830-1898`): every configured entity's `device_id` from `hass.entities`; ids not in it are asked through `config/entity_registry/get` in parallel. Fails closed: zero or more than one device, or any failed lookup, sets `_deviceIdError` (`toast_no_device` / `toast_multiple_devices`). Generation-guarded against a `setConfig` landing mid-await.
2. **Busy lock** (`_statusEntityId` `:1936-1959`, `_isPrinterBusy` `:1970-1975`): the device's entity with platform `ha_creality_ws` and `translation_key` `print_status`; busy states `printing, paused, processing, self-testing` (`:213-218`, cross-checked against `utils.BUSY_PRINT_STATES` by `test_card_busy_states_match_the_integration`). Only a hit is memoised.
3. **Edit button** (`_renderEditButton`, `:1710-1734`): real `<button>` with escaped `title`/`aria-label`; blocked states use `aria-disabled="true"` (not `disabled`) so a click can explain the block via toast. Hover-reveal only under `@media (hover: hover)` (`:1054-1066`); `:focus-visible` ring (`:1067-1071`).
4. **Dialog** (`_showEditDialog`, `:2021-2072`): an `.edit-overlay` (`position: fixed; z-index: 100`) appended to the card's shadow root, `role="dialog"`, `aria-modal`, Escape and backdrop click close it. Focus goes to the dialog; two `.focus-sentinel` spans around it keep Tab inside (past the end returns to the dialog, before the start goes to the last button), and closing returns focus to the element that opened it (R42). Sentinels, because `ha-form` hides its inputs in its own shadow root. The body (`_renderEditForm`, `:2085-2232`) is an `ha-form` for type, name, vendor, min/max temp (150-300 / 150-350 C), pressure (0-1), bounds cross-checked against `services.yaml`, plus a hand-built colour row (native colour input + hex text). Multi-colour spools get a disabled colour row.
5. **Presets** (`_renderPresets`, `:2244-2329`; `ColourPresetsManager`, `:118-183`): 12 Creality standard colours plus user presets in localStorage `k-cfs-colour-presets`, one shared store per page (`sharedPresets`, `:194-198`), created lazily. Custom presets are deleted by right-click (`contextmenu`) only. `rename()` (`:156-163`) has no caller outside tests.
6. **Save** (`_saveMaterial`, `:2335-2402`): validates type, temperature order and 6-digit hex; calls `ha_creality_ws.set_cfs_material` with `device_id, box_id, slot_id, type` and the optional `name, vendor, color, min_temp, max_temp, pressure, rfid`; then `ha_creality_ws.request_cfs_info`. Errors become toasts.

Toasts (`_showToast`, `:1978-1995`): one at a time, 4 s, `position: absolute` at the bottom of the card host, `z-index: 110` (above the overlay, guarded by `test_a_toast_is_stacked_above_the_edit_overlay`), `role="status"` and `aria-live="polite"` (R42).

### Layout and sizing

- `getCardSize()` (`:2450-2470`): compact = configured boxes + external row (+1 when more than 2); full and box = 5.
- `getGridOptions()` since R23: `{columns: 12, rows: "auto", min_columns: 6}`, so a sections view sizes the cell to the card. The previous `getLayoutOptions()` returned fixed `{grid_rows: N, grid_min_rows: N}` (5 for full), which HA turned into a fixed-height wrapper the card then drew past.
- `:host` forces `display:block; height:auto; contain:none` with `!important`, plus `position:relative; z-index:1` (`:536-544`); `ha-card` forces `overflow:visible` and `height:auto` (`:549-558`). Content taller than the fixed sections wrapper therefore spills over the cards below instead of scrolling or clipping (T6), which is the symptom reported in #69/#71.

### Theming and HA variables used

`--primary-text-color`, `--secondary-text-color`, `--rgb-primary-text-color` (many translucent fills), `--rgb-primary-color` (active spool), `--primary-color`, `--card-background-color`, `--secondary-background-color`, `--divider-color`, `--success-color`, `--warning-color`, `--text-primary-color`, `--ha-card-border-radius`, `--ha-card-box-shadow`. Themes that do not define the `--rgb-*` variants lose those fills (issue #77 territory). Hard-coded: temperature text `#ffb74d` (`:862`), humidity palette, `.ext-icon`/`.ext-dot` white text on `--primary-color`, empty mini spool `#333`, placeholder `#cccccc`, `.edit-warning` black on `--warning-color`. Infinite `pulse-badge` animation on the active slot with no `prefers-reduced-motion` handling (`:736-742`). The `.card` rule (`:570-579`) and `.subtitle` (`:600-604`) match no element.

### Lifecycle

No `connectedCallback`/`disconnectedCallback` on the card. Nothing global is subscribed, so nothing leaks; the toast timer simply fires on a detached node. A `setConfig` while the dialog is open (dashboard saved elsewhere, editor preview) wipes the dialog with the shell.

---

## 7. `k-cfs-card-editor`

`k_cfs_card.js:2524-2695`.

- Built once since R22 (`_refresh` -> `_build` the first time, then `_setFormData`, which reassigns `data` only when the JSON of the config differs from what the forms show). `_edited` updates the shown data before dispatching, so Lovelace's echoed `setConfig` is a no-op. A late language load relabels (`_applyLabels`: tab text, new `computeLabel` functions, theme schema). Before R22 every `setConfig` replaced the shadow tree, so every keystroke lost focus and the tab reset to Entities. Pinned by `tools/tests/js/test_cfs_editor.mjs`.
- `config-changed` is dispatched synchronously on every `value-changed`, no debounce (`:2688-2694`), with the full 62-key config.
- Entities form: 52 fields (`name` + 51 entity selectors, all `domain: sensor`, no integration filter), labels via `label_*` keys with `{box}`/`{slot}` placeholders (1-based for display) (`:2598-2655`). Unknown names fall back to the raw key.
- "Theme" tab holds `view_mode` (select: full/compact/box) and `show_type_in_mini` (`:2657-2686`). The tab name says Theme; there is no theming.
- `set hass` updates `hass` on both forms (R22).

---

## 8. Translation Files (`www/i18n/*.json`)

Two sections, `printer_card` (109 keys) and `cfs_card` (54 keys), identical key sets in `en` and `es` (`test_english_and_spanish_have_the_same_keys`), placeholders currently identical but not tested. Only `en` and `es` exist, matching the integration's `translations/`.

Unused keys: `cfs_card.cfs_label` and `cfs_card.schema_compact_view` (the latter is not in the bundled dict either). Printer keys that look unused (`label_*`, `helper_*`, `color_*`, `helper_auto_*`) are built dynamically in `_label`/`_helper`.

Strings that bypass i18n, all deliberate since R33 translated the default printer title and the picker entries: CFS stub `name: "CFS"` and the `'Creality CFS'` fallback (product names), `CREALITY_STANDARD_COLOURS` names (deliberate, tooltips), `humanizeName` labels, the `#rrggbb` placeholder, and the `Ns` seconds suffix in `fmtTimeLeft`.

---

## 9. Tests

### How they run

`tools/tests/test_cfs_card.py::test_javascript_suite` parametrises over `tools/tests/js/test_*.mjs` and runs `node <suite>` from the repo root, asserting exit code 0 (`test_cfs_card.py:76-89`). `test_the_javascript_suites_are_discoverable` fails if the glob is empty or a named suite disappears. Node-dependent tests skip when `node` is missing; CI installs Node 20 (`.github/workflows/tests.yml:38-40`), so the skip reason "expected in CI" is stale. Standalone: `node tools/tests/js/<suite>.mjs`.

### Runner check

Every suite builds `tests` with `const test = (name, fn) => tests.push([name, fn])` and iterates once at the bottom. Checked on this branch: in all 11 suites the last `test(` call is above the `for (const [name, fn] of tests)` loop, no `test(`/`tests.push` appears below it, every runner exits 1 on failure, and every suite with `async` tests awaits `fn()`. The counts printed match the registrations: box_view 15, collector 15, device_scoping 21, edit_dialog 33, interactions 9, presets 18, printer_editor 35, printer_migration 13, printer_telemetry 17, time_left 6, view_mode 14 (196 total, all passing). The Python card tests (`test_cfs_card.py`, `test_printer_card_editor.py`, `test_printer_card_layout.py`, `test_card_install.py`) pass, 61 tests in about 2 s.

### Harness (`cfs_card_harness.mjs`, `printer_card_harness.mjs`)

A `vm` sandbox with a small DOM shim: `FakeElement` parses the card's own markup into a tree, supports `getElementById`/`querySelector(All)` on simple selectors, `fire()` for events. `customElements.define` throws on duplicates like the real registry. localStorage counts reads/writes.

Shim behaviour that differs from a browser and hides real bugs:

- `btoa` is `Buffer.from(s, "binary").toString("base64")` (`cfs_card_harness.mjs:332`), which never throws; browser and Node `btoa` throw for characters above U+00FF.
- `fetch` never resolves, so the i18n loader, 404 handling and language fallback are never exercised.
- `dispatchEvent` is a no-op returning true; `ll-rebuild` and `config-changed` are only observable if a test patches it.
- No layout engine; geometry tests stub `offsetTop`/`clientWidth`.

### Coverage map

| Area | Covered by |
|---|---|
| CFS collection, sentinels, colour parsing, render gate | `test_collector.mjs`, `test_box_view.mjs` |
| CFS device scoping, busy lock, stale-generation races | `test_device_scoping.mjs` |
| CFS dialog, escaping, payload, Escape | `test_edit_dialog.mjs` |
| CFS clicks, unit selector | `test_interactions.mjs` |
| Presets | `test_presets.mjs` |
| CFS view modes, `getCardSize`, `getGridOptions` (rows auto) | `test_view_mode.mjs`; overlap measured in Chromium by `tools/testbox/card_check.mjs` |
| Printer editor build-once, labels, device prefill, debounce flush | `test_printer_editor.mjs`, `test_printer_card_editor.py` |
| Printer colour migration | `test_printer_migration.mjs` |
| Printer telemetry density on one instance | `test_printer_telemetry.mjs`, `test_printer_card_layout.py` |
| Duration scaling | `test_time_left.mjs` |
| Cache buster, static path dedup | `test_card_install.py` |
| i18n key presence and bundled/remote parity | `test_cfs_card.py`, `test_printer_card_editor.py` |

Not covered: `_init_resource`, `_migrate_local_resources`, `_expand_base_resource`; printer render cost per `hass`; `ll-rebuild` across element recreation; non-Latin-1 card names; CFS editor behaviour under the `setConfig` echo; i18n fetch/fallback; `$` patterns in placeholder values; card i18n placeholder parity.

---

## 10. Duplication Between the Cards

Identical or near-identical in both files: the whole i18n block (section 3), `defineOnce`, `mdi`, the `fmtState` fallback formatter (`k_printer_card.js:992-1014`, `k_cfs_card.js:1253-1272`), editor tab CSS and tab switching, `_resolveLanguage`, the `customCards` push. A shared module would remove one copy of each and the double i18n fetch, but a relative `import` does not inherit the importer's `?v=`; the shared file would need its own cache buster (for example `new URL(import.meta.url).search` appended to the import specifier via dynamic `import()`), or it becomes the one stale file after an upgrade.

---

## 11. Traps

**T1. The printer card rebuilds its shadow DOM on every `hass` assignment.** `set hass` -> `_applyTheme()` -> `_render()` (`k_printer_card.js:486-499`, `:468-475`). Unrelated entity changes count. Keyboard focus on `#more` or a chip is destroyed each time, the stylesheet is re-parsed and every `ha-icon` (six in the shell, one per visible chip) is recreated. The 150 ms follow-up timer adds a third `_update`.

**T2. `ll-rebuild` recreates the card it is trying to resize.** `hui-card` handles `ll-rebuild` by constructing a new element, which has no `_cardSize` and reports 3 again. Whenever the telemetry row needs two or more lines even with units hidden (narrow phone, small sections column), the card destroys and recreates itself every ~2 s for as long as telemetry keeps arriving, and Lovelace never sees the larger size. Reproduced in the node harness with the HA handler simulated (5 rebuilds in 10 simulated seconds, `getCardSize()` always 3). `test_printer_card_layout.py:25-39` asserts the mechanism exists.

**T3. `generateCardId` uses `btoa` on the card name.** A name with any character above U+00FF (`č`, `ř`, an en dash, CJK, emoji) throws `InvalidCharacterError` from `setConfig`, so HA renders an error card; in the editor the throw happens inside the debounce timer in `_emitConfig`, so `config-changed` is never sent and edits are silently dropped. The device picker copies the device's user-facing name into `name`, so this is reachable without typing. Separately, the id covers only the first 12 bytes of `name-status`: `Creality K1 Max` and `Creality K1 Max 2`, or two default-named `3D Printer` cards whose status is a `sensor.` entity, share theme storage and the rebuild throttle.

**T4. The CFS editor rebuilds on every keystroke.** `setConfig` -> `_render()` replaces both forms and resets the tab, and HA calls `setConfig` after each `config-changed`. Reproduced in the harness: after one `value-changed` plus the echo, `_form` is a new element and the Theme tab is no longer active. The printer editor solved exactly this; the CFS editor did not get the same treatment.

**T5. The CFS dialog lives inside the card's stacking context.** `:host { position: relative; z-index: 1 }` makes every CFS card its own stacking context at level 1. The overlay's `z-index: 100` only orders it inside that context, so later siblings with their own stacking context (a second CFS card, which has the same host rule) and HA's toolbar paint over the backdrop and possibly the dialog. A native `<dialog>` with `showModal()` (top layer) would escape this and also give a focus trap and Escape handling.

**T6. FIXED (R23).** `getLayoutOptions` with numeric rows plus `height:auto !important` overflowed sections: measured overlap 176 px (full) and 31 px (compact) at 390 px wide, 0 with `getGridOptions` rows auto. The `!important` heights stay; with an auto row they no longer fight the wrapper.

**T7. Translations and the CFS image are not cache-busted.** `i18n/*.json` is fetched by bare URL without `Cache-Control`, so heuristic caching can keep old strings after an upgrade (new keys then show the bundled English); `cfs_box.webp` is served with a 31-day `max-age` under a bare URL, so a replaced image (with re-measured bay geometry in CSS) stays stale. A locale without a file is re-requested by every new card instance, which T2 turns into a 404 every ~2 s.

**T8. Placeholder values are replacement strings.** `toast_preset_saved` with a preset called `Teal $& Co` renders `Preset "Teal {name} Co" saved` (reproduced). Same in the printer card. Use a replacer function.

**T9. Module-scope `const` declared after `defineOnce`.** `THEME_COLOR_FIELDS` (`k_printer_card.js:1389`) is used by `KPrinterCard._migrateTheme` but declared after `defineOnce(CARD_TAG, KPrinterCard)` (`:1326`). Safe today only because HA calls `setConfig` after `whenDefined` resolves, which is after module evaluation; any synchronous use during upgrade would hit the temporal dead zone.

**T10. The editors emit the whole merged config.** Both editors dispatch `{...stub, ...config}`: 62 keys for CFS, 28 top-level keys plus a 23-key theme for the printer card. Defaults are frozen into every saved dashboard, so changing a default later reaches no existing card, and the YAML view is mostly empty strings.

**T11. Legacy `/local/` resources become 404 entries.** See section 2; the rewrite builds `/ha_creality_ws/<card>.js/?v=<old>?v=<new>`.

**T12. Printer theme values and icon overrides are interpolated unescaped.** Theme strings go straight into `<style>` (`k_printer_card.js:513-538`) and `btn.icon` into an attribute (`:1146`), inside templates assigned to `innerHTML`. A value containing `</style>` or `"` breaks out into markup. Only dashboard editors (admins) can set these, so this is hardening, not an exploit path; the CFS card's `esc()` discipline is the model.

**T13. CFS comments overstate admin gating.** `k_cfs_card.js:1846-1849` and `:1869-1873` say `hass.entities` is absent for non-admins and `config/entity_registry/get` is admin-only. In core 2026.9 neither `config/entity_registry/list_for_display` nor `config/entity_registry/get` carries `@require_admin` (`components/config/entity_registry.py:68-103`). The fail-closed logic is still correct; the stated reason is not.

---

## 12. Discrepancies Between Code and README

1. README "Pause shown when `printing|resuming|pausing`": the card shows Pause only for `printing`; `resuming`/`pausing` are not states the integration produces (`PRINT_STATES`, `k_printer_card.js:290-301`).
2. README YAML example uses `light: switch.k1c_light`, `box: sensor.k1c_box_temperature`, `layer: sensor.k1c_working_layer`, `time_left: sensor.k1c_print_time_left`; the integration's roles are a `light.` entity, `chamber_temperature`, `current_layer` and `print_left_time` (`DEVICE_ROLE_ENTITIES`, `k_printer_card.js:74-88`).
3. README YAML-mode resource block lists only `k_printer_card.js`; the CFS card is reachable in YAML mode only because `_init_resource` falls back to `add_extra_js_url`.
4. `CrealityCardRegistration` docstrings (`frontend.py:213-217`, `:223-227`) describe serving one card and not touching resources; the code serves two cards and creates/updates resources.

## Accessibility (R42, 2026-10-03)

- **Contrast.** CFS temperature and humidity are `color-mix(in srgb, <hue> 45%, var(--primary-text-color))`. The humidity hue comes in through `--hum-color`, still through `_sanitizeColor`. Measured in Chromium on the test box: 5.1-7.0:1 on the default light theme, 8.3-11.4:1 on dark. It was 1.73:1 for the temperature on light.
- **Editor tabs.** Both editors' tabs are `<button role="tab">` inside a `role="tablist"`, with `aria-selected` kept in step and a focus ring.
- **Motion.** The CFS status pulse stops under `prefers-reduced-motion: reduce`.
- **Tests.** `tools/tests/js/test_accessibility.mjs` covers all of the above.

## CFS editor: fill from the printer (R43, 2026-10-03)

A device picker (`#device-form`, filtered to `ha_creality_ws`) and a "Fill all fields from the printer" button (`#refill`) sit above the entity form.

`cfsEntitiesForDevice(hass, deviceId)` walks `hass.entities` for that device:
- `cfs_slot_*` sensors are placed by their `box_id`/`slot_id` attributes, or by the default entity id (`..._cfs_box_1_slot_2_filament`, slot 1-based) while a sensor has no reading.
- `cfs_box_temp`/`cfs_box_humidity` are placed by the new `box_id` attribute.
- `cfs_ext_*` fill the external fields.
- CFS units take card positions 0-3 in printer box order. Box 0, the external holder (pre-R20 installs still have "Box 0 Slot 1" sensors), is skipped.

Picking a device fills only empty fields; the button replaces. The status line reads "Filled X of Y fields." `device` is stored in the card config for the button.
