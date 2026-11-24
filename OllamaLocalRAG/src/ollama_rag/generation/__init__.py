"""Backward-compatible facade for model and agent modules."""

from ollama_rag.agents import RAGAgent, RAGAnswer
from ollama_rag.models import OllamaChatClient

__all__ = ["OllamaChatClient", "RAGAgent", "RAGAnswer"]
