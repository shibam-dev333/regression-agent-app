# Regression Agent App

LLM-based regression-testing assistant for the OnBase SBPPA team, anchored on
Jira Xray Test Plan **[SBPPA-14690](https://hyland.atlassian.net/browse/SBPPA-14690)**
("26.1 - Regression - Workflow").

Standalone web app — runs outside VS Code. Same prompts and templates as the
in-editor Copilot agent at [`shibam-dev333/regression-agent`](https://github.com/shibam-dev333/regression-agent),
but with a browser UI, RAG over Confluence + MRG, scheduled jobs, and a real
compliance gate you can unit-test.

## Stack

| Layer | Choice |
|---|---|
| LLM | GitHub Models (`https://models.inference.ai.azure.com`) via OpenAI-compatible API |
| Agent | LangGraph + LangChain (Python) |
| RAG | Qdrant + LangChain vector store |
| Backend | FastAPI + WebSocket |
| Frontend | Next.js 15 (App Router) + TypeScript + Tailwind |
| Session state | SQLite (dev) / Postgres (prod) — LangGraph checkpointer |
| Auth | NextAuth (GitHub OAuth) + Atlassian OAuth 2.0 — *Phase 1+* |

## Architecture

End-state (Phase 6). Solid = wired today (Phase 0). Dashed = arrives in later phases.

```mermaid
flowchart LR
    subgraph Browser["Browser"]
        UI["Next.js 15 chat UI<br/>(React 19 + Tailwind)"]
    end

    subgraph Backend["FastAPI backend (Python)"]
        WS["WebSocket /api/chat"]
        REST["REST /api/status<br/>/api/coverage /api/health"]
        GRAPH["LangGraph FSM<br/>CONFIG → PREFLIGHT → FETCH-TEST<br/>→ SHOW-STEP → AWAIT-VERDICT<br/>→ BUG-DRAFT → APPROVAL → POSTED"]
        GATE["Compliance gate<br/>label / fixVersion / System Type / naming"]
        TOOLS["LangChain tools<br/>jira / xray / confluence / mrg / runlog"]
        SCHED["APScheduler<br/>nightly audit + EOD summary"]
        CKPT[("SQLite / Postgres<br/>LangGraph checkpointer<br/>+ audit log")]
    end

    subgraph RAG["RAG layer"]
        QDRANT[("Qdrant<br/>vector store")]
        INGEST["Ingest:<br/>Confluence + MRG + Xray<br/>+ past run-logs"]
    end

    subgraph External["External"]
        GHM["GitHub Models<br/>(OpenAI-compatible)"]
        JIRA["Atlassian Cloud<br/>Jira + Xray + Confluence"]
        SLACK["Slack / Teams<br/>EOD summary"]
        MRG["OnBase MRG"]
    end

    UI ==>|JSON frames| WS
    UI -.->|fetch| REST
    WS ==> GRAPH
    REST -.-> GRAPH
    GRAPH ==> GHM
    GRAPH -.-> TOOLS
    GRAPH -.-> CKPT
    TOOLS -.-> JIRA
    TOOLS -.-> QDRANT
    TOOLS -.-> GATE
    GATE -.->|writes| JIRA
    INGEST -.-> QDRANT
    JIRA -.-> INGEST
    MRG -.-> INGEST
    SCHED -.-> GRAPH
    SCHED -.-> SLACK

    classDef phase0 stroke:#10b981,stroke-width:2px;
    classDef later stroke:#64748b,stroke-dasharray: 4 3;
    class UI,WS,GRAPH,GHM phase0;
    class REST,GATE,TOOLS,SCHED,CKPT,QDRANT,INGEST,JIRA,SLACK,MRG later;
```

### Request path (Phase 0, today)

```mermaid
sequenceDiagram
    autonumber
    participant U as User (browser)
    participant FE as Next.js page
    participant BE as FastAPI /api/chat (WS)
    participant LG as LangGraph hello node
    participant GH as GitHub Models

    U->>FE: types message
    FE->>BE: {type:"user", text, history}
    BE->>LG: invoke(state.messages)
    LG->>GH: chat.completions (stream)
    GH-->>LG: token chunks
    LG-->>BE: AIMessageChunk
    BE-->>FE: {type:"token", text} (×N)
    BE-->>FE: {type:"done"}
    FE-->>U: streamed reply
```

### Drive loop (Phase 2, planned)

```mermaid
stateDiagram-v2
    [*] --> CONFIG
    CONFIG --> PREFLIGHT: build / env / tester confirmed
    PREFLIGHT --> FETCH_TEST: env healthy
    PREFLIGHT --> [*]: blocker (exception path)
    FETCH_TEST --> SHOW_STEP: next Xray step
    SHOW_STEP --> AWAIT_VERDICT
    AWAIT_VERDICT --> SHOW_STEP: pass / next
    AWAIT_VERDICT --> BUG_DRAFT: fail
    AWAIT_VERDICT --> FETCH_TEST: block (skip remaining)
    BUG_DRAFT --> APPROVAL: compliance gate passes
    BUG_DRAFT --> BUG_DRAFT: gate violation → fix fields
    APPROVAL --> POSTED: tester says "post"
    APPROVAL --> BUG_DRAFT: tester says "edit"
    POSTED --> FETCH_TEST: continue execution
    FETCH_TEST --> [*]: test plan exhausted
```

## Status

**Phase 0 — scaffold.** Frontend talks to backend, backend talks to GitHub Models, the
hello LangGraph node returns a streamed completion. No Jira/Xray, no RAG, no auth yet.

See [`docs/BUILD-PLAN.md`](docs/BUILD-PLAN.md) for the full phase plan (0 → 6).

## Quick start (dev)

Prereqs: Python 3.11+ with [uv](https://docs.astral.sh/uv/), Node 20+, pnpm 9+, Docker.

```powershell
# 1. Copy env template and fill in GITHUB_TOKEN (a PAT with models:read scope)
Copy-Item .env.example .env
# Edit .env -> set GITHUB_TOKEN

# 2. Start Qdrant + Postgres (used from Phase 1+; safe to start now)
docker compose up -d

# 3. Backend
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000

# 4. Frontend (new terminal)
cd frontend
pnpm install
pnpm dev
```

Open http://localhost:3000 → type a message → it streams back from GitHub Models via
the LangGraph hello node.

## Repo layout

```
regression-agent-app/
  backend/                       FastAPI + LangGraph + LangChain (Python, uv)
    app/
      main.py                    FastAPI entrypoint
      llm.py                     GitHub Models client (OpenAI-compatible)
      graph/
        drive.py                 LangGraph FSM (skeleton in Phase 0, real in Phase 2)
      routes/
        health.py
        chat.py                  WebSocket /api/chat for streaming
      compliance.py              Compliance gate (Phase 3)
      templates/                 Jinja2 templates lifted from sibling repo
    pyproject.toml
    uv.lock
  frontend/                      Next.js 15 + Tailwind (TypeScript, pnpm)
    src/
      app/
        layout.tsx
        page.tsx                 Chat surface (Phase 0)
        api/chat/route.ts        Edge proxy -> backend WS
      components/
      lib/
    package.json
    tsconfig.json
    tailwind.config.ts
  docs/
    BUILD-PLAN.md                Phase 0 -> 6 roadmap
  docker-compose.yml             Qdrant + Postgres for local dev
  .env.example
  .gitignore
  README.md                      this file
```

## License

Internal Hyland use.
