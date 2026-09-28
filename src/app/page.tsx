"use client";

import {
  isValidElement,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import {
  Activity,
  Archive,
  ArrowUp,
  Check,
  ChevronDown,
  Copy,
  FileText,
  FolderOpen,
  Grid2X2,
  ImagePlus,
  Menu,
  MessageSquare,
  MoreHorizontal,
  Paperclip,
  Plus,
  Search,
  Settings,
  Sparkles,
  Upload,
  X,
} from "lucide-react";
import "highlight.js/styles/github.css";

type View =
  | "Chat"
  | "Documents"
  | "Activity"
  | "History"
  | "My library"
  | "Collections"
  | "Settings";
type Message = {
  role: "user" | "assistant";
  content: string;
  created_at?: string;
};
type Document = {
  id: string;
  name: string;
  type: string;
  size: number;
  chunks: number;
  status: string;
  error?: string | null;
};
type Conversation = { id: string; title: string; updated_at: string };
type ActivityEvent = {
  id: number;
  type: string;
  title: string;
  detail?: string;
  status: string;
  created_at: string;
};
type Collection = { id: string; name: string; document_count: number };
type SettingsData = {
  model: string;
  embed_model: string;
  top_k: number;
  max_distance: number;
  temperature: number;
  system_prompt: string;
};

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers ?? {});
  const hasBody = init?.body !== undefined && !(init.body instanceof FormData);

  if (hasBody && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  if (!hasBody && headers.has("Content-Type")) {
    headers.delete("Content-Type");
  }

  const response = await fetch(`${API}${path}`, {
    ...init,
    headers,
  });
  if (!response.ok)
    throw new Error((await response.text()) || "Request failed");
  return response.json();
}

function formatSize(bytes: number) {
  if (!bytes) return "0 KB";
  return bytes > 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

const IMAGE_FILE_PATTERN = /\.(png|jpe?g|gif|webp|bmp|heic|heif)$/i;

function isImageFile(file: File) {
  return file.type.startsWith("image/") || IMAGE_FILE_PATTERN.test(file.name);
}

function getTextContent(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") {
    return String(node);
  }
  if (Array.isArray(node)) {
    return node.map(getTextContent).join("");
  }
  if (isValidElement<{ children?: ReactNode }>(node)) {
    return getTextContent(node.props.children);
  }
  return "";
}

function MarkdownCodeBlock({ children }: { children?: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const code = getTextContent(children).replace(/\n$/, "");

  async function copyCode() {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  return (
    <div className="markdown-code-block">
      <button
        className="copy-code-button"
        type="button"
        onClick={() => void copyCode()}
        aria-label={copied ? "Code copied" : "Copy code"}
        title={copied ? "Code copied" : "Copy code"}
      >
        {copied ? <Check size={14} /> : <Copy size={14} />}
        <span>{copied ? "Copied" : "Copy"}</span>
      </button>
      <pre>{children}</pre>
    </div>
  );
}

export default function Home() {
  const [view, setView] = useState<View>("Chat");
  const [mobileMenu, setMobileMenu] = useState(false);
  const [message, setMessage] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversationId, setConversationId] = useState<string>();
  const [documents, setDocuments] = useState<Document[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [settingsData, setSettingsData] = useState<SettingsData>();
  const [isSending, setIsSending] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const attachmentInput = useRef<HTMLInputElement>(null);
  const imageInput = useRef<HTMLInputElement>(null);

  async function loadDocuments() {
    setDocuments(await api<Document[]>("/api/documents"));
  }
  async function loadConversations() {
    setConversations(await api<Conversation[]>("/api/conversations"));
  }
  async function loadActivity() {
    setEvents(await api<ActivityEvent[]>("/api/activity"));
  }

  useEffect(() => {
    fileInput.current?.setAttribute("webkitdirectory", "");
    Promise.all([
      api<Document[]>("/api/documents"),
      api<Conversation[]>("/api/conversations"),
      api<ActivityEvent[]>("/api/activity"),
      api<Collection[]>("/api/collections"),
      api<SettingsData>("/api/settings"),
    ])
      .then(
        ([
          loadedDocuments,
          loadedConversations,
          loadedEvents,
          loadedCollections,
          loadedSettings,
        ]) => {
          setDocuments(loadedDocuments);
          setConversations(loadedConversations);
          setEvents(loadedEvents);
          setCollections(loadedCollections);
          setSettingsData(loadedSettings);
        },
      )
      .catch((reason) =>
        setError(
          reason instanceof Error ? reason.message : "Could not load workspace",
        ),
      );
  }, []);

  async function selectConversation(id: string) {
    try {
      const conversation = await api<{ id: string; messages: Message[] }>(
        `/api/conversations/${id}`,
      );
      setConversationId(conversation.id);
      setMessages(conversation.messages);
      setView("Chat");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load conversation",
      );
    }
  }

  async function newConversation() {
    try {
      const conversation = await api<{ id: string }>("/api/conversations", {
        method: "POST",
        body: JSON.stringify({ title: "New conversation" }),
      });
      setConversationId(conversation.id);
      setMessages([]);
      await loadConversations();
      setView("Chat");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not create conversation",
      );
    }
  }

  async function handleSend(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const question = message.trim();
    if (!question || isSending) return;
    setMessages((current) => [...current, { role: "user", content: question }]);
    setMessage("");
    setIsSending(true);
    setError("");
    try {
      const response = await fetch(`${API}/api/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          k: settingsData?.top_k ?? 3,
          conversation_id: conversationId,
        }),
      });
      if (!response.ok || !response.body)
        throw new Error("Chat request failed");
      const assistantIndex = messages.length + 1;
      let assistantContent = "";
      let lastPaint = 0;
      const paintAssistant = (force = false) => {
        const now = performance.now();
        if (!force && now - lastPaint < 50) return;
        lastPaint = now;
        const content = assistantContent;
        setMessages((current) =>
          current.map((item, index) =>
            index === assistantIndex ? { ...item, content } : item,
          ),
        );
      };
      setMessages((current) => [
        ...current,
        { role: "assistant", content: "" },
      ]);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let completedConversationId = conversationId;
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() ?? "";
        for (const event of events) {
          if (!event.startsWith("data: ")) continue;
          const payload = JSON.parse(event.slice(6)) as {
            token?: string;
            done?: boolean;
            conversation_id?: string;
            error?: string;
          };
          if (payload.error) throw new Error(payload.error);
          if (payload.conversation_id)
            completedConversationId = payload.conversation_id;
          if (payload.token) {
            assistantContent += payload.token;
            paintAssistant();
          }
        }
      }
      paintAssistant(true);
      setConversationId(completedConversationId);
      await loadConversations();
    } catch (reason) {
      const message =
        reason instanceof Error ? reason.message : "Chat request failed";
      setError(message);
      setMessages((current) => {
        const previous = [...current];
        const last = previous[previous.length - 1];
        if (last && last.role === "assistant" && last.content === "") {
          previous.splice(previous.length - 1, 1, {
            role: "assistant",
            content:
              "I could not reach the Friday API. Check that the backend is running.",
          });
          return previous;
        }
        return [
          ...previous,
          {
            role: "assistant",
            content:
              "I could not reach the Friday API. Check that the backend is running.",
          },
        ];
      });
    } finally {
      setIsSending(false);
    }
  }

  async function upload(files: File[]) {
    const supportedFiles = files.filter((file) =>
      /\.(pdf|md|txt|csv|png|jpe?g|gif|webp|bmp|heic|heif)$/i.test(file.name),
    );
    if (!supportedFiles.length) {
      setError("Choose PDF, Markdown, text, CSV, or image files.");
      return;
    }
    setIsUploading(true);
    try {
      await Promise.all(
        supportedFiles.map(async (file) => {
          const form = new FormData();
          form.append("file", file);
          const response = await fetch(`${API}/api/documents/upload`, {
            method: "POST",
            body: form,
          });
          if (!response.ok) throw new Error(`Upload failed for ${file.name}`);
        }),
      );
      await loadDocuments();
      await loadActivity();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Upload failed");
    } finally {
      setIsUploading(false);
    }
  }

  const navigation: [typeof MessageSquare, View][] = [
    [MessageSquare, "Chat"],
    [FileText, "Documents"],
    [Activity, "Activity"],
    [Archive, "History"],
    [FolderOpen, "My library"],
    [Grid2X2, "Collections"],
    [Settings, "Settings"],
  ];

  return (
    <main className="app-shell">
      <aside className={`sidebar ${mobileMenu ? "is-open" : ""}`}>
        <div className="brand-row">
          <div className="brand-mark">
            <Sparkles size={17} />
          </div>
          <span>friday</span>
          <button
            className="icon-button mobile-close"
            onClick={() => setMobileMenu(false)}
            aria-label="Close navigation"
          >
            <X size={18} />
          </button>
        </div>
        <button
          className="new-chat"
          onClick={newConversation}
          disabled={isSending}
        >
          <Plus size={16} /> New conversation <span>⌘ K</span>
        </button>
        <nav className="main-nav" aria-label="Primary navigation">
          {navigation.map(([Icon, label]) => (
            <button
              className={`nav-item ${view === label ? "selected" : ""}`}
              key={label}
              disabled={isSending}
              onClick={() => {
                setView(label);
                setMobileMenu(false);
              }}
            >
              <Icon size={17} /> {label}
            </button>
          ))}
        </nav>
        <div className="sidebar-spacer" />
        <div className="local-card">
          <div className="status-dot" />
          <div>
            <strong>Local engine</strong>
            <span>Ollama connected</span>
          </div>
          <MoreHorizontal size={16} />
        </div>
        <div className="profile">
          <div className="avatar">VB</div>
          <div>
            <strong>Veer Bajpai</strong>
            <span>Personal workspace</span>
          </div>
          <ChevronDown size={16} />
        </div>
      </aside>
      <section className="workspace">
        <header className="topbar">
          <button
            className="icon-button menu-trigger"
            onClick={() => setMobileMenu(true)}
            aria-label="Open navigation"
          >
            <Menu size={19} />
          </button>
          <div className="breadcrumbs">
            <span>Workspace</span>
            <span>/</span>
            <strong>{view}</strong>
          </div>
          <div className="top-actions">
            <button className="icon-button" aria-label="Search">
              <Search size={18} />
            </button>
            <div className="mini-avatar">VB</div>
          </div>
        </header>
        <div className="content-grid">
          <section className="chat-column" aria-busy={isUploading}>
            <div className="chat-head">
              <div>
                <div className="eyebrow">
                  PERSONAL ASSISTANT <span className="live-pill">LIVE</span>
                </div>
                <h1>{view === "Chat" ? "Friday workspace" : view}</h1>
              </div>
              <button className="icon-button" aria-label="More actions">
                <MoreHorizontal size={19} />
              </button>
            </div>
            {error && (
              <div className="error-banner" role="alert">
                {error}
                <button onClick={() => setError("")} aria-label="Dismiss error">
                  <X size={14} />
                </button>
              </div>
            )}
            {view === "Chat" ? (
              <Chat
                messages={messages}
                message={message}
                setMessage={setMessage}
                onSend={handleSend}
                isSending={isSending}
                onAttachFile={() => attachmentInput.current?.click()}
                onAttachImage={() => imageInput.current?.click()}
              />
            ) : (
              <WorkspaceView
                view={view}
                documents={documents}
                conversations={conversations}
                events={events}
                collections={collections}
                settingsData={settingsData}
                onSettingsSaved={setSettingsData}
                onCollectionsChanged={setCollections}
                onUpload={() => fileInput.current?.click()}
                onSelectConversation={selectConversation}
                onNewConversation={newConversation}
              />
            )}
            <input
              ref={fileInput}
              hidden
              type="file"
              accept=".pdf,.md,.txt,.csv,.png,.jpg,.jpeg,.gif,.webp,.bmp"
              multiple
              onChange={(event) => {
                const files = event.target.files
                  ? Array.from(event.target.files)
                  : [];
                if (files.length) void upload(files);
                event.target.value = "";
              }}
            />
            <input
              ref={attachmentInput}
              hidden
              type="file"
              accept=".pdf,.md,.txt,.csv,.png,.jpg,.jpeg,.gif,.webp,.bmp"
              multiple
              onChange={(event) => {
                const files = event.target.files
                  ? Array.from(event.target.files)
                  : [];
                if (files.length) void upload(files);
                event.target.value = "";
              }}
            />
            <input
              ref={imageInput}
              hidden
              type="file"
              accept="image/*,.png,.jpg,.jpeg,.gif,.webp,.bmp,.heic,.heif"
              capture="environment"
              multiple
              onChange={(event) => {
                const selectedFiles = event.target.files
                  ? Array.from(event.target.files)
                  : [];

                const imageFiles = selectedFiles.filter(isImageFile);
                const hasNonImage = selectedFiles.some(
                  (file) => !isImageFile(file),
                );

                if (
                  selectedFiles.length &&
                  (hasNonImage || imageFiles.length === 0)
                ) {
                  setError("Please choose photo files only.");
                  event.target.value = "";
                  return;
                }

                if (imageFiles.length) void upload(imageFiles);
                event.target.value = "";
              }}
            />
          </section>
          <ContextPanel
            documents={documents}
            settingsData={settingsData}
            onUpload={() => fileInput.current?.click()}
          />
        </div>
      </section>
    </main>
  );
}

function Chat({
  messages,
  message,
  setMessage,
  onSend,
  isSending,
  onAttachFile,
  onAttachImage,
}: {
  messages: Message[];
  message: string;
  setMessage: (value: string) => void;
  onSend: (event: React.FormEvent<HTMLFormElement>) => void;
  isSending: boolean;
  onAttachFile: () => void;
  onAttachImage: () => void;
}) {
  return (
    <>
      <div className="conversation-meta">
        <span>
          {messages.length
            ? `${messages.length} messages`
            : "Start a local conversation"}
        </span>
        <span className="line" />
        <span>Friday · local model</span>
      </div>
      <div className="messages">
        {messages.length === 0 && (
          <div className="empty-or-activity">
            <div className="activity-icon">
              <Sparkles size={23} />
            </div>
            <h2>Ask Friday anything</h2>
            <p>
              Your saved conversations and document sources will appear here.
            </p>
          </div>
        )}
        {messages.map((item, index) => (
          <div
            className={`message ${item.role === "user" ? "user-message" : "assistant-message"}`}
            key={`${item.role}-${index}`}
          >
            <div
              className={`message-avatar ${item.role === "user" ? "user-avatar" : "assistant-avatar"}`}
            >
              {item.role === "user" ? "VB" : <Sparkles size={15} />}
            </div>
            <div className="message-content">
              <div className="message-author">
                {item.role === "user" ? "You" : "Friday"}{" "}
                <span>{item.created_at ?? "now"}</span>
              </div>
              {item.role === "assistant" ? (
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  rehypePlugins={[rehypeHighlight]}
                  components={{ pre: MarkdownCodeBlock }}
                >
                  {item.content}
                </ReactMarkdown>
              ) : (
                <p>{item.content}</p>
              )}
            </div>
          </div>
        ))}
      </div>
      <div className="composer-wrap">
        <form className="composer" onSubmit={onSend}>
          <textarea
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
            placeholder="Message Friday..."
            rows={1}
            disabled={isSending}
          />
          <div className="composer-actions">
            <button
              type="button"
              className="icon-button"
              aria-label="Attach file"
              onClick={onAttachFile}
            >
              <Paperclip size={18} />
            </button>
            <button
              type="button"
              className="icon-button"
              aria-label="Attach image"
              onClick={onAttachImage}
            >
              <ImagePlus size={18} />
            </button>
            <button
              className="send-button"
              type="submit"
              aria-label="Send message"
              disabled={isSending || !message.trim()}
            >
              <ArrowUp size={17} />
            </button>
          </div>
        </form>
        <div className="composer-note">
          {isSending
            ? "Friday is thinking..."
            : "Friday can make mistakes. Check important info."}
        </div>
      </div>
    </>
  );
}

function WorkspaceView({
  view,
  documents,
  conversations,
  events,
  collections,
  settingsData,
  onSettingsSaved,
  onCollectionsChanged,
  onUpload,
  onSelectConversation,
  onNewConversation,
}: {
  view: View;
  documents: Document[];
  conversations: Conversation[];
  events: ActivityEvent[];
  collections: Collection[];
  settingsData?: SettingsData;
  onSettingsSaved: (settings: SettingsData) => void;
  onCollectionsChanged: (collections: Collection[]) => void;
  onUpload: () => void;
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
}) {
  if (view === "Documents" || view === "My library")
    return (
      <div className="data-list">
        <div className="panel-toolbar">
          <span>{documents.length} documents</span>
          <button className="primary-button" onClick={onUpload}>
            <Upload size={15} /> Add files / folder
          </button>
        </div>
        {documents.map((document) => (
          <div className="data-row" key={document.id}>
            <FileText size={18} />
            <div>
              <strong>{document.name}</strong>
              <span>
                {document.type} · {formatSize(document.size)} ·{" "}
                {document.chunks} chunks · {document.status}
              </span>
            </div>
          </div>
        ))}
        {!documents.length && (
          <Empty
            text="Add files to give Friday more context."
            action="Add a document"
            onClick={onUpload}
          />
        )}
      </div>
    );
  if (view === "History")
    return (
      <div className="data-list">
        {conversations.map((conversation) => (
          <button
            className="data-row data-row-button"
            key={conversation.id}
            onClick={() => onSelectConversation(conversation.id)}
          >
            <MessageSquare size={18} />
            <div>
              <strong>{conversation.title}</strong>
              <span>{new Date(conversation.updated_at).toLocaleString()}</span>
            </div>
          </button>
        ))}
        {!conversations.length && (
          <Empty
            text="Your recent conversations will appear here."
            action="Start a conversation"
            onClick={onNewConversation}
          />
        )}
      </div>
    );
  if (view === "Activity")
    return (
      <div className="data-list">
        {events.map((event) => (
          <div className="data-row" key={event.id}>
            <Activity size={18} />
            <div>
              <strong>{event.title}</strong>
              <span>
                {event.detail || event.type} ·{" "}
                {new Date(event.created_at).toLocaleString()}
              </span>
            </div>
          </div>
        ))}
        {!events.length && (
          <Empty
            text="Your local activity will appear here."
            action="Start a conversation"
            onClick={onNewConversation}
          />
        )}
      </div>
    );
  if (view === "Collections")
    return (
      <CollectionsView
        collections={collections}
        onChanged={onCollectionsChanged}
      />
    );
  if (view === "Settings")
    return (
      <SettingsView settingsData={settingsData} onSaved={onSettingsSaved} />
    );
  return (
    <div className="data-list">
      <div className="settings-card">
        <strong>{view}</strong>
        <span>
          Configure the local assistant from the API-backed workspace.
        </span>
      </div>
    </div>
  );
}

function CollectionsView({
  collections,
  onChanged,
}: {
  collections: Collection[];
  onChanged: (collections: Collection[]) => void;
}) {
  const [name, setName] = useState("");
  async function create() {
    if (!name.trim()) return;
    const collection = await api<Collection>("/api/collections", {
      method: "POST",
      body: JSON.stringify({ title: name.trim() }),
    });
    onChanged([...collections, collection]);
    setName("");
  }
  return (
    <div className="data-list">
      <div className="settings-card">
        <strong>Collections</strong>
        <span>Organize documents into focused retrieval groups.</span>
        <div className="inline-form">
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Collection name"
          />
          <button className="primary-button" onClick={() => void create()}>
            <Plus size={15} /> Create
          </button>
        </div>
      </div>
      {collections.map((collection) => (
        <div className="data-row" key={collection.id}>
          <Grid2X2 size={18} />
          <div>
            <strong>{collection.name}</strong>
            <span>{collection.document_count} documents</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function SettingsView({
  settingsData,
  onSaved,
}: {
  settingsData?: SettingsData;
  onSaved: (settings: SettingsData) => void;
}) {
  const [draft, setDraft] = useState<SettingsData | undefined>(
    () => settingsData,
  );
  const currentDraft = draft ?? settingsData;

  if (!currentDraft)
    return (
      <div className="data-list">
        <div className="settings-card">
          <span>Loading settings...</span>
        </div>
      </div>
    );

  async function save() {
    const saved = await api<SettingsData>("/api/settings", {
      method: "PUT",
      body: JSON.stringify(currentDraft),
    });
    onSaved(saved);
  }

  return (
    <div className="data-list">
      <div className="settings-card">
        <strong>Local assistant settings</strong>
        <label>
          Generation model
          <input
            value={currentDraft.model}
            onChange={(event) =>
              setDraft({ ...currentDraft, model: event.target.value })
            }
          />
        </label>
        <label>
          Embedding model
          <input
            value={currentDraft.embed_model}
            onChange={(event) =>
              setDraft({ ...currentDraft, embed_model: event.target.value })
            }
          />
        </label>
        <label>
          Retrieval count
          <input
            type="number"
            min="1"
            max="20"
            value={currentDraft.top_k}
            onChange={(event) =>
              setDraft({ ...currentDraft, top_k: Number(event.target.value) })
            }
          />
        </label>
        <label>
          Similarity limit
          <input
            type="number"
            min="0"
            max="2"
            step="0.1"
            value={currentDraft.max_distance}
            onChange={(event) =>
              setDraft({
                ...currentDraft,
                max_distance: Number(event.target.value),
              })
            }
          />
        </label>
        <label>
          Temperature
          <input
            type="number"
            min="0"
            max="2"
            step="0.1"
            value={currentDraft.temperature}
            onChange={(event) =>
              setDraft({
                ...currentDraft,
                temperature: Number(event.target.value),
              })
            }
          />
        </label>
        <label>
          System prompt
          <textarea
            rows={5}
            value={currentDraft.system_prompt}
            onChange={(event) =>
              setDraft({ ...currentDraft, system_prompt: event.target.value })
            }
          />
        </label>
        <button className="primary-button" onClick={() => void save()}>
          Save settings
        </button>
      </div>
    </div>
  );
}

function Empty({
  text,
  action,
  onClick,
}: {
  text: string;
  action: string;
  onClick: () => void;
}) {
  return (
    <div className="empty-or-activity">
      <div className="activity-icon">
        <Activity size={23} />
      </div>
      <h2>Nothing here yet</h2>
      <p>{text}</p>
      <button className="primary-button" onClick={onClick}>
        <Plus size={16} /> {action}
      </button>
    </div>
  );
}

function ContextPanel({
  documents,
  settingsData,
  onUpload,
}: {
  documents: Document[];
  settingsData?: SettingsData;
  onUpload: () => void;
}) {
  return (
    <aside className="context-panel">
      <div className="context-heading">
        <div>
          <span className="eyebrow">CONTEXT</span>
          <h2>Workspace details</h2>
        </div>
      </div>
      <div className="context-section">
        <div className="section-title">
          Attached sources <span>{documents.length}</span>
        </div>
        {documents.slice(0, 4).map((document) => (
          <div className="document-row" key={document.id}>
            <div className="doc-icon mint">
              <FileText size={16} />
            </div>
            <div className="doc-info">
              <strong>{document.name}</strong>
              <span>
                {document.type} · {formatSize(document.size)}
              </span>
            </div>
          </div>
        ))}
        <button className="upload-button" onClick={onUpload}>
          <Upload size={15} /> Add files / folder
        </button>
      </div>
      <div className="context-section">
        <div className="section-title">Search settings</div>
        <div className="setting-row">
          <span>Retrieval count</span>
          <strong>
            {settingsData ? `${settingsData.top_k} chunks` : "3 chunks"}
          </strong>
        </div>
        <div className="setting-row">
          <span>Similarity limit</span>
          <strong>
            {settingsData
              ? `${settingsData.max_distance.toFixed(1)} max`
              : "2.0 max"}
          </strong>
        </div>
        <div className="setting-row">
          <span>Storage</span>
          <strong>SQLite</strong>
        </div>
        <div className="setting-row">
          <span>Embeddings</span>
          <strong>Ollama</strong>
        </div>
      </div>
      <div className="context-footer">
        <span className="status-dot" /> Your data stays on this device
      </div>
    </aside>
  );
}
