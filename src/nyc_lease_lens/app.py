import logging
import os
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from nyc_lease_lens.agent import Agent
from nyc_lease_lens.config import LLMSettings, Settings, get_settings
from nyc_lease_lens.context import request_id
from nyc_lease_lens.log import configure_logging, ms_since
from nyc_lease_lens.opendata import OpenDataClient
from nyc_lease_lens.schemas import ChatRequest, ChatResponse
from nyc_lease_lens.sessions import SessionStore
from nyc_lease_lens.tools import build_registry

STATIC_DIR = Path(__file__).parent / "static"
logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    client: OpenDataClient | None = None,
    agent: Agent | None = None,
) -> FastAPI:
    """Build the app. Tests can pass their own settings, Open Data client or agent."""
    settings = settings or get_settings()
    configure_logging(settings.logging.level, settings.logging.format)
    _use_vertex_project(settings.llm)

    client = client or OpenDataClient.from_settings(settings.opendata)
    agent = agent or Agent(
        tools=build_registry(client),
        model=settings.llm.model,
        vertex_project=settings.llm.vertexai_project,
        vertex_location=settings.llm.vertexai_location,
        max_tool_rounds=settings.llm.max_tool_rounds,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "app ready",
            extra={
                "model": agent.model,
                "tools": len(agent.tools.schemas),
                "max_tool_rounds": agent.max_tool_rounds,
                "opendata_timeout": client.timeout,
            },
        )
        yield
        client.http.close()
        logger.info("app stopped")

    app = FastAPI(title="NYC Lease Lens", lifespan=lifespan)
    app.state.agent = agent
    app.state.sessions = SessionStore(agent.system_prompt)
    app.middleware("http")(log_requests)
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def _use_vertex_project(llm: LLMSettings) -> None:
    # Bill Vertex calls to our project; gcloud user credentials have no quota project by default.
    if llm.vertexai_project:
        os.environ.setdefault("GOOGLE_CLOUD_QUOTA_PROJECT", llm.vertexai_project)
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", llm.vertexai_project)


def get_agent(request: Request) -> Agent:
    return request.app.state.agent


def get_sessions(request: Request) -> SessionStore:
    return request.app.state.sessions


AgentDep = Annotated[Agent, Depends(get_agent)]
SessionsDep = Annotated[SessionStore, Depends(get_sessions)]


async def log_requests(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Tag everything logged during a request with one ID, returned to the client as X-Request-ID."""
    incoming = request.headers.get("x-request-id", "")
    rid = incoming if re.fullmatch(r"[\w-]{1,64}", incoming) else uuid.uuid4().hex[:12]
    token = request_id.set(rid)
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("request failed", extra={"method": request.method, "path": request.url.path})
        raise
    else:
        response.headers["X-Request-ID"] = rid
        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": ms_since(start),
            },
        )
        return response
    finally:
        request_id.reset(token)


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
