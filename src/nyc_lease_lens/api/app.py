import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles

from nyc_lease_lens.agent.loop import Agent
from nyc_lease_lens.agent.sessions import SessionStore
from nyc_lease_lens.api.middleware import log_requests
from nyc_lease_lens.api.routes import REVALIDATE, STATIC_DIR, router
from nyc_lease_lens.config import Settings, get_settings, use_vertex_project
from nyc_lease_lens.data.client import OpenDataClient
from nyc_lease_lens.observability.log import configure_logging
from nyc_lease_lens.tools import build_registry

logger = logging.getLogger(__name__)


class RevalidatingStaticFiles(StaticFiles):
    """Static files that browsers check for changes before reusing, so updates show up on a normal reload."""

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers.update(REVALIDATE)
        return response


def create_app(
    settings: Settings | None = None,
    *,
    client: OpenDataClient | None = None,
    agent: Agent | None = None,
) -> FastAPI:
    """Build the app. Tests can pass their own settings, Open Data client or agent."""
    settings = settings or get_settings()
    configure_logging(settings.logging.level, settings.logging.format)
    use_vertex_project(settings.llm)

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
    app.mount("/static", RevalidatingStaticFiles(directory=STATIC_DIR), name="static")
    return app
