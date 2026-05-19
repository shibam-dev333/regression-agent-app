# Build Plan — Phases 0 → 6

| Phase | Scope | Done when |
|---|---|---|
| **0 — Scaffold** | Monorepo, Docker compose (Qdrant + Postgres), FastAPI hello, LangGraph hello, GitHub Models client, Next.js chat page wired to backend WebSocket | Type a message in the browser → streamed response from GitHub Models |
| **1 — Read-only Jira** | Atlassian OAuth per-user, `/api/status`, `/api/coverage`, `/dashboard/*` pages | Manager sees SBPPA-14690 status in browser |
| **2 — Drive FSM** | LangGraph state machine (CONFIG → PREFLIGHT → FETCH-TEST → SHOW-STEP → AWAIT-VERDICT → BUG-DRAFT → APPROVAL → POSTED), human-in-the-loop checkpoints, SQLite checkpointer, WebSocket chat surface | Tester walks one Xray test end-to-end; verdicts go to a local run-log file only |
| **3 — Compliance gate + bug logger** | `compliance.gate()` as a tested function, `log bug` drafts a Jira issue, `post` writes it | First end-to-end real run against a 26.1 build |
| **4 — RAG** | Confluence + MRG + Xray + past run-logs ingested into Qdrant; LangChain `RetrievalQA` grounding | Agent cites Confluence/MRG URLs; no hallucinated field rules |
| **5 — Scheduled jobs** | APScheduler nightly audit + EOD summary; posts to Slack/Confluence | Managers stop asking for status |
| **6 — Triage + MRG-drift + Exception** | Tier-3 commands from the Copilot agent | Long tail |

## Phase 0 — what's in scope

- [x] Repo layout
- [x] Docker compose with Qdrant + Postgres (declared, not yet used)
- [x] `.env.example`
- [ ] Backend
  - [ ] FastAPI app
  - [ ] GitHub Models client (OpenAI-compatible)
  - [ ] LangGraph "hello" graph (one LLM node)
  - [ ] `/health` REST
  - [ ] `/api/chat` WebSocket (streams tokens from LangGraph)
- [ ] Frontend
  - [ ] Next.js 15 App Router
  - [ ] Tailwind 4
  - [ ] Chat page at `/`
  - [ ] WebSocket client wired to backend
- [ ] Smoke test
- [ ] Initial commit + push to GitHub

## Phase 0 — what's NOT in scope

- No Jira/Xray calls
- No RAG / Qdrant usage
- No auth
- No compliance gate logic
- No run-log file writes
- No scheduled jobs
