"""Print the go2rtc streams of the Home Assistant in this container.

HA 2026.9 runs go2rtc on a Unix socket with generated credentials (no TCP
API), so this reads both from HA's managed config. Copied in and run by
`hactl.py go2rtc`.
"""
import glob, json, re, socket, base64, http.client
import os; d = max(glob.glob('/tmp/go2rtc-*/'), key=os.path.getmtime); cfg = open(glob.glob(d + 'go2rtc_*.yaml')[0]).read()
user = re.search(r'username: (\S+)', cfg).group(1); pw = re.search(r'password: (\S+)', cfg).group(1)
sock_path = d + 'go2rtc.sock'
class C(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX); self.sock.connect(sock_path)
c = C('localhost'); c.request('GET', '/api/streams', headers={'Authorization': 'Basic ' + base64.b64encode(f'{user}:{pw}'.encode()).decode()})
d = json.loads(c.getresponse().read())
for k, v in d.items():
    print(k, 'producers:', json.dumps([{kk: p.get(kk) for kk in ('url', 'format_name', 'protocol', 'remote_addr')} for p in (v.get('producers') or [])]), 'consumers:', len(v.get('consumers') or []))
