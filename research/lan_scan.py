import socket, json, time
# Govee LAN API: ask on multicast 4001, replies come to port 4002
r = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); r.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
r.bind(("", 4002)); r.settimeout(1)
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
msg = json.dumps({"msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}}).encode()
end = time.time() + 6
while time.time() < end:
    s.sendto(msg, ("239.255.255.250", 4001))
    try:
        data, addr = r.recvfrom(4096); print(addr[0], data.decode())
    except socket.timeout: pass
