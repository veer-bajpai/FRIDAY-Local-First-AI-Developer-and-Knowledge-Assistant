"use client";

import { useEffect, useRef, useState } from "react";
import "./thinking.css";

type ThinkingStatus = "idle" | "thinking" | "streaming" | "done";

const PHRASES = ["Thinking", "Working through it", "Checking the details"];

export function ThinkingIndicator({ status }: { status: ThinkingStatus }) {
  const startedAt = useRef<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [finalElapsed, setFinalElapsed] = useState<number | null>(null);
  const [phraseIndex, setPhraseIndex] = useState(0);

  useEffect(() => {
    if (status !== "thinking" && status !== "streaming") {
      if (startedAt.current !== null) {
        setFinalElapsed(Math.max(0, (performance.now() - startedAt.current) / 1000));
        startedAt.current = null;
      }
      return;
    }

    if (startedAt.current === null) {
      startedAt.current = performance.now();
      setElapsed(0);
      setFinalElapsed(null);
    }

    const timer = window.setInterval(() => {
      if (startedAt.current !== null) {
        setElapsed((performance.now() - startedAt.current) / 1000);
      }
    }, 250);
    const phraseTimer = window.setInterval(() => {
      setPhraseIndex((current) => (current + 1) % PHRASES.length);
    }, 2500);

    return () => {
      window.clearInterval(timer);
      window.clearInterval(phraseTimer);
    };
  }, [status]);

  if (status === "idle") return null;

  if (status === "done") {
    return (
      <div className="think-done" role="status">
        Thought for {Math.round(finalElapsed ?? elapsed)}s
      </div>
    );
  }

  return (
    <div className="think-row" role="status" aria-live="polite">
      <span className="think-orb-wrap" aria-hidden="true">
        <span className="think-orb" />
        <span className="think-orb-shadow" />
      </span>
      <span className="think-phrase-slot">
        <span className="think-phrase" key={phraseIndex}>
          {PHRASES[phraseIndex]}
        </span>
      </span>
      <span className="think-timer">{elapsed.toFixed(1)}s</span>
    </div>
  );
}
