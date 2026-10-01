import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from nyc_lease_lens.agent.loop import Agent
from nyc_lease_lens.agent.sessions import SessionStore
from nyc_lease_lens.api.schemas import ChatRequest, ChatResponse

STATIC_DIR = Path(__file__).parent.parent / "static"
logger = logging.getLogger(__name__)


def get_agent(request: Request) -> Agent:
    return request.app.state.agent


def get_sessions(request: Request) -> SessionStore:
    return request.app.state.sessions


AgentDep = Annotated[Agent, Depends(get_agent)]
SessionsDep = Annotated[SessionStore, Depends(get_sessions)]


router = APIRouter()


@router.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, agent: AgentDep, sessions: SessionsDep) -> ChatResponse:
    session_id, messages = sessions.get_or_create(body.session_id)
    logger.info("chat", extra={"session_id": session_id, "turn": len(messages), "message_chars": len(body.message)})
    logger.debug("chat message", extra={"text": body.message})

    # Append user's message to the context
    messages += [{"role": "user", "content": body.message}]

    try:
        response, tool_calls = agent.run(messages)
    except Exception as e:
        logger.exception("agent failed", extra={"session_id": session_id})
        response, tool_calls = f"Model call failed: {type(e).__name__}: {str(e)[:300]}", []

    return ChatResponse(response=response, session_id=session_id, tool_calls=tool_calls)


@router.post("/clear")
def clear(sessions: SessionsDep, session_id: str | None = None) -> dict[str, str]:
    sessions.clear(session_id)
    logger.info("session cleared", extra={"session_id": session_id})
    return {"status": "ok"}
