from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings. Precedence: environment variables, then .env, then these defaults."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Model
    model: str = "vertex_ai/gemini-3.5-flash-lite"
    vertexai_project: str | None = None  # None: fall back to the gcloud ADC project
    vertexai_location: str = "global"
    max_tool_rounds: int = Field(default=5, ge=1)

    # Server
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    reload: bool = False

    # NYC Open Data
    geosearch_url: str = "https://geosearch.planninglabs.nyc/v2/search"
    socrata_url: str = "https://data.cityofnewyork.us/resource/{dataset}.json"
    opendata_timeout: float = Field(default=15, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
