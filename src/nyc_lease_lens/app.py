from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from nyc_lease_lens.agent import Agent
from nyc_lease_lens.opendata import OpenDataClient
from nyc_lease_lens.schemas import ChatRequest, ChatResponse
from nyc_lease_lens.sessions import SessionStore
from nyc_lease_lens.tools import build_registry

STATIC_DIR = Path(__file__).parent / "static"

agent = Agent(tools=build_registry(OpenDataClient()))
sessions = SessionStore(agent.system_prompt)

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
        response, tool_calls = agent.run(messages)
    except Exception as e:
        response, tool_calls = f"Model call failed: {type(e).__name__}: {str(e)[:300]}", []

    return ChatResponse(response=response, session_id=session_id, tool_calls=tool_calls)


@app.post("/clear")
def clear(session_id: str | None = None):
    sessions.clear(session_id)
    return {"status": "ok"}
