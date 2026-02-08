"""Centralized configuration for the AI Drive Agent.

All settings are loaded from environment variables or a ``.env`` file.
Sensitive values are **never** hard-coded.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings with validation and sensible defaults."""

    # ── Google OAuth 2.0 ──────────────────────────────────────────
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/auth/callback"
    GOOGLE_SCOPES: list[str] = [
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/documents",
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ]

    # ── Google Gemini ─────────────────────────────────────────────
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # ── Groq (primary LLM) ────────────────────────────────────────
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # ── Tavily (web search) ────────────────────────────────────────
    TAVILY_API_KEY: str = ""

    # ── Security ──────────────────────────────────────────────────
    ENCRYPTION_KEY: str  # Fernet key (32 url-safe base64 bytes)
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_HOURS: int = 24

    # ── Application ───────────────────────────────────────────────
    APP_NAME: str = "AI Drive Agent"
    MEMORY_FOLDER_NAME: str = "AI_AGENT_MEMORY"
    SUMMARIES_FOLDER_NAME: str = "summaries"
    LOG_LEVEL: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache()
def get_settings() -> Settings:
    """Return cached application settings (singleton)."""
    return Settings()
