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
type Step = { idx: number; total: number; action: string; data: string; expected: string };
type TestRef = { key: string; title: string };
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

  // Drive-mode live state
  const [frame, setFrame] = useState<{ client: string; b64: string } | null>(null);
  const [currentStep, setCurrentStep] = useState<Step | null>(null);
  const [testList, setTestList] = useState<{ exec: string; tests: TestRef[] } | null>(null);
  const [routeInfo, setRouteInfo] = useState<
    { client: string; confidence: number; reason: string } | null
  >(null);

  const wsRef = useRef<WebSocket | null>(null);
  const assistantIdRef = useRef<string | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const appendAssistant = useCallback((text: string) => {
    setMessages((prev) => [...prev, { id: crypto.randomUUID(), role: "assistant", text }]);
  }, []);

  useEffect(() => {
    const ws = new WebSocket(`${WS_URL}/api/chat`);
    wsRef.current = ws;

    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);

    ws.onmessage = (ev) => {
      const f = JSON.parse(ev.data);
      const id = assistantIdRef.current;

      switch (f.type) {
        case "sources":
          if (id) setMessages((p) => p.map((m) => (m.id === id ? { ...m, sources: f.items } : m)));
          break;
        case "token":
          if (id)
            setMessages((p) => p.map((m) => (m.id === id ? { ...m, text: m.text + f.text } : m)));
          break;
        case "status":
          appendAssistant(`· ${f.text}`);
          break;
        case "issue":
          appendAssistant(`📋 ${f.key} [${f.issue_type}] — ${f.summary}  (${f.status})`);
          break;
        case "tests":
          setTestList({ exec: f.exec_key, tests: f.tests });
          appendAssistant(`Linked tests (${f.tests.length}). Type \`pick <KEY>\` or click below.`);
          break;
        case "steps":
          appendAssistant(`✓ ${f.count} step(s) scraped for ${f.test_key}`);
          break;
        case "route":
          setRouteInfo({ client: f.client, confidence: f.confidence, reason: f.reason });
          appendAssistant(`→ routed to ${f.client} (conf ${f.confidence.toFixed(2)}, ${f.reason})`);
          break;
        case "step":
          setCurrentStep({
            idx: f.idx,
            total: f.total,
            action: f.action,
            data: f.data,
            expected: f.expected,
          });
          break;
        case "frame":
          setFrame({ client: f.client, b64: f.jpg_b64 });
          break;
        case "done":
          assistantIdRef.current = null;
          setStreaming(false);
          break;
        case "error":
          appendAssistant(`⚠ ${f.text}`);
          assistantIdRef.current = null;
          setStreaming(false);
          break;
      }
    };

    return () => ws.close();
  }, [appendAssistant]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const send = useCallback(
    (override?: string) => {
      const text = (override ?? draft).trim();
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
    },
    [draft, messages, streaming],
  );

  const driveActive = !!(frame || currentStep || testList || routeInfo);

  return (
    <main className="mx-auto flex h-screen max-w-[1500px] gap-4 p-4">
      {/* Left: chat transcript */}
      <section className="flex w-[480px] flex-col">
        <header className="mb-3 flex items-center justify-between border-b border-white/10 pb-2">
          <h1 className="text-lg font-semibold">SBPPA Regression Agent</h1>
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

        <div className="flex-1 space-y-2 overflow-y-auto pr-1 text-sm">
          {messages.length === 0 && (
            <div className="rounded-lg border border-white/10 bg-white/5 p-3 text-muted">
              Type <code>run SBPPA-14878</code> to fetch a Jira test and start a live drive,
              or ask any question for a RAG-grounded answer with citations.
            </div>
          )}
          {messages.map((m) => (
            <div
              key={m.id}
              className={
                "rounded-lg border p-2 whitespace-pre-wrap " +
                (m.role === "user"
                  ? "border-accent/40 bg-accent/10"
                  : "border-white/10 bg-white/5")
              }
            >
              <div className="mb-1 text-[10px] uppercase tracking-wide text-muted">
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
          className="mt-3 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
        >
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={connected ? "run SBPPA-14878 | pass | fail <r> | stop" : "Waiting for backend…"}
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

        {currentStep && (
          <div className="mt-3 flex gap-2">
            <button
              onClick={() => send("pass")}
              disabled={streaming}
              className="flex-1 rounded-md bg-emerald-500/20 px-3 py-2 text-xs text-emerald-200 disabled:opacity-40"
            >
              PASS
            </button>
            <button
              onClick={() => send("fail")}
              disabled={streaming}
              className="flex-1 rounded-md bg-rose-500/20 px-3 py-2 text-xs text-rose-200 disabled:opacity-40"
            >
              FAIL
            </button>
            <button
              onClick={() => send("block needs env")}
              disabled={streaming}
              className="flex-1 rounded-md bg-amber-500/20 px-3 py-2 text-xs text-amber-200 disabled:opacity-40"
            >
              BLOCK
            </button>
            <button
              onClick={() => send("stop")}
              disabled={streaming}
              className="rounded-md bg-white/10 px-3 py-2 text-xs disabled:opacity-40"
            >
              STOP
            </button>
          </div>
        )}
      </section>

      {/* Right: live view */}
      <section className="flex-1 rounded-xl border border-white/10 bg-black/40 p-3">
        {!driveActive && (
          <div className="flex h-full items-center justify-center text-sm text-muted">
            Live view will appear here once you start a drive run.
          </div>
        )}
        {driveActive && (
          <div className="flex h-full flex-col gap-3">
            {routeInfo && (
              <div className="rounded-md border border-white/10 bg-white/5 px-3 py-2 text-xs">
                Client: <strong>{routeInfo.client}</strong> · confidence{" "}
                {routeInfo.confidence.toFixed(2)} · {routeInfo.reason}
              </div>
            )}
            {currentStep && (
              <div className="rounded-md border border-accent/40 bg-accent/10 px-3 py-2 text-xs">
                <div className="mb-1 font-semibold">
                  Step {currentStep.idx} / {currentStep.total}
                </div>
                <div>
                  <span className="text-muted">Action:</span> {currentStep.action}
                </div>
                {currentStep.data && (
                  <div>
                    <span className="text-muted">Data:</span> {currentStep.data}
                  </div>
                )}
                <div>
                  <span className="text-muted">Expect:</span> {currentStep.expected}
                </div>
              </div>
            )}
            {testList && !currentStep && (
              <div className="max-h-48 overflow-y-auto rounded-md border border-white/10 bg-white/5 px-3 py-2 text-xs">
                <div className="mb-1 font-semibold">Tests in {testList.exec}</div>
                {testList.tests.map((t) => (
                  <button
                    key={t.key}
                    onClick={() => send(`pick ${t.key}`)}
                    className="block w-full truncate text-left hover:text-accent"
                  >
                    {t.key} — {t.title}
                  </button>
                ))}
              </div>
            )}
            <div className="flex-1 overflow-hidden rounded-md border border-white/10 bg-black">
              {frame ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={`data:image/jpeg;base64,${frame.b64}`}
                  alt={`live ${frame.client}`}
                  className="h-full w-full object-contain"
                />
              ) : (
                <div className="flex h-full items-center justify-center text-xs text-muted">
                  waiting for first frame…
                </div>
              )}
            </div>
          </div>
        )}
      </section>
    </main>
  );
}
