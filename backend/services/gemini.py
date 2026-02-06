"""Google Gemini integration for research, summarisation, and planning.

Provides:
  • A factory for the LangChain ``ChatGoogleGenerativeAI`` wrapper used by
    the agent executor.
  • Direct helper methods for topic research and text summarisation that
    bypass the agent loop when a single LLM call suffices.
"""

import logging
from typing import Optional

import google.generativeai as genai
from langchain_google_genai import ChatGoogleGenerativeAI

from backend.config import get_settings

logger = logging.getLogger(__name__)


class GeminiService:
    """Thin, reusable wrapper around the Google Gemini SDK."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.GEMINI_API_KEY
        self._model_name = model or settings.GEMINI_MODEL
        genai.configure(api_key=self._api_key)
        self._model = genai.GenerativeModel(self._model_name)

    # ── LangChain integration ─────────────────────────────────────

    def get_langchain_llm(
        self, temperature: float = 0.2
    ) -> ChatGoogleGenerativeAI:
        """Return a LangChain-compatible chat model backed by Gemini."""
        return ChatGoogleGenerativeAI(
            model=self._model_name,
            google_api_key=self._api_key,
            temperature=temperature,
            convert_system_message_to_human=True,
        )

    # ── Direct research call ──────────────────────────────────────

    def research_topic(self, topic: str) -> str:
        """Generate a comprehensive, Markdown-formatted research article.

        Returns:
            A structured article with headings, sections, and conclusions.
        """
        prompt = (
            f"You are a world-class researcher and technical writer.\n"
            f"Write a comprehensive, well-structured research article on "
            f"the topic: **{topic}**.\n\n"
            f"Requirements:\n"
            f"- Use Markdown headings: # for title, ## for main sections, "
            f"### for sub-sections\n"
            f"- Include these sections at minimum: Introduction, "
            f"Key Concepts, Current Trends, Real-World Applications, "
            f"Challenges & Limitations, Future Outlook, Conclusion\n"
            f"- Be factual, balanced, and informative\n"
            f"- Aim for 800–1 200 words\n"
            f"- Do NOT include unverifiable references or citations\n"
        )
        response = self._model.generate_content(prompt)
        logger.info("Research generated for topic: %s", topic)
        return response.text

    # ── Summarisation ─────────────────────────────────────────────

    def summarise(self, text: str, max_words: int = 150) -> str:
        """Return a concise summary preserving key facts."""
        prompt = (
            f"Summarise the following text in at most {max_words} words. "
            f"Preserve key facts and conclusions.\n\n{text}"
        )
        response = self._model.generate_content(prompt)
        return response.text

    # ── Action planning ───────────────────────────────────────────

    def plan_actions(self, command: str, context: str = "") -> str:
        """Return a JSON action plan for the given user command.

        This is used for the *preview mode* so the user can inspect what
        the agent intends to do before execution.
        """
        prompt = (
            "You are an AI agent planner.  Given the user's command and "
            "optional context, produce a JSON array of action steps.\n"
            "Each element must have:\n"
            '  "step": <int>, "action": <tool_name>, '
            '"params": {...}, "reason": <str>\n\n'
            "Available tools: list_drive_files, search_drive, "
            "create_folder, create_document, write_to_document, "
            "read_document, research_topic\n\n"
            f"User command: {command}\n"
            f"Context: {context or 'None'}\n\n"
            "Respond ONLY with the JSON array."
        )
        response = self._model.generate_content(prompt)
        return response.text
