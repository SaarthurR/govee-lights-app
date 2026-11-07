# Wi-Fi LAN test: status, full brightness, then per-bulb "razer" stream. usage: lan_test.py <ip> <bulbs>
import socket, json, base64, sys, time
ip, n = sys.argv[1], int(sys.argv[2])
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
r = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); r.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); r.bind(("", 4002)); r.settimeout(2)
def cmd(c, data): s.sendto(json.dumps({"msg": {"cmd": c, "data": data}}).encode(), (ip, 4003))
def razer(b):
    b = bytes(b); x = 0
    for v in b: x ^= v
    cmd("razer", {"pt": base64.b64encode(b + bytes([x])).decode()})
def frame(colors):  # bb 00 len b0 01 count rgb...
    body = [0xb0, 0x01, len(colors)] + [v for c in colors for v in c]
    return [0xbb, 0x00, len(body) - 1] + body
assert frame([(1, 2, 3)]) == [0xbb, 0, 5, 0xb0, 1, 1, 1, 2, 3]
cmd("devStatus", {})
try: print("status:", r.recvfrom(4096)[0].decode())
except socket.timeout: print("no status reply")
cmd("turn", {"value": 1}); cmd("brightness", {"value": 100}); time.sleep(0.5)
razer([0xbb, 0x00, 0x01, 0xb1, 0x01])  # razer mode on
GOLD, SILVER = (255, 200, 80), (255, 255, 255)
for t in range(40):  # 8 s: alternate, then swap
    razer(frame([GOLD if (i + t // 10) % 2 else SILVER for i in range(n)]))
    time.sleep(0.2)
print("sent", n, "bulbs")
