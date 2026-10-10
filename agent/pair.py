import sys
import time
from app import security

PORT = security.PORT
cmd = sys.argv[1] if len(sys.argv) > 1 else "new"

if cmd == "new":
    security.print_pairing(security.lan_ip(), PORT)
elif cmd == "list":
    for d in security.list_devices():
        seen = time.strftime("%Y-%m-%d %H:%M", time.localtime(d["last_seen"]))
        print(f'{d["id"]}  {d["name"]:<24} last seen {seen}')
elif cmd == "revoke" and len(sys.argv) == 3:
    print("revoked" if security.revoke_device(sys.argv[2]) else "no such device")
else:
    print("usage: python pair.py [new | list | revoke <id>]")