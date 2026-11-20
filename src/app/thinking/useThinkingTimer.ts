"use client";

import { useEffect, useRef, useState } from "react";

export type ThinkingStatus = "idle" | "thinking" | "streaming" | "done";

export function useThinkingTimer(status: ThinkingStatus) {
  const [elapsedMs, setElapsedMs] = useState(0);
  const startedAtRef = useRef<number | null>(null);

  useEffect(() => {
    if (status === "idle") {
      startedAtRef.current = null;
      return;
    }

    if (status === "thinking" && startedAtRef.current === null) {
      startedAtRef.current = performance.now();
      setElapsedMs(0);
    }

    if (status === "done") {
      const startedAt = startedAtRef.current;
      if (startedAt === null) return;
      startedAtRef.current = null;
      const frozenElapsed = performance.now() - startedAt;
      const timeoutId = window.setTimeout(() => setElapsedMs(frozenElapsed), 0);
      return () => window.clearTimeout(timeoutId);
    }

    if (status !== "thinking" && status !== "streaming") return;

    const updateElapsed = () => {
      if (startedAtRef.current !== null) {
        setElapsedMs(performance.now() - startedAtRef.current);
      }
    };

    const initialTickId = window.setTimeout(updateElapsed, 0);
    const intervalId = window.setInterval(updateElapsed, 250);
    return () => {
      window.clearTimeout(initialTickId);
      window.clearInterval(intervalId);
    };
  }, [status]);

  const visibleElapsedMs = status === "idle" ? 0 : elapsedMs;
  const seconds = Math.floor(visibleElapsedMs / 1000);
  const label = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;

  return { elapsedMs: visibleElapsedMs, seconds, label };
}
