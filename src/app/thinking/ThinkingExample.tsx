"use client";

import { useState } from "react";
import { ThinkingIndicator } from "./ThinkingIndicator";
import { type ThinkingStatus } from "./useThinkingTimer";

const wait = (milliseconds: number) =>
  new Promise((resolve) => window.setTimeout(resolve, milliseconds));

export function ThinkingExample() {
  const [status, setStatus] = useState<ThinkingStatus>("idle");
  const [text, setText] = useState("");

  async function runFakeStream() {
    setText("");
    setStatus("thinking");
    await wait(1200);
    setStatus("streaming");
    for (const word of "Here is a short answer streamed word by word.".split(
      " ",
    )) {
      setText((current) => `${current}${word} `);
      await wait(120);
    }
    setStatus("done");
  }

  return (
    <div>
      <button type="button" onClick={() => void runFakeStream()}>
        Run fake stream
      </button>
      <ThinkingIndicator status={status} />
      <p>{text}</p>
    </div>
  );
}
