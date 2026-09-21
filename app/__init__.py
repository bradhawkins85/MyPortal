__all__ = ["app"]


def __getattr__(name: str):
    """Lazily expose the ASGI app to avoid importing web dependencies eagerly."""
    if name == "app":
        from .main import app as fastapi_app

        globals()["app"] = fastapi_app
        return fastapi_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
