"""Central application configuration and path definitions."""

from .settings import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_OLLAMA_BASE_URL,
    AppSettings,
)

__all__ = [
    "AppSettings",
    "DEFAULT_CHAT_MODEL",
    "DEFAULT_EMBEDDING_MODEL",
    "DEFAULT_OLLAMA_BASE_URL",
]
