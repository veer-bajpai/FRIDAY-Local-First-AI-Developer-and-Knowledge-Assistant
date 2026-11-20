"use client";

import { useEffect, useState } from "react";
import { Check, ChevronDown, X } from "lucide-react";
import { clock, type Step } from "./api";

const SEARCH_PHRASES = [
  "Reading your documents",
  "Checking your memory",
  "Connecting the dots",
  "Thinking it through",
];

function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(media.matches);
    media.addEventListener("change", update);
    const timeout = window.setTimeout(update, 0);
    return () => {
      media.removeEventListener("change", update);
      window.clearTimeout(timeout);
    };
  }, []);
  return reduced;
}

/** Live "working" card: rotating status words, timer, indeterminate progress and steps.
 *  When the run finishes it collapses to a one-line summary that can be expanded. */
export function WorkingStatus({
  steps,
  live,
  startedAt,
  elapsedMs,
  summary,
  phrase,
}: {
  steps: Step[];
  live: boolean;
  startedAt?: number;
  elapsedMs?: number;
  summary?: string;
  phrase?: string;
}) {
  const reduced = usePrefersReducedMotion();
  const [now, setNow] = useState(() => Date.now());
  const [phraseIndex, setPhraseIndex] = useState(0);
  const [open, setOpen] = useState(false); // finished: details hidden until opened
  const [collapsed, setCollapsed] = useState(false); // live: steps shown until collapsed

  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, [live]);

  useEffect(() => {
    if (!live || phrase || reduced) return;
    const rotate = window.setInterval(() => setPhraseIndex((i) => (i + 1) % SEARCH_PHRASES.length), 2400);
    return () => window.clearInterval(rotate);
  }, [live, phrase, reduced]);

  const elapsed = live && startedAt ? now - startedAt : elapsedMs ?? 0;
  const failed = steps.some((step) => step.state === "error");
  const words = phrase ?? SEARCH_PHRASES[phraseIndex];

  if (!live) {
    return (
      <div className={`work work-done${failed ? " is-failed" : ""}`}>
        <button type="button" className="work-summary" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          {failed ? <X size={13} /> : <Check size={13} />}
          <span>{summary ?? `Worked for ${Math.max(1, Math.round(elapsed / 1000))}s`}</span>
          {steps.length > 0 && <ChevronDown size={13} className={open ? "rot" : ""} />}
        </button>
        {open && <StepList steps={steps} />}
      </div>
    );
  }

  return (
    <div className="work" role="status" aria-live="polite">
      <div className="work-head">
        <span className="work-loader" aria-hidden="true" />
        <span className="work-phrase" key={words}>
          {words}
          <span className="work-dots" aria-hidden="true" />
        </span>
        <span className="work-timer">{clock(elapsed)}</span>
        <span className="work-progress" aria-hidden="true"><i /></span>
        <button type="button" className="work-toggle" onClick={() => setCollapsed((v) => !v)} aria-expanded={!collapsed}>
          {steps.length} step{steps.length === 1 ? "" : "s"}
          <ChevronDown size={13} className={collapsed ? "" : "rot"} />
        </button>
      </div>
      {!collapsed && <StepList steps={steps} />}
    </div>
  );
}

function StepList({ steps }: { steps: Step[] }) {
  return (
    <ol className="work-steps">
      {steps.map((step, index) => (
        <li key={`${index}-${step.label}`} className={`is-${step.state}`}>
          {step.state === "done" ? <Check size={13} /> : step.state === "error" ? <X size={13} /> : <span className="mini-spin" />}
          <span>{step.label}</span>
        </li>
      ))}
    </ol>
  );
}

