# Implementation Plan - Fix WebRTC camera stream regression

## Phase 1: Diagnostics and Reproduction
- [ ] Task: Research go2rtc 1.9.14 changes and potential breaking changes in WebRTC signaling.
- [ ] Task: Inspect `_ensure_stream_configured` in `camera.py` to see how streams are registered.
- [ ] Task: Add enhanced debug logging to `async_handle_async_webrtc_offer` to capture the full request/response context.
- [ ] Task: Conductor - User Manual Verification 'Diagnostics and Reproduction' (Protocol in workflow.md)

## Phase 2: Implementation
- [ ] Task: Update the go2rtc stream registration logic if needed.
- [ ] Task: Adjust `forward_whep_sdp_offer` parameters or call pattern to match go2rtc 1.9.14 requirements.
- [ ] Task: Implement improved error recovery in `camera.py`.
- [ ] Task: Conductor - User Manual Verification 'Implementation' (Protocol in workflow.md)

## Phase 3: Verification
- [ ] Task: Verify the fix with the user's reported environment (if possible).
- [ ] Task: Perform a final code review and linting.
- [ ] Task: Conductor - User Manual Verification 'Verification' (Protocol in workflow.md)
