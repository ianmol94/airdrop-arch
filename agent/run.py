import sys

import uvicorn

from app import security

PORT = security.PORT

if __name__ == "__main__":
    ip = security.lan_ip()
    print(f"Certificate SHA-256: {security.ensure_cert(ip)}\n")
    if sys.stdout.isatty():
        security.print_pairing(ip, PORT)
    else:
        print("Running as a service. Run `python pair.py` in a terminal to pair a device.")
    uvicorn.run(
        "app.main:app", host="0.0.0.0", port=PORT,
        ssl_keyfile=str(security.KEY), ssl_certfile=str(security.CERT),
    )