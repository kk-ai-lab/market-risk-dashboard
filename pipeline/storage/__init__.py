"""Storage package (architecture §3.5)."""

__all__ = ["StorageWriter"]

def __getattr__(name: str):
    if name == "StorageWriter":
        from pipeline.storage.writer import StorageWriter

        return StorageWriter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
