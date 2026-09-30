from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class _Group(BaseSettings):
    """Each group reads its own variables. Precedence: environment, then .env, then the defaults below."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class LLMSettings(_Group):
    """MODEL, VERTEXAI_PROJECT, VERTEXAI_LOCATION, MAX_TOOL_ROUNDS"""

    model: str = "vertex_ai/gemini-3.5-flash-lite"
    vertexai_project: str | None = None  # None: fall back to the gcloud ADC project
    vertexai_location: str = "global"
    max_tool_rounds: int = Field(default=8, ge=1)


class ServerSettings(_Group):
    """HOST, PORT, RELOAD"""

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    reload: bool = False


class OpenDataSettings(_Group):
    """OPENDATA_TIMEOUT, OPENDATA_RETRIES, OPENDATA_SLOW_MS, OPENDATA_GEOSEARCH_URL, OPENDATA_SOCRATA_URL"""

    model_config = SettingsConfigDict(env_prefix="OPENDATA_")

    timeout: float = Field(default=20, gt=0)
    retries: int = Field(default=2, ge=0)
    slow_ms: int = Field(default=5000, gt=0)  # requests slower than this are logged as warnings
    geosearch_url: str = "https://geosearch.planninglabs.nyc/v2/search"
    socrata_url: str = "https://data.cityofnewyork.us/resource/{dataset}.json"


class LoggingSettings(_Group):
    """LOG_LEVEL, LOG_FORMAT"""

    model_config = SettingsConfigDict(env_prefix="LOG_")

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["text", "json"] = "text"


class Settings(BaseModel):
    """All settings, by group. In tests, override one group: Settings(opendata=OpenDataSettings(timeout=1))."""

    llm: LLMSettings = Field(default_factory=LLMSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    opendata: OpenDataSettings = Field(default_factory=OpenDataSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)


@lru_cache
def get_settings() -> Settings:
    return Settings()
