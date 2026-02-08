# Product Guidelines - Creality WebSocket Integration

## Communication & Logging
- **Technical Precision:** Logs should prioritize exact error codes and raw telemetry states to facilitate advanced troubleshooting and debugging.
- **Structured Debugging:** Use hierarchical logging to separate core WebSocket events from high-frequency sensor updates.

## Visual Identity & UX
- **Native Integration:** Custom Lovelace cards MUST adhere to Home Assistant's Material Design principles.
- **Consistent UI:** Use standard Home Assistant icons, fonts, and color palettes to ensure the cards look like native components.
- **Accessibility:** Prioritize readability and clear status indicators that align with HA's visual language.

## Development & Architecture
- **Capability-Based Design:** Avoid hardcoding model lists; prefer detecting features (e.g., `boxTemp`, `lightSw`) based on available telemetry fields.
- **Modular Extensibility:** Model-specific logic should be abstracted into the `utils.py` or dedicated handler classes to keep core logic clean.
- **Defensive Parsing:** Always provide safe fallbacks for unexpected telemetry values to prevent integration crashes.

## Performance & Reliability
- **Real-Time Responsiveness:** The integration must maintain a low-latency pipeline from the WebSocket stream to the HA Event Bus.
- **Resilient Connectivity:** Implement mandatory auto-reconnect logic with exponential backoff, specifically tuned to handle printer power cycles and Wi-Fi drops.
- **Resource Optimization:** Minimize CPU and memory usage when handling frequent telemetry updates and image/camera streams to ensure stability on lower-powered HA hardware (e.g., Raspberry Pi).
