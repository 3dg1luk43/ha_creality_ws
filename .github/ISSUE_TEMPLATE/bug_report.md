---
name: Bug Report
about: Report a problem with the Creality WebSocket Integration
title: "[Bug] Short description"
labels: bug
assignees: '3dg1luk43'
---

## Environment

- Home Assistant Core version: (e.g. 2025.11.0)
- Integration version (from `manifest.json` or HACS):
- Installation method: HACS / Manual
- Printer model (friendly name):
- Printer board/modelVersion code (if shown, e.g. F012, F021):
- Connection host/IP:
- Camera type (reported or forced): auto / mjpeg / webrtc
- Power switch bound?: yes / no

## Description

<!-- What happened? Provide a clear summary of the issue. -->

## Expected Behavior

<!-- What did you expect to happen instead? -->

## Reproduction Steps
<!-- Replace with exact steps -->
1. Step one
2. Step two
3. ...

## Logs & Diagnostics

<!-- 1. Run the service `ha_creality_ws.diagnostic_dump` and paste ONLY the `printers` section (remove any sensitive info like hostnames if needed) -->

```
<paste printers subsection or attach file>
```

<!-- 2. Relevant Home Assistant log lines (filter for `ha_creality_ws`) -->

```
<paste log excerpt>
```

## Affected Entities

<!-- List entity IDs that are wrong / missing / noisy -->

```
sensor.my_printer_print_progress
light.my_printer_light
image.my_printer_current_print_preview
```

## Additional Context / Screenshots

<!-- Add any screenshots or additional context here. -->

## Regression?

<!-- Did this work in an earlier version? If yes, which version? -->

## Checklist

- [ ] I confirmed the issue persists after a hard Lovelace refresh (Ctrl+F5 / Reload resources)
- [ ] I confirmed the issue persists after a Home Assistant restart
- [ ] I searched existing issues and did not find a duplicate
- [ ] I included diagnostic output and log excerpts
