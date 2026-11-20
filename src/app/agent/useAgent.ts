"use client";

import { useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type AgentStatus = "idle" | "thinking" | "streaming" | "done";
export type AgentTodo = { content: string; status: string };
type ToolArguments = Record<string, unknown> | string;

export type AgentItem =
  | { key: string; kind: "text"; content: string }
  | {
      key: string;
      kind: "tool";
      callId: string;
      name: string;
      args: ToolArguments;
      result?: string;
    }
  | {
      key: string;
      kind: "approval";
      approvalId: string;
      callId: string;
      name: string;
      level: string;
      args: ToolArguments;
      preview: string;
      resolved: boolean;
    }
  | { key: string; kind: "error"; message: string };

type AgentEvent =
  | { type: "turn_start"; model: string; mode: string }
  | { type: "token"; content: string }
  | { type: "tool_start"; id: string; tool: string; arguments: ToolArguments }
  | {
      type: "approval_required";
      id: string;
      tool: string;
      level: string;
      arguments: ToolArguments;
      preview: string;
    }
  | { type: "tool_result"; id: string; tool: string; result: string }
  | { type: "todos"; items?: AgentTodo[]; todos?: AgentTodo[] }
  | { type: "context_trimmed"; estimated_tokens: number }
  | { type: "error"; message: string }
  | {
      type: "done";
      mode: string;
      status?: string;
      todos?: AgentTodo[];
      undo_count: number;
    };

type HistoryEntry = {
  role: "user" | "assistant" | "tool";
  content: string;
  tool?: string;
  tool_calls?: string[];
};

type SessionInfo = {
  id: string;
  status?: string;
  last_error?: string | null;
  todos?: AgentTodo[];
  model?: string;
  model_note?: string | null;
};

const STORAGE_KEY = "friday.agent.session";

async function responseError(response: Response, fallback: string) {
  const text = await response.text().catch(() => "");
  try {
    const detail = (JSON.parse(text) as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
  } catch {
    /* not JSON */
  }
  return text || `${fallback} (${response.status})`;
}

function readStoredSession(): { id: string; root: string } | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as { id: string; root: string }) : null;
  } catch {
    return null;
  }
}

function storeSession(value: { id: string; root: string } | null) {
  try {
    if (value) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage unavailable: session simply won't survive a reload */
  }
}

function parseEventBlock(block: string): AgentEvent | null {
  const data = block
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  return data ? (JSON.parse(data) as AgentEvent) : null;
}

export function useAgent() {
  const [items, setItems] = useState<AgentItem[]>([]);
  const [todos, setTodos] = useState<AgentTodo[]>([]);
  const [status, setStatus] = useState<AgentStatus>("idle");
  const sessionRef = useRef("");
  const rootRef = useRef("");
  const [restoredRoot, setRestoredRoot] = useState("");
  const [notice, setNotice] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const itemSequence = useRef(0);

  function appendError(message: string) {
    itemSequence.current += 1;
    setItems((current) => [
      ...current,
      { key: `error-${itemSequence.current}`, kind: "error", message },
    ]);
  }

  function handleEvent(event: AgentEvent) {
    switch (event.type) {
      case "turn_start":
        setStatus("thinking");
        break;
      case "token":
        setStatus("streaming");
        setItems((current) => {
          const last = current.at(-1);
          if (last?.kind === "text") {
            return current.map((item, index) =>
              index === current.length - 1 && item.kind === "text"
                ? { ...item, content: item.content + event.content }
                : item,
            );
          }
          itemSequence.current += 1;
          return [
            ...current,
            {
              key: `text-${itemSequence.current}`,
              kind: "text",
              content: event.content,
            },
          ];
        });
        break;
      case "tool_start":
        itemSequence.current += 1;
        setItems((current) => [
          ...current,
          {
            key: `tool-${itemSequence.current}`,
            kind: "tool",
            callId: event.id,
            name: event.tool,
            args: event.arguments,
          },
        ]);
        break;
      case "approval_required":
        itemSequence.current += 1;
        setItems((current) => [
          ...current,
          {
            key: `approval-${itemSequence.current}`,
            kind: "approval",
            approvalId: event.id,
            callId: event.id,
            name: event.tool,
            level: event.level,
            args: event.arguments,
            preview: event.preview,
            resolved: false,
          },
        ]);
        break;
      case "tool_result":
        setItems((current) =>
          current.map((item) =>
            item.kind === "tool" && item.callId === event.id
              ? { ...item, result: event.result }
              : item,
          ),
        );
        break;
      case "todos":
        setTodos(event.items ?? event.todos ?? []);
        break;
      case "error":
        appendError(event.message);
        setStatus("done");
        break;
      case "done":
        setStatus("done");
        if (event.todos) setTodos(event.todos);
        if (event.status === "failed")
          setNotice("The task stopped with an error. Send another message to continue.");
        break;
    }
  }

  async function run(task: string, projectRoot: string, model = "") {
    if (!task.trim() || !projectRoot.trim() || abortRef.current) return;
    const controller = new AbortController();
    abortRef.current = controller;
    setNotice("");
    setStatus("thinking");
    itemSequence.current += 1;
    setItems((current) => [
      ...current,
      {
        key: `user-${itemSequence.current}`,
        kind: "text",
        content: `**You:** ${task.trim()}`,
      },
    ]);

    try {
      let sessionId = sessionRef.current;
      if (sessionId && rootRef.current !== projectRoot.trim()) {
        sessionId = ""; // different project: start a fresh session
      }
      if (!sessionId) {
        const created = await fetch(`${API}/api/agent/sessions`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            model: model.trim() || null,
            mode: "default",
            project_root: projectRoot.trim(),
          }),
          signal: controller.signal,
        });
        if (!created.ok) {
          throw new Error(
            await responseError(created, "Could not create an agent session"),
          );
        }
        const session = (await created.json()) as SessionInfo;
        sessionId = session.id;
        sessionRef.current = sessionId;
        rootRef.current = projectRoot.trim();
        storeSession({ id: sessionId, root: rootRef.current });
        if (session.model_note) setNotice(session.model_note);
      }

      const response = await fetch(
        `${API}/api/agent/sessions/${sessionId}/turns`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt: task.trim() }),
          signal: controller.signal,
        },
      );
      if (response.status === 404) {
        // the backend no longer knows this session (deleted or evicted): start over
        sessionRef.current = "";
        storeSession(null);
        throw new Error("That agent session expired. Run the task again to start a new session.");
      }
      if (!response.ok) {
        throw new Error(await responseError(response, "Agent request failed"));
      }
      if (!response.body)
        throw new Error("The agent response did not include a stream.");

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split(/\r?\n\r?\n/);
        buffer = blocks.pop() ?? "";
        for (const block of blocks) {
          const event = parseEventBlock(block);
          if (event) handleEvent(event);
        }
      }
      buffer += decoder.decode();
      const finalEvent = parseEventBlock(buffer);
      if (finalEvent) handleEvent(finalEvent);
    } catch (reason) {
      if (!(reason instanceof Error && reason.name === "AbortError")) {
        appendError(
          reason instanceof Error ? reason.message : "Agent request failed.",
        );
      }
    } finally {
      abortRef.current = null;
      setStatus("done");
    }
  }

  async function decide(
    approvalId: string,
    decision: "allow_once" | "allow_always" | "deny",
  ) {
    try {
      const mapped =
        decision === "allow_always"
          ? "always"
          : decision === "allow_once"
            ? "allow"
            : "deny";
      const response = await fetch(
        `${API}/api/agent/sessions/${sessionRef.current}/approvals/${approvalId}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision: mapped }),
        },
      );
      if (!response.ok)
        throw new Error(
          await responseError(response, "Approval could not be submitted"),
        );
      setItems((current) =>
        current.map((item) =>
          item.kind === "approval" && item.approvalId === approvalId
            ? { ...item, resolved: true }
            : item,
        ),
      );
    } catch (reason) {
      appendError(
        reason instanceof Error
          ? reason.message
          : "Approval could not be submitted.",
      );
    }
  }

  function stop() {
    abortRef.current?.abort();
    setStatus("done");
  }

  /** Close the current session on the backend and start fresh. */
  async function reset() {
    abortRef.current?.abort();
    const id = sessionRef.current;
    sessionRef.current = "";
    rootRef.current = "";
    storeSession(null);
    setItems([]);
    setTodos([]);
    setNotice("");
    setStatus("idle");
    if (id) {
      await fetch(`${API}/api/agent/sessions/${id}`, { method: "DELETE" }).catch(
        () => undefined,
      );
    }
  }

  // Sessions are persisted by the backend: reopen the last one instead of deleting it
  // when the panel unmounts, so switching tabs or reloading keeps the task and context.
  useEffect(() => {
    const stored = readStoredSession();
    if (!stored) return;
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch(
          `${API}/api/agent/sessions/${stored.id}/history?limit=200`,
        );
        if (response.status === 404) {
          storeSession(null);
          return;
        }
        if (!response.ok || cancelled) return;
        const data = (await response.json()) as SessionInfo & {
          history: HistoryEntry[];
        };
        sessionRef.current = stored.id;
        rootRef.current = stored.root;
        setRestoredRoot(stored.root);
        setTodos(data.todos ?? []);
        const restored: AgentItem[] = [];
        data.history.forEach((entry, index) => {
          if (entry.role === "user" && !entry.content.startsWith("[system note]")) {
            restored.push({ key: `h-${index}`, kind: "text", content: `**You:** ${entry.content}` });
          } else if (entry.role === "assistant" && entry.content.trim()) {
            restored.push({ key: `h-${index}`, kind: "text", content: entry.content });
          } else if (entry.role === "tool") {
            restored.push({
              key: `h-${index}`,
              kind: "tool",
              callId: `h-${index}`,
              name: entry.tool ?? "tool",
              args: {},
              result: entry.content,
            });
          }
        });
        itemSequence.current = restored.length + 1;
        setItems(restored);
        if (data.status === "interrupted")
          setNotice("The previous task was interrupted (nothing was replayed). Send a message to continue it.");
        else if (data.status === "failed" && data.last_error)
          setNotice(`Last task failed: ${data.last_error}`);
        else if (restored.length) setNotice("Resumed your previous agent session.");
      } catch {
        /* backend offline: the session stays stored for next time */
      }
    })();
    return () => {
      cancelled = true;
      abortRef.current?.abort();
    };
  }, []);

  return { items, todos, status, run, decide, stop, reset, notice, restoredRoot };
}

