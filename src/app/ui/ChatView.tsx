"use client";

import { isValidElement, useEffect, useRef, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import {
  ArrowUp,
  Brain,
  Check,
  Copy,
  Database,
  FileText,
  MessageSquare,
  Paperclip,
  RotateCcw,
  Search,
  Bot,
  Square,
} from "lucide-react";
import { LogoMark } from "./Logo";
import { WorkingStatus } from "./WorkingStatus";
import type { Memory, Message, SettingsData, Source } from "./api";

/* ---------------------------------------------------------------- composer */

export function Composer({
  value,
  onChange,
  onSubmit,
  onStop,
  busy,
  onAttach,
  documentsCount,
  memoriesCount,
  onOpenDocuments,
  onOpenMemory,
  models,
  settings,
  onModelChange,
  placeholder,
  hero = false,
  autoFocus = false,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onStop?: () => void;
  busy: boolean;
  onAttach: () => void;
  documentsCount: number;
  memoriesCount: number;
  onOpenDocuments: () => void;
  onOpenMemory: () => void;
  models: string[];
  settings?: SettingsData;
  onModelChange: (model: string) => void;
  placeholder: string;
  hero?: boolean;
  autoFocus?: boolean;
}) {
  const field = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const element = field.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 200)}px`;
  }, [value]);

  useEffect(() => {
    if (autoFocus) field.current?.focus();
  }, [autoFocus]);

  const modelOptions = settings && !models.includes(settings.model) ? [settings.model, ...models] : models;

  return (
    <form
      className={`composer${hero ? " hero" : ""}`}
      onSubmit={(event) => {
        event.preventDefault();
        if (!busy && value.trim()) onSubmit();
      }}
    >
      <div className="composer-input">
        {hero && <LogoMark size={26} />}
        <textarea
          ref={field}
          value={value}
          rows={hero ? 2 : 1}
          placeholder={placeholder}
          aria-label="Message FRIDAY"
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
        />
      </div>
      <div className="composer-tools">
        <button type="button" className="chip" onClick={onAttach} title="Add files to your knowledge base">
          <Paperclip size={14} /> Attach
        </button>
        <button type="button" className="chip is-on" onClick={onOpenDocuments}
          title={`Answers search your ${documentsCount} indexed documents`}>
          <Database size={14} /> Documents <i>{documentsCount}</i>
        </button>
        <button type="button" className="chip" onClick={onOpenMemory} title="Relevant memories are added automatically">
          <Brain size={14} /> Memory <i>{memoriesCount}</i>
        </button>
        <span className="grow" />
        {modelOptions.length > 0 && (
          <select
            className="model-select hide-sm"
            aria-label="Chat model"
            value={settings?.model ?? modelOptions[0]}
            onChange={(event) => onModelChange(event.target.value)}
          >
            {modelOptions.map((model) => (
              <option key={model} value={model}>
                {model}
              </option>
            ))}
          </select>
        )}
        {busy ? (
          <button type="button" className="send stop" onClick={onStop} aria-label="Stop response" title="Stop">
            <Square size={13} fill="currentColor" />
          </button>
        ) : (
          <button type="submit" className="send" aria-label="Send" disabled={!value.trim()}>
            <ArrowUp size={17} />
          </button>
        )}
      </div>
    </form>
  );
}

/* ---------------------------------------------------------------- markdown */

function textOf(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return textOf(node.props.children);
  return "";
}

function CodeBlock({ children }: { children?: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const code = textOf(children).replace(/\n$/, "");
  const child = Array.isArray(children) ? children[0] : children;
  const className = isValidElement<{ className?: string }>(child) ? child.props.className ?? "" : "";
  const language = /language-([\w-]+)/.exec(className)?.[1] ?? "code";

  return (
    <div className="code-block">
      <div className="code-block-head">
        <span>{language}</span>
        <button
          type="button"
          onClick={async () => {
            await navigator.clipboard.writeText(code);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          }}
        >
          {copied ? <Check size={13} /> : <Copy size={13} />} {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre>{children}</pre>
    </div>
  );
}

function Answer({ text, streaming }: { text: string; streaming: boolean }) {
  return (
    <div className="answer">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]} components={{ pre: CodeBlock }}>
        {text}
      </ReactMarkdown>
      {streaming && <span className="caret" aria-hidden="true" />}
    </div>
  );
}

/* ---------------------------------------------------------------- thread */

export function docKind(name: string, fileType?: string) {
  const ext = (fileType || name.split(".").pop() || "").toLowerCase();
  if (ext === "pdf") return { label: "PDF", cls: "pdf" };
  if (["png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff", "image"].includes(ext)) return { label: "IMG", cls: "img" };
  if (["docx", "pptx", "xlsx"].includes(ext)) return { label: ext.slice(0, 3).toUpperCase(), cls: "office" };
  return { label: ext.slice(0, 3).toUpperCase() || "TXT", cls: "" };
}

function sourceLocation(source: Source) {
  return source.page ? `p.${source.page}` : `chunk ${source.chunk}`;
}

function summary(message: Message) {
  const seconds = Math.max(1, Math.round((message.elapsedMs ?? 0) / 1000));
  if (message.failed) return `Stopped after ${seconds}s`;
  const parts = [`Answered in ${seconds}s`];
  if (message.sources?.length) parts.push(`${message.sources.length} source${message.sources.length === 1 ? "" : "s"}`);
  if (message.memories?.length) parts.push(`${message.memories.length} memor${message.memories.length === 1 ? "y" : "ies"}`);
  return parts.join(" · ");
}

export function Thread({
  messages,
  live,
  liveStartedAt,
  livePhrase,
  onRetry,
  onStarter,
}: {
  messages: Message[];
  live: boolean;
  liveStartedAt?: number;
  livePhrase?: string;
  onRetry: (question: string) => void;
  onStarter: (text: string) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState<number | null>(null);
  const lastLength = messages.length ? messages[messages.length - 1].content.length : 0;

  useEffect(() => {
    const element = scroller.current;
    if (!element) return;
    const nearBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 160;
    if (nearBottom || live) element.scrollTo({ top: element.scrollHeight });
  }, [messages.length, lastLength, live]);

  if (!messages.length) {
    const starters = [
      { icon: FileText, title: "Summarise a document", text: "Summarise the key points of my most recent document." },
      { icon: Search, title: "Search my knowledge", text: "What do my documents say about " },
      { icon: Bot, title: "Plan a coding task", text: "Help me plan the steps to " },
      { icon: Brain, title: "Remember something", text: "remember: " },
    ];
    return (
      <div className="thread" ref={scroller}>
        <div className="chat-empty">
          <LogoMark size={44} />
          <h2>What can I help with?</h2>
          <p>Answers use your local documents and memory. Nothing leaves this device.</p>
          <div className="starters">
            {starters.map(({ icon: Icon, title, text }) => (
              <button type="button" className="starter" key={title} onClick={() => onStarter(text)}>
                <Icon size={16} color="var(--accent)" />
                <b>{title}</b>
                <small>{text.endsWith(" ") ? `${text}…` : text}</small>
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="thread" ref={scroller} aria-live="polite">
      <div className="thread-inner">
        {messages.map((message, index) => {
          if (message.role === "user") {
            return (
              <div className="msg-user" key={index}>
                <div className="bubble">{message.content}</div>
              </div>
            );
          }
          const isLive = live && index === messages.length - 1;
          const question = messages[index - 1]?.role === "user" ? messages[index - 1].content : "";
          return (
            <div className="msg-ai" key={index}>
              <div className="ai-head">
                <LogoMark size={20} />
                FRIDAY
                {message.model && <small>{message.model}</small>}
              </div>
              {message.steps && message.steps.length > 0 && (
                <WorkingStatus
                  steps={message.steps}
                  live={isLive}
                  startedAt={isLive ? liveStartedAt : undefined}
                  elapsedMs={message.elapsedMs}
                  phrase={isLive ? livePhrase : undefined}
                  summary={isLive ? undefined : summary(message)}
                />
              )}
              {message.content && <Answer text={message.content} streaming={isLive} />}
              {message.note && <p className="msg-note">{message.note}</p>}
              {!!message.sources?.length && (
                <div className="sources" aria-label="Sources">
                  {message.sources.map((source, sourceIndex) => (
                    <span className="src" key={`${source.title}-${source.chunk}-${sourceIndex}`} title={source.title}>
                      <i>{sourceIndex + 1}</i>
                      <FileText size={13} />
                      <b>{source.title}</b>
                      <small>{sourceLocation(source)}</small>
                    </span>
                  ))}
                </div>
              )}
              {!isLive && message.content && (
                <div className="msg-actions">
                  <button
                    type="button"
                    aria-label="Copy answer"
                    title="Copy"
                    onClick={async () => {
                      await navigator.clipboard.writeText(message.content);
                      setCopied(index);
                      window.setTimeout(() => setCopied(null), 1500);
                    }}
                  >
                    {copied === index ? <Check size={15} /> : <Copy size={15} />}
                  </button>
                  {question && index === messages.length - 1 && (
                    <button type="button" aria-label="Ask again" title="Ask again" onClick={() => onRetry(question)}>
                      <RotateCcw size={15} />
                    </button>
                  )}
                  {message.created_at && (
                    <span>{new Date(message.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- context rail */

export function ContextRail({
  message,
  settings,
  onOpenMemory,
}: {
  message?: Message;
  settings?: SettingsData;
  onOpenMemory: () => void;
}) {
  const sources = message?.sources ?? [];
  const memories: Memory[] = message?.memories ?? [];
  return (
    <aside className="rail" aria-label="Answer context">
      <div className="card">
        <div className="card-head">Sources in this answer</div>
        {sources.length ? (
          sources.map((source, index) => {
            const kind = docKind(source.title, source.file_type);
            return (
              <div className="doc-row" key={`${source.title}-${index}`}>
                <span className={`doc-icon ${kind.cls}`}>{kind.label}</span>
                <div>
                  <b title={source.title}>{source.title}</b>
                  <small>
                    {sourceLocation(source)}
                    {typeof source.score === "number" ? ` · ${Math.round(source.score * 100)}% match` : ""}
                  </small>
                </div>
              </div>
            );
          })
        ) : (
          <div className="empty-mini">Sources appear here when an answer uses your documents.</div>
        )}
      </div>
      <div className="card">
        <div className="card-head">
          Memory used
          <button type="button" className="link" onClick={onOpenMemory}>Manage</button>
        </div>
        {memories.length ? (
          memories.map((memory) => <div className="memo" key={memory.id}>{memory.content}</div>)
        ) : (
          <div className="empty-mini">
            No memories were relevant. Type <b>remember:</b> followed by a fact to save one.
          </div>
        )}
      </div>
      <div className="card">
        <div className="card-head">This run</div>
        <div className="kv"><span>Model</span><b>{message?.model ?? settings?.model ?? "—"}</b></div>
        <div className="kv"><span>Retrieval</span><b>Hybrid · top {settings?.top_k ?? 3}</b></div>
        <div className="kv"><span>Temperature</span><b>{settings?.temperature ?? "—"}</b></div>
        <div className="kv">
          <span>Time</span>
          <b>
            {message?.steps?.some((step) => step.state === "live")
              ? "running…"
              : message?.elapsedMs
                ? `${(message.elapsedMs / 1000).toFixed(1)}s`
                : "—"}
          </b>
        </div>
      </div>
    </aside>
  );
}

export function ChatHeader({ title, online }: { title: string; online: boolean | null }) {
  return (
    <div className="chat-head">
      <MessageSquare size={16} color="var(--muted)" />
      <span>{title}</span>
      <span className={`pill${online === false ? " error" : ""}`}>
        <i /> {online === false ? "Ollama offline" : "Local · private"}
      </span>
    </div>
  );
}
