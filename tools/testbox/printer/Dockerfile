# The mock printer (tools/simulator/, started through
# tools/creality_printer_test_server.py) and nothing else.
# Its own image because it needs aiortc, which the Home Assistant image lacks.
# The server itself is bind-mounted from the working tree, not copied, so an
# edit to it only needs `docker compose restart printer`.
FROM python:3.13-slim

# No ffmpeg: the camera loop is pre-rendered and stored with the simulator
# (tools/simulator/media/), so nothing is encoded at runtime.

RUN pip install --no-cache-dir \
    "aiohttp>=3.8" "aiortc>=1.7,<2" "av>=12.0" "numpy>=1.24" \
    "websockets>=12.0" "Pillow>=10.0"

WORKDIR /tools
