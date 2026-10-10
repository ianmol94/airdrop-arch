import hashlib
import json
import os
import re
import time
import uuid

import asyncio
import socket
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import WebSocket, WebSocketDisconnect
from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

from fastapi import Response
from fastapi.responses import JSONResponse
from . import security
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

SHARE = Path(os.environ.get("AIRDROP_DIR", Path.home() / "Share"))
STATE = SHARE / ".uploads"
STATE.mkdir(parents=True, exist_ok=True)
ID_RE = re.compile(r"^[0-9a-f]{32}$")

@asynccontextmanager
async def lifespan(_: FastAPI):
    azc, info = None, None
    try:
        host = socket.gethostname().split(".")[0]
        info = ServiceInfo(
            "_airdrop-arch._tcp.local.",
            f"airdrop-arch on {host}._airdrop-arch._tcp.local.",
            addresses=[socket.inet_aton(security.lan_ip())],
            port=security.PORT,
            properties={"v": "1"},
            server=f"{security.MDNS_HOST}.",
        )
        z = AsyncZeroconf()
        await z.async_register_service(info)
        azc = z
    except Exception as e:          # mDNS is a convenience, never fatal
        print(f"mDNS disabled: {e}")
    yield
    if azc:
        await azc.async_unregister_service(info)
        await azc.async_close()


app = FastAPI(lifespan=lifespan)

PUBLIC_PATHS = {"/api/pair"}


@app.middleware("http")
async def require_pairing(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in PUBLIC_PATHS:
        session = request.cookies.get(security.COOKIE)
        device = security.device_for(session) if session else None
        if not device:
            return JSONResponse({"detail": "not paired"}, status_code=401)
        request.state.device = device
    return await call_next(request)


class PairBody(BaseModel):
    token: str
    name: str = "Unnamed device"


@app.post("/api/pair")
def pair(body: PairBody, response: Response):
    session = security.redeem_pairing(body.token, body.name)
    if not session:
        raise HTTPException(403, "invalid or expired pairing code")
    response.set_cookie(
        security.COOKIE, session,
        max_age=365 * 24 * 3600, httponly=True, secure=True, samesite="strict",
    )
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    return request.state.device


@app.get("/api/devices")
def devices(request: Request):
    mine = request.state.device["id"]
    return [{**d, "current": d["id"] == mine} for d in security.list_devices()]


@app.delete("/api/devices/{device_id}")
def revoke(device_id: str):
    if not security.revoke_device(device_id):
        raise HTTPException(404)
    return {"ok": True}


# ---------- helpers ----------
def safe_name(name: str) -> str:
    clean = Path(name).name
    if clean in ("", ".", ".."):
        raise HTTPException(400, "bad file name")
    return clean

def unique_path(name: str) -> Path:
    dest = SHARE / name
    stem, suffix, n = dest.stem, dest.suffix, 1
    while dest.exists():
        dest = SHARE / f"{stem} ({n}){suffix}"
        n += 1
    return dest

def upload_paths(uid: str):
    if not ID_RE.match(uid):
        raise HTTPException(400, "bad upload id")
    meta, part = STATE / f"{uid}.json", STATE / f"{uid}.part"
    if not meta.exists() or not part.exists():
        raise HTTPException(404, "unknown upload")
    return meta, part

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()

def cleanup_stale(max_age: int = 24 * 3600):
    now = time.time()
    for p in STATE.iterdir():
        if now - p.stat().st_mtime > max_age:
            p.unlink(missing_ok=True)

cleanup_stale()


# ---------- uploads ----------
class NewUpload(BaseModel):
    name: str
    size: int

class Complete(BaseModel):
    sha256: str

@app.post("/api/uploads")
def create_upload(body: NewUpload):
    if body.size < 0:
        raise HTTPException(400, "bad size")
    uid = uuid.uuid4().hex
    (STATE / f"{uid}.json").write_text(
        json.dumps({"name": safe_name(body.name), "size": body.size})
    )
    (STATE / f"{uid}.part").touch()
    return {"id": uid, "offset": 0}

@app.get("/api/uploads/{uid}")
def upload_status(uid: str):
    _, part = upload_paths(uid)
    return {"offset": part.stat().st_size}

@app.put("/api/uploads/{uid}")
async def upload_chunk(uid: str, offset: int, request: Request):
    meta, part = upload_paths(uid)
    size = json.loads(meta.read_text())["size"]
    if offset > part.stat().st_size:
        raise HTTPException(409, "gap in upload")
    written = offset
    with part.open("r+b") as f:
        f.truncate(offset)          # idempotent retries
        f.seek(offset)
        async for chunk in request.stream():
            written += len(chunk)
            if written > size:
                raise HTTPException(413, "more data than declared")
            f.write(chunk)
    return {"offset": written}

@app.post("/api/uploads/{uid}/complete")
def complete_upload(uid: str, body: Complete):
    meta, part = upload_paths(uid)
    info = json.loads(meta.read_text())
    if part.stat().st_size != info["size"]:
        raise HTTPException(409, "upload incomplete")
    if sha256_of(part) != body.sha256.lower():
        part.unlink(); meta.unlink()
        raise HTTPException(422, "checksum mismatch")
    dest = unique_path(info["name"])
    part.replace(dest)              # same filesystem, so this is an atomic rename
    security.add_event("received", dest.name, dest.stat().st_size)
    meta.unlink()
    return {"name": dest.name}

@app.delete("/api/uploads/{uid}")
def cancel_upload(uid: str):
    meta, part = upload_paths(uid)
    part.unlink(missing_ok=True); meta.unlink(missing_ok=True)
    return {"ok": True}


# ---------- files ----------
@app.get("/api/files")
def list_files():
    files = [p for p in SHARE.iterdir() if p.is_file() and not p.name.startswith(".")]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.name, "size": p.stat().st_size} for p in files]

@app.get("/api/files/{name}")
def download(name: str):
    path = SHARE / safe_name(name)
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, filename=path.name)

@app.get("/api/files/{name}/sha256")
def file_hash(name: str):
    path = SHARE / safe_name(name)
    if not path.is_file():
        raise HTTPException(404)
    return {"sha256": sha256_of(path)}

@app.websocket("/api/ws")
async def ws_events(websocket: WebSocket):
    # HTTP middleware doesn't run for WebSockets, so authenticate here
    session = websocket.cookies.get(security.COOKIE)
    origin = websocket.headers.get("origin")
    same_origin = not origin or urlparse(origin).netloc == websocket.headers.get("host")
    if not (session and security.device_for(session) and same_origin):
        await websocket.close(code=4401)
        return
    await websocket.accept()

    last = security.last_event_id()          # only events from now on

    async def pump():
        nonlocal last
        while True:
            for ev in security.events_after(last):
                await websocket.send_json(ev)
                last = ev["id"]
            await asyncio.sleep(1)

    task = asyncio.create_task(pump())
    try:
        while True:
            await websocket.receive_text()   # raises when the client disconnects
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()

# ---------- web ui (keep last) ----------
DIST = Path(os.environ.get("AIRDROP_WEB", Path(__file__).resolve().parents[2] / "web" / "dist"))
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="web")