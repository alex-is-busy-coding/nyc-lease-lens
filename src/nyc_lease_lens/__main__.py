import uvicorn

from nyc_lease_lens.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "nyc_lease_lens.app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        reload_dirs=["src"] if settings.reload else None,
    )


if __name__ == "__main__":
    main()
