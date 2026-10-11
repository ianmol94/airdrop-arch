import { useEffect, useState } from "react";

function guessName() {
  const m = navigator.userAgent.match(/Android [\d.]+; ([^)]+)\)/);
  return m && m[1].length > 2 ? m[1] : "Android phone";
}

// "checking" | "paired" | "unpaired" | "failed"
export function usePairing() {
  const [state, setState] = useState("checking");
  useEffect(() => {
    (async () => {
      const m = location.hash.match(/^#pair=(.+)$/);
      if (m) {
        const guess = guessName();
        const name = prompt("Name this device:", guess) || guess;
        const r = await fetch("/api/pair", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: m[1], name }),
        });
        history.replaceState(null, "", location.pathname);  // drop the token from the URL
        if (!r.ok) return setState("failed");
      }
      const me = await fetch("/api/me");
      setState(me ? (me.ok ? "paired" : "unpaired") : "offline");
    })();
  }, []);
  return state;
}