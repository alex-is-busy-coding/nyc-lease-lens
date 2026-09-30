import uvicorn

from nyc_lease_lens.config import get_settings
from nyc_lease_lens.log import configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    uvicorn.run(
        "nyc_lease_lens.app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        reload_dirs=["src"] if settings.reload else None,
        log_config=None,  # keep our logging setup instead of uvicorn's
    )


if __name__ == "__main__":
    main()
