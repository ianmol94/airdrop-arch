import json
import os
import hashlib
import ipaddress
import secrets
import socket
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import qrcode
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

PORT = int(os.environ.get("AIRDROP_PORT", "8000"))
MDNS_HOST = f"airdrop-{socket.gethostname().split('.')[0].lower()}.local"

CONF = Path.home() / ".config" / "airdrop-arch"
CONF.mkdir(parents=True, exist_ok=True)
CONF.chmod(0o700)
CERT, KEY, DB = CONF / "cert.pem", CONF / "key.pem", CONF / "state.db"

def _load_config() -> dict:
    try:
        return json.loads((CONF / "config.json").read_text())
    except (FileNotFoundError, ValueError):
        return {}


_cfg = _load_config()
PUBLIC_HOST = _cfg.get("host")                       # trusted hostname, if configured
TLS_CERT = Path(_cfg["cert"]).expanduser() if "cert" in _cfg else CERT
TLS_KEY = Path(_cfg["key"]).expanduser() if "key" in _cfg else KEY

COOKIE = "airdrop_session"
PAIR_TTL = 300  # seconds

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
  id TEXT PRIMARY KEY, name TEXT, token_hash TEXT UNIQUE,
  created REAL, last_seen REAL);
CREATE TABLE IF NOT EXISTS pairing (token_hash TEXT PRIMARY KEY, expires REAL);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, name TEXT, size INTEGER);
"""


def _connect():
    con = sqlite3.connect(DB, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _init():
    con = _connect()
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    DB.chmod(0o600)


_init()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _run(sql: str, args=()):
    con = _connect()
    try:
        rows = con.execute(sql, args).fetchall()
        con.commit()
        return rows
    finally:
        con.close()

def add_event(kind: str, name: str, size: int):
    _run("INSERT INTO events (ts, kind, name, size) VALUES (?, ?, ?, ?)",
         (time.time(), kind, name, size))
    _run("DELETE FROM events WHERE ts < ?", (time.time() - 3600,))


def last_event_id() -> int:
    return _run("SELECT COALESCE(MAX(id), 0) AS m FROM events")[0]["m"]


def events_after(last: int):
    return [dict(r) for r in _run(
        "SELECT id, ts, kind, name, size FROM events WHERE id > ? ORDER BY id", (last,))]


# ---------- pairing and sessions ----------
def new_pairing_token() -> str:
    token = secrets.token_urlsafe(16)
    _run("DELETE FROM pairing WHERE expires < ?", (time.time(),))
    _run("INSERT INTO pairing VALUES (?, ?)", (_hash(token), time.time() + PAIR_TTL))
    return token


def redeem_pairing(token: str, name: str):
    """Atomically consume a pairing token. Returns a session token or None."""
    con = _connect()
    try:
        cur = con.execute(
            "DELETE FROM pairing WHERE token_hash = ? AND expires > ?",
            (_hash(token), time.time()),
        )
        if cur.rowcount != 1:
            return None
        session = secrets.token_urlsafe(32)
        now = time.time()
        con.execute(
            "INSERT INTO devices VALUES (?, ?, ?, ?, ?)",
            (secrets.token_hex(8), name.strip()[:60] or "Unnamed device", _hash(session), now, now),
        )
        con.commit()
        return session
    finally:
        con.close()


def device_for(session: str):
    rows = _run("SELECT id, name FROM devices WHERE token_hash = ?", (_hash(session),))
    if not rows:
        return None
    now = time.time()
    _run("UPDATE devices SET last_seen = ? WHERE id = ? AND last_seen < ?",
         (now, rows[0]["id"], now - 60))
    return {"id": rows[0]["id"], "name": rows[0]["name"]}


def list_devices():
    return [dict(r) for r in _run(
        "SELECT id, name, created, last_seen FROM devices ORDER BY last_seen DESC")]


def revoke_device(device_id: str) -> bool:
    con = _connect()
    try:
        cur = con.execute("DELETE FROM devices WHERE id = ?", (device_id,))
        con.commit()
        return cur.rowcount == 1
    finally:
        con.close()


# ---------- TLS certificate ----------
def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is actually sent
        return s.getsockname()[0]
    finally:
        s.close()


def ensure_cert(ip: str) -> str:
    """Create a self-signed cert covering `ip` unless a valid one exists. Returns its fingerprint."""
    if CERT.exists() and KEY.exists():
        cert = x509.load_pem_x509_certificate(CERT.read_bytes())
        try:
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            have = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
            have_dns = set(san.get_values_for_type(x509.DNSName))
        except x509.ExtensionNotFound:
            have, have_dns = set(), set()
        soon = datetime.now(timezone.utc) + timedelta(days=30)
        if ip in have and MDNS_HOST in have_dns and cert.not_valid_after_utc > soon:
            return cert.fingerprint(hashes.SHA256()).hex()

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "airdrop-arch")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([
            x509.IPAddress(ipaddress.ip_address(ip)),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            x509.DNSName("localhost"),
            x509.DNSName(MDNS_HOST),
        ]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    KEY.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    KEY.chmod(0o600)
    CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert.fingerprint(hashes.SHA256()).hex()


# ---------- pairing QR ----------
def print_pairing(ip: str, port: int):
    host = PUBLIC_HOST or ip
    url = f"https://{host}:{port}/#pair={new_pairing_token()}"
    qr = qrcode.QRCode()
    qr.add_data(url)
    qr.print_ascii(invert=True)
    print(f"Pair a device (single use, valid {PAIR_TTL // 60} min):\n{url}\n")