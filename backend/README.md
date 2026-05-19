# Backend — FastAPI + LangGraph

```powershell
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/docs for the OpenAPI UI.

WebSocket endpoint: `ws://localhost:8000/api/chat`
