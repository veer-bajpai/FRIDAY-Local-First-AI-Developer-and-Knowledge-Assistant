"use client";

import { useState, type FormEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, Play, ShieldAlert, Square } from "lucide-react";
import { ThinkingIndicator } from "../thinking/ThinkingIndicator";
import { useAgent } from "./useAgent";
import "./agent.css";

function formatArguments(args: Record<string, unknown> | string) {
  return typeof args === "string" ? args : JSON.stringify(args, null, 2);
}

function DiffPreview({ text }: { text: string }) {
  if (!text)
    return <pre className="agent-preview">No diff preview is available.</pre>;
  return (
    <pre className="agent-preview">
      {text.split(/\r?\n/).map((line, index) => (
        <span
          className={
            line.startsWith("+")
              ? "agent-diff-add"
              : line.startsWith("-")
                ? "agent-diff-remove"
                : ""
          }
          key={`${index}-${line.slice(0, 20)}`}
        >
          {line}
          {"\n"}
        </span>
      ))}
    </pre>
  );
}

export default function AgentPanel() {
  const { items, todos, status, run, decide, stop, reset, notice, restoredRoot } =
    useAgent();
  // null until the user edits the field, so a resumed session's project path shows up
  const [editedRoot, setEditedRoot] = useState<string | null>(null);
  const projectRoot = editedRoot ?? restoredRoot;
  const [task, setTask] = useState("");
  const [model, setModel] = useState("");
  const busy = status === "thinking" || status === "streaming";

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void run(task, projectRoot, model);
  }

  return (
    <section className="agent-workspace" aria-label="Coding agent">
      <div className="agent-heading">
        <div>
          <span className="eyebrow">REPOSITORY WORKFLOW</span>
          <h2>Agent</h2>
          <p>
            Inspect a project, plan changes, and run approved edits from one
            place.
          </p>
        </div>
        <span className="agent-local-badge">
          <span /> Local model
        </span>
      </div>

      <form className="agent-task-form" onSubmit={submit}>
        <label className="agent-field">
          <span>Project path</span>
          <input
            value={projectRoot}
            onChange={(event) => setEditedRoot(event.target.value)}
            placeholder="C:\code\my-app or /home/me/my-app"
            autoComplete="off"
            spellCheck={false}
            required
            disabled={busy}
          />
        </label>
        <label className="agent-field">
          <span>Task</span>
          <textarea
            value={task}
            onChange={(event) => setTask(event.target.value)}
            placeholder="What should FRIDAY investigate or change?"
            rows={3}
            maxLength={4000}
            required
            disabled={busy}
          />
        </label>
        <div className="agent-form-footer">
          <label className="agent-model-field">
            <span>Model</span>
            <input
              value={model}
              onChange={(event) => setModel(event.target.value)}
              placeholder="AGENT_MODEL default"
              maxLength={120}
              disabled={busy}
            />
          </label>
          <div className="agent-form-actions">
            {busy ? (
              <button
                className="agent-stop-button"
                type="button"
                onClick={stop}
              >
                <Square size={13} fill="currentColor" /> Stop
              </button>
            ) : (
              <>
              {items.length > 0 && (
                <button
                  className="agent-stop-button"
                  type="button"
                  onClick={() => void reset()}
                  title="Close this session and start a new one"
                >
                  New session
                </button>
              )}
              <button
                className="agent-run-button"
                type="submit"
                disabled={!projectRoot.trim() || !task.trim()}
              >
                <Play size={14} fill="currentColor" /> Run task
              </button>
              </>
            )}
          </div>
        </div>
      </form>

      {notice && (
        <div className="agent-safety-note" role="status">
          <span>{notice}</span>
        </div>
      )}

      <div className="agent-safety-note" role="note">
        <ShieldAlert size={16} />
        <span>
          File changes and commands require approval. For untrusted
          repositories, run FRIDAY in Docker; the command denylist is not a
          sandbox.
        </span>
      </div>

      <div className="agent-progress">
        <ThinkingIndicator status={status} />
        {todos.length > 0 && (
          <ul className="agent-todos" aria-label="Agent task list">
            {todos.map((todo, index) => (
              <li
                className={`agent-todo is-${todo.status}`}
                key={`${index}-${todo.content}`}
              >
                <span aria-hidden="true">
                  {todo.status === "completed" ? <Check size={13} /> : "·"}
                </span>
                {todo.content}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="agent-results" aria-live="polite">
        {items.map((item) => {
          if (item.kind === "text") {
            return (
              <article className="agent-answer" key={item.key}>
                <div className="agent-item-label">FRIDAY</div>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {item.content}
                </ReactMarkdown>
              </article>
            );
          }

          if (item.kind === "error") {
            return (
              <div className="agent-error" key={item.key} role="alert">
                {item.message}
              </div>
            );
          }

          if (item.kind === "tool") {
            return (
              <details className="agent-tool" key={item.key}>
                <summary>
                  <strong>{item.name}</strong>
                  <span>{item.result ? "Complete" : "Running"}</span>
                </summary>
                <pre>{formatArguments(item.args)}</pre>
                {item.result && (
                  <pre className="agent-tool-result">{item.result}</pre>
                )}
              </details>
            );
          }

          return (
            <article className="agent-approval" key={item.key}>
              <div className="agent-approval-heading">
                <div>
                  <strong>Approve {item.name}?</strong>
                  <span>
                    {item.level === "exec"
                      ? "Command execution"
                      : "File change"}
                  </span>
                </div>
                {item.resolved && (
                  <span className="agent-approved">
                    <Check size={13} /> Submitted
                  </span>
                )}
              </div>
              <div className="agent-approval-args">
                {formatArguments(item.args)}
              </div>
              <DiffPreview text={item.preview} />
              {!item.resolved && (
                <div className="agent-approval-actions">
                  <button
                    type="button"
                    onClick={() => void decide(item.approvalId, "deny")}
                  >
                    Deny
                  </button>
                  <button
                    type="button"
                    onClick={() => void decide(item.approvalId, "allow_always")}
                  >
                    Always allow this tool
                  </button>
                  <button
                    className="agent-allow-button"
                    type="button"
                    onClick={() => void decide(item.approvalId, "allow_once")}
                  >
                    Allow once
                  </button>
                </div>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}

