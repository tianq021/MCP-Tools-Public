"""Vector persistence and similarity search implementations."""

from .sqlite_store import build_database, search_database

__all__ = ["build_database", "search_database"]
