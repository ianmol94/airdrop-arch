import { useEffect, useRef, useState } from "react";
import { uploadFile } from "./uploader";
import { usePairing } from "./pairing";
import { useLive } from "./live";
import { downloadVerified, IN_APP_LIMIT } from "./downloader";

const fmt = (n) =>
  n > 1e9 ? (n / 1e9).toFixed(2) + " GB" : n > 1e6 ? (n / 1e6).toFixed(1) + " MB" : (n / 1e3).toFixed(0) + " KB";
const newId = () => `${Date.now()}-${Math.random().toString(36).slice(2)}`;

function Main() {
  const [files, setFiles] = useState([]);
  const [queue, setQueue] = useState([]);
  const [incoming, setIncoming] = useState(null);
  const aborts = useRef({});

  const refresh = () => fetch("/api/files").then((r) => r.json()).then(setFiles);
  const patch = (id, p) => setQueue((q) => q.map((i) => (i.id === id ? { ...i, ...p } : i)));

  useEffect(() => { refresh(); }, []);

  useLive((ev) => {
    if (ev.kind === "connected" || ev.kind === "received") refresh();
    if (ev.kind === "sent") { refresh(); setIncoming(ev); }
  });

  // Worker: one upload at a time, in order
  useEffect(() => {
    if (queue.some((i) => i.status === "uploading")) return;
    const next = queue.find((i) => i.status === "queued");
    if (!next) return;
    const ctrl = new AbortController();
    aborts.current[next.id] = ctrl;
    patch(next.id, { status: "uploading", error: "" });
    uploadFile(next.file, {
      signal: ctrl.signal,
      onProgress: (p) => patch(next.id, { progress: p }),
      onNote: (note) => patch(next.id, { note }),
    })
      .then(() => { patch(next.id, { status: "done", progress: 1, note: "" }); refresh(); })
      .catch((e) =>
        patch(next.id, e.cancelled ? { status: "paused", note: "" } : { status: "error", error: e.message })
      );
  }, [queue]);

  const enqueue = (list) =>
    setQueue((q) => [
      ...q,
      ...list.map((file) => ({ id: newId(), file, status: "queued", progress: 0, note: "", error: "" })),
    ]);

  function onPick(e) {
    enqueue(Array.from(e.target.files));
    e.target.value = "";
  }

  const pause = (i) =>
    i.status === "uploading" ? aborts.current[i.id]?.abort() : patch(i.id, { status: "paused" });
  const resume = (i) => patch(i.id, { status: "queued", error: "" });

  return (
    <main
      style={{ maxWidth: 560, margin: "0 auto", padding: 16, fontFamily: "sans-serif", minHeight: "100vh" }}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        enqueue(Array.from(e.dataTransfer.files));
      }}
    >
      <h2>Send to laptop</h2>
      <input type="file" multiple onChange={onPick} />

      {queue.map((i) => (
        <div key={i.id} style={{ margin: "12px 0" }}>
          <div>
            <small>
              {i.file.name} ({fmt(i.file.size)}) · {i.status} {Math.round(i.progress * 100)}% {i.note}
            </small>
          </div>
          <progress value={i.progress} max="1" style={{ width: "100%" }} />
          {i.error && <div style={{ color: "crimson" }}>{i.error}</div>}
          {(i.status === "uploading" || i.status === "queued") && <button onClick={() => pause(i)}>Pause</button>}
          {(i.status === "paused" || i.status === "error") && <button onClick={() => resume(i)}>Resume</button>}
        </div>
      ))}

      {incoming && (
        <div style={{ background: "#e8f5e9", padding: 8, margin: "8px 0" }}>
          Laptop sent <b>{incoming.name}</b> ({fmt(incoming.size)}), shown below.{" "}
          <button onClick={() => setIncoming(null)}>Dismiss</button>
        </div>
      )}

      <h2>On laptop</h2>
      <button onClick={refresh}>Refresh</button>
      <ul>
        {files.map((f) => (
          <FileRow key={f.name} f={f} flash={incoming?.name === f.name} />
        ))}
      </ul>

      <Devices />
    </main>
  );
}

function FileRow({ f, flash }) {
  const [p, setP] = useState(null);
  const [err, setErr] = useState("");

  async function get() {
    setErr("");
    setP(0);
    try { await downloadVerified(f.name, setP); }
    catch (e) { setErr(e.message); }
    setP(null);
  }

  return (
    <li style={{ background: flash ? "#fff8d6" : "none", padding: 4 }}>
      {f.name} ({fmt(f.size)}){" "}
      {f.size <= IN_APP_LIMIT ? (
        <button onClick={get} disabled={p !== null}>
          {p === null ? "Download" : `${Math.round(p * 100)}%`}
        </button>
      ) : (
        <a href={`/api/files/${encodeURIComponent(f.name)}`}>Download</a>
      )}
      {err && <span style={{ color: "crimson" }}> {err}</span>}
    </li>
  );
}

function Devices() {
  const [list, setList] = useState([]);
  const load = () => fetch("/api/devices").then((r) => r.json()).then(setList);
  useEffect(() => { load(); }, []);

  async function revoke(d) {
    if (!confirm(`Revoke "${d.name}"?`)) return;
    await fetch(`/api/devices/${d.id}`, { method: "DELETE" });
    if (d.current) location.reload();
    else load();
  }

  return (
    <>
      <h2>Paired devices</h2>
      <ul>
        {list.map((d) => (
          <li key={d.id}>
            {d.name}{d.current ? " (this device)" : ""} · seen {new Date(d.last_seen * 1000).toLocaleString()}{" "}
            <button onClick={() => revoke(d)}>Revoke</button>
          </li>
        ))}
      </ul>
    </>
  );
}

export default function App() {
  const state = usePairing();
  if (state === "checking") return <p style={{ padding: 16 }}>Loading...</p>;
  if (state === "offline")
    return (
      <main style={{ maxWidth: 560, margin: "0 auto", padding: 16, fontFamily: "sans-serif" }}>
        <h2>Laptop not reachable</h2>
        <p>Check that the laptop is on, the airdrop-arch service is running, and Tailscale is connected.</p>
        <button onClick={() => location.reload()}>Retry</button>
      </main>
  );
  if (state !== "paired")
    return (
      <main style={{ maxWidth: 560, margin: "0 auto", padding: 16, fontFamily: "sans-serif" }}>
        <h2>Not paired</h2>
        <p>
          {state === "failed" ? "That pairing code was invalid or expired. " : ""}
          On the laptop, run <code>python pair.py</code> and scan the QR code with your camera.
        </p>
      </main>
    );
  return <Main />;
}