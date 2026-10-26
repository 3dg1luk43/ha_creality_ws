"""SDP shaping for the simulated printer's WebRTC answer. Pure Python on purpose.

A real K1C (firmware 1.3.5.22) answers every offer with the same video line,

    m=video 9 UDP/TLS/RTP/SAVPF 96 101

and streams on 101: 96 is listed first and never sent. go2rtc's creality source
(`fixCrealitySDP`) relies on that and drops the first codec of the answer. The
simulator's answer is aiortc's, which lists the codec it sends first, so go2rtc
dropped exactly that one and counted no packets although the SRTP arrived
(R78). `creality_answer` puts an unused H.264 payload type in front, the way
the printer does.
"""
from __future__ import annotations

# The printer's own first entry, kept when the offer left it free.
DECOY_PT = 96


def creality_answer(sdp: str) -> str:
    """The answer with an unused H.264 payload type listed first in the video line."""
    lines = sdp.split("\r\n")
    try:
        m_index = next(i for i, line in enumerate(lines) if line.startswith("m=video "))
    except StopIteration:
        return sdp
    fields = lines[m_index].split(" ")
    formats = fields[3:]
    taken = {int(pt) for pt in formats if pt.isdigit()}
    for line in lines:
        if line.startswith("a=rtpmap:"):
            pt = line[len("a=rtpmap:"):].split(" ", 1)[0]
            if pt.isdigit():
                taken.add(int(pt))
    decoy = next((pt for pt in [DECOY_PT, *range(96, 128)] if pt not in taken), None)
    if decoy is None:
        return sdp
    lines[m_index] = " ".join(fields[:3] + [str(decoy)] + formats)
    # Its attributes go before the section's first rtpmap, where the printer has them.
    end = next((i for i in range(m_index + 1, len(lines)) if lines[i].startswith("m=")), len(lines))
    insert_at = next(
        (i for i in range(m_index + 1, end) if lines[i].startswith("a=rtpmap:")), end
    )
    lines[insert_at:insert_at] = [
        f"a=rtpmap:{decoy} H264/90000",
        f"a=fmtp:{decoy} profile-level-id=42e01f;level-asymmetry-allowed=1",
    ]
    return "\r\n".join(lines)
