import { useEffect, useRef } from "react";

// Calls onEvent for server pushes, plus {kind:"connected"} on every (re)connect
export function useLive(onEvent) {
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    let ws, timer, closed = false, delay = 1000;
    const connect = () => {
      ws = new WebSocket(`wss://${location.host}/api/ws`);
      ws.onopen = () => { delay = 1000; handler.current({ kind: "connected" }); };
      ws.onmessage = (m) => handler.current(JSON.parse(m.data));
      ws.onclose = () => {
        if (closed) return;
        timer = setTimeout(connect, delay);
        delay = Math.min(delay * 2, 15000);
      };
    };
    connect();
    return () => { closed = true; clearTimeout(timer); ws && ws.close(); };
  }, []);
}