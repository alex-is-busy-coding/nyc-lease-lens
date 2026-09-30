from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from nyc_lease_lens import sessions
from nyc_lease_lens.agent import run_agent
from nyc_lease_lens.schemas import ChatRequest, ChatResponse

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="NYC Lease Lens")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    session_id, messages = sessions.get_or_create(request.session_id)

    # Append user's message to the context
    messages += [{"role": "user", "content": request.message}]

    try:
        response, tool_calls = run_agent(messages)
    except Exception as e:
        response, tool_calls = f"Model call failed: {type(e).__name__}: {str(e)[:300]}", []

    return ChatResponse(response=response, session_id=session_id, tool_calls=tool_calls)


@app.post("/clear")
def clear(session_id: str | None = None):
    sessions.clear(session_id)
    return {"status": "ok"}
