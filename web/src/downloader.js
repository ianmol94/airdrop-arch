import { sha256 } from "@noble/hashes/sha256";
import { bytesToHex } from "@noble/hashes/utils";

export const IN_APP_LIMIT = 200 * 1024 * 1024;   // above this, use the browser's native download

export async function downloadVerified(name, onProgress) {
  const url = `/api/files/${encodeURIComponent(name)}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const total = Number(res.headers.get("Content-Length")) || 0;

  const hasher = sha256.create();
  const parts = [];
  let got = 0;
  const reader = res.body.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    hasher.update(value);
    parts.push(value);
    got += value.length;
    if (total) onProgress(got / total);
  }

  const expected = (await (await fetch(`${url}/sha256`)).json()).sha256;
  if (bytesToHex(hasher.digest()) !== expected) throw new Error("checksum mismatch, try again");

  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob(parts));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 60000);
}