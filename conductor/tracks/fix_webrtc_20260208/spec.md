# Specification: Fix WebRTC camera stream regression

## Problem Statement
After the Home Assistant 2026.2.1 update, which includes go2rtc 1.9.14, the WebRTC camera stream in the `ha_creality_ws` integration has stopped working. Users report a "500 Internal Server Error" when the integration attempts to forward the WebRTC offer to the local go2rtc instance.

## Error Analysis
The log trace indicates the following:
- `aiohttp.client_exceptions.ClientResponseError: 500, message='Internal Server Error', url='http://localhost:11984/api/webrtc?src=creality_k2_192_168_0_90'`
- This occurs in `async_handle_async_webrtc_offer` when calling `self._go2rtc_client.webrtc.forward_whep_sdp_offer`.
- The `go2rtc` version is 1.9.14.

The 500 error from go2rtc suggests that the source (`src`) might not be correctly initialized or the signaling format expected by go2rtc 1.9.14 has changed.

## Requirements
- Identify the change in go2rtc 1.9.14 that causes the 500 error.
- Update `custom_components/ha_creality_ws/camera.py` to correctly handle signaling with go2rtc 1.9.14.
- Ensure backward compatibility with older go2rtc versions if necessary, or document the minimum required version.
- Improve error handling in `async_handle_async_webrtc_offer` to provide more actionable insights when go2rtc fails.

## Proposed Changes
1. **Investigation:** Verify if the stream name/source registration in go2rtc has changed.
2. **Code Update:**
   - Review `_ensure_stream_configured` in `camera.py`.
   - Update `async_handle_async_webrtc_offer` to use the correct API or parameters for go2rtc 1.9.14.
3. **Verification:** Test with HA 2026.2.1+ and go2rtc 1.9.14.
