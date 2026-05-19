"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Role = "user" | "assistant";
type Source = {
  n: number;
  source: string;
  path: string;
  title: string;
  score: number;
};
type Msg = { id: string; role: Role; text: string; sources?: Source[] };

const WS_URL =
  process.env.NEXT_PUBLIC_BACKEND_WS ?? "ws://localhost:8000";

function SourceChips({ sources }: { sources: Source[] }) {
  if (!sources?.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {sources.map((s) => {
        const isUrl = /^https?:\/\//i.test(s.path);
        const label = `[${s.n}] ${s.title}`;
        const className =
          "rounded-md border border-white/15 bg-white/5 px-1.5 py-0.5 text-[10px] text-muted hover:border-accent/40 hover:text-foreground transition-colors";
        return isUrl ? (
          <a
            key={s.n}
            href={s.path}
            target="_blank"
            rel="noreferrer noopener"
            className={className}
            title={`${s.source} · score ${s.score}\n${s.path}`}
          >
            {label}
          </a>
        ) : (
          <span
            key={s.n}
            className={className}
            title={`${s.source} · score ${s.score}\n${s.path}`}
          >
            {label}
          </span>
        );
      })}
    </div>
  );
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const assistantIdRef = useRef<string | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const ws = new WebSocket(`${WS_URL}/api/chat`);
    wsRef.current = ws;

    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);

    ws.onmessage = (ev) => {
      const frame = JSON.parse(ev.data) as
        | { type: "sources"; items: Source[] }
        | { type: "token"; text: string }
        | { type: "done" }
        | { type: "error"; text: string };

      const id = assistantIdRef.current;

      if (frame.type === "sources") {
        if (!id) return;
        setMessages((prev) =>
          prev.map((m) => (m.id === id ? { ...m, sources: frame.items } : m)),
        );
      } else if (frame.type === "token") {
        if (!id) return;
        setMessages((prev) =>
          prev.map((m) => (m.id === id ? { ...m, text: m.text + frame.text } : m)),
        );
      } else if (frame.type === "done") {
        assistantIdRef.current = null;
        setStreaming(false);
      } else if (frame.type === "error") {
        if (id) {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === id ? { ...m, text: `[error] ${frame.text}` } : m,
            ),
          );
        }
        assistantIdRef.current = null;
        setStreaming(false);
      }
    };

    return () => ws.close();
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const send = useCallback(() => {
    const text = draft.trim();
    if (!text || streaming) return;
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;

    const userMsg: Msg = { id: crypto.randomUUID(), role: "user", text };
    const assistantMsg: Msg = { id: crypto.randomUUID(), role: "assistant", text: "" };
    assistantIdRef.current = assistantMsg.id;

    const history = messages.map((m) => ({ role: m.role, text: m.text }));
    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setDraft("");
    setStreaming(true);

    ws.send(JSON.stringify({ type: "user", text, history }));
  }, [draft, messages, streaming]);

  return (
    <main className="mx-auto flex h-screen max-w-3xl flex-col p-4">
      <header className="mb-4 flex items-center justify-between border-b border-white/10 pb-3">
        <div>
          <h1 className="text-lg font-semibold">SBPPA Regression Agent</h1>
          <p className="text-xs text-muted">
            Phase 1 — RAG grounded. Drive loop arrives in Phase 3.
          </p>
        </div>
        <span
          className={
            "rounded-full px-2 py-0.5 text-xs " +
            (connected
              ? "bg-emerald-500/20 text-emerald-300"
              : "bg-rose-500/20 text-rose-300")
          }
        >
          {connected ? "connected" : "offline"}
        </span>
      </header>

      <div className="flex-1 space-y-3 overflow-y-auto pr-1">
        {messages.length === 0 && (
          <div className="rounded-lg border border-white/10 bg-white/5 p-4 text-sm text-muted">
            Ask me anything about OnBase 26.1 regression. Answers are grounded
            in the indexed corpus (Confluence + MRG + local docs) and cite
            their sources as [doc N] chips below each reply.
          </div>
        )}
        {messages.map((m) => (
          <div
            key={m.id}
            className={
              "rounded-lg border p-3 text-sm whitespace-pre-wrap " +
              (m.role === "user"
                ? "border-accent/40 bg-accent/10"
                : "border-white/10 bg-white/5")
            }
          >
            <div className="mb-1 text-xs uppercase tracking-wide text-muted">
              {m.role}
            </div>
            {m.text || (m.role === "assistant" ? "…" : "")}
            {m.role === "assistant" && m.sources ? (
              <SourceChips sources={m.sources} />
            ) : null}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <form
        className="mt-4 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={connected ? "Type a message…" : "Waiting for backend…"}
          disabled={!connected || streaming}
          className="flex-1 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm outline-none focus:border-accent/60"
        />
        <button
          type="submit"
          disabled={!connected || streaming || !draft.trim()}
          className="rounded-lg bg-accent/80 px-4 py-2 text-sm font-medium text-black disabled:opacity-40"
        >
          {streaming ? "…" : "Send"}
        </button>
      </form>
    </main>
  );
}
