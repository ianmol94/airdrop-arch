import { sha256 } from "@noble/hashes/sha256";
import { bytesToHex } from "@noble/hashes/utils";

const CHUNK = 8 * 1024 * 1024;
const MAX_RETRIES = 8;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function putChunk(id, offset, blob, signal, onLoaded) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `/api/uploads/${id}?offset=${offset}`);
    xhr.upload.onprogress = (e) => onLoaded(e.loaded);
    xhr.onload = () =>
      xhr.status < 300
        ? resolve()
        : reject(Object.assign(new Error(`HTTP ${xhr.status}`), { status: xhr.status }));
    xhr.onerror = () => reject(new Error("network error"));
    xhr.onabort = () => reject(Object.assign(new Error("cancelled"), { cancelled: true }));
    signal.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(blob);
  });
}

const json = (body) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export async function uploadFile(file, { signal, onProgress, onNote }) {
  // Same file (name+size+mtime) reuses its session, so this survives page reloads
  const key = `up:${file.name}:${file.size}:${file.lastModified}`;
  let id = localStorage.getItem(key);
  let offset = 0;

  if (id) {
    const r = await fetch(`/api/uploads/${id}`);
    if (r.ok) offset = (await r.json()).offset;
    else id = null;
  }
  if (!id) {
    const r = await fetch("/api/uploads", json({ name: file.name, size: file.size }));
    if (!r.ok) throw new Error(`could not start upload (HTTP ${r.status})`);
    id = (await r.json()).id;
    localStorage.setItem(key, id);
    offset = 0;
  }

  // Resuming: re-hash the part already on the server so the final hash covers the whole file
  const hasher = sha256.create();
  if (offset > 0) onNote("resuming...");
  for (let p = 0; p < offset; p += CHUNK) {
    const end = Math.min(p + CHUNK, offset);
    hasher.update(new Uint8Array(await file.slice(p, end).arrayBuffer()));
  }

  while (offset < file.size) {
    const end = Math.min(offset + CHUNK, file.size);
    const blob = file.slice(offset, end);

    for (let attempt = 0; ; attempt++) {
      try {
        await putChunk(id, offset, blob, signal, (loaded) =>
          onProgress((offset + loaded) / file.size)
        );
        break;
      } catch (e) {
        if (e.cancelled) throw e;
        if (e.status >= 400 && e.status < 500) throw e;   // 401/403/409... retrying won't help
        if (attempt >= MAX_RETRIES) throw new Error(`gave up: ${e.message}`);
        onNote(`connection problem, retry ${attempt + 1}/${MAX_RETRIES}...`);
        await sleep(Math.min(2 ** attempt * 500, 10000));
      }
    }
    hasher.update(new Uint8Array(await blob.arrayBuffer()));
    offset = end;
    onNote("");
  }

  onNote("verifying...");
  const digest = bytesToHex(hasher.digest());
  const r = await fetch(`/api/uploads/${id}/complete`, json({ sha256: digest }));
  localStorage.removeItem(key);
  if (r.status === 422) throw new Error("checksum mismatch, please retry");
  if (!r.ok) throw new Error(`could not finish (HTTP ${r.status})`);
  return (await r.json()).name;
}