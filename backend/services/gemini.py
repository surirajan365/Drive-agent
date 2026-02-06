"""LLM integration — Groq (primary) + Gemini (fallback / research).

Provides:
  • A factory for the LangChain chat model used by the agent executor.
    → Uses Groq (Llama 3.3 70B) by default for high free-tier limits.
    → Falls back to Google Gemini if Groq is unavailable.
  • Direct helper methods for topic research and text summarisation.
"""

import logging
from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel

from backend.config import get_settings

logger = logging.getLogger(__name__)


class GeminiService:
    """Multi-provider LLM wrapper — Groq primary, Gemini fallback."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        self._settings = get_settings()

        # Gemini (for research / summarisation / fallback)
        self._gemini_key = api_key or self._settings.GEMINI_API_KEY
        self._gemini_model = model or self._settings.GEMINI_MODEL

        # Groq (primary agent LLM)
        self._groq_key = self._settings.GROQ_API_KEY
        self._groq_model = self._settings.GROQ_MODEL

        # Initialise Gemini SDK only if key is available
        self._gemini_genai_model = None
        if self._gemini_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=self._gemini_key)
                self._gemini_genai_model = genai.GenerativeModel(self._gemini_model)
            except Exception as exc:
                logger.warning("Gemini SDK init failed: %s", exc)

    # ── Agent LLM (Groq primary, Gemini fallback) ────────────────

    def get_agent_llm(self, temperature: float = 0.2) -> BaseChatModel:
        """Return the best available LangChain chat model for the agent.

        Priority: Groq → Gemini → error
        """
        if self._groq_key:
            try:
                from langchain_groq import ChatGroq
                logger.info("Using Groq (%s) as agent LLM", self._groq_model)
                return ChatGroq(
                    model=self._groq_model,
                    api_key=self._groq_key,
                    temperature=temperature,
                )
            except Exception as exc:
                logger.warning("Groq init failed, falling back to Gemini: %s", exc)

        return self._get_gemini_langchain(temperature)

    # ── Gemini-only LangChain model ───────────────────────────────

    def _get_gemini_langchain(
        self, temperature: float = 0.2
    ) -> BaseChatModel:
        """Return a LangChain model backed by Gemini."""
        from langchain_google_genai import ChatGoogleGenerativeAI
        logger.info("Using Gemini (%s) as LLM", self._gemini_model)
        return ChatGoogleGenerativeAI(
            model=self._gemini_model,
            google_api_key=self._gemini_key,
            temperature=temperature,
            convert_system_message_to_human=True,
        )

    # Keep legacy method for backward compatibility
    def get_langchain_llm(self, temperature: float = 0.2) -> BaseChatModel:
        """Legacy alias — returns agent LLM."""
        return self.get_agent_llm(temperature)

    # ── Direct research call ──────────────────────────────────────

    def research_topic(self, topic: str) -> str:
        """Generate a comprehensive, Markdown-formatted research article."""
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
            f"- Aim for 800\u20131 200 words\n"
            f"- Do NOT include unverifiable references or citations\n"
        )

        # Try Gemini first for research (better at long-form), then Groq
        if self._gemini_genai_model:
            try:
                response = self._gemini_genai_model.generate_content(prompt)
                logger.info("Research generated (Gemini) for topic: %s", topic)
                return response.text
            except Exception as exc:
                logger.warning("Gemini research failed: %s", exc)

        # Fallback to Groq via LangChain
        llm = self.get_agent_llm(temperature=0.3)
        response = llm.invoke(prompt)
        logger.info("Research generated (Groq) for topic: %s", topic)
        return response.content

    # ── Summarisation ─────────────────────────────────────────────

    def summarise(self, text: str, max_words: int = 150) -> str:
        """Return a concise summary preserving key facts."""
        prompt = (
            f"Summarise the following text in at most {max_words} words. "
            f"Preserve key facts and conclusions.\n\n{text}"
        )

        if self._gemini_genai_model:
            try:
                response = self._gemini_genai_model.generate_content(prompt)
                return response.text
            except Exception as exc:
                logger.warning("Gemini summarise failed: %s", exc)

        llm = self.get_agent_llm(temperature=0.1)
        response = llm.invoke(prompt)
        return response.content

    # ── Action planning ───────────────────────────────────────────

    def plan_actions(self, command: str, context: str = "") -> str:
        """Return a JSON action plan for the given user command."""
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

        if self._gemini_genai_model:
            try:
                response = self._gemini_genai_model.generate_content(prompt)
                return response.text
            except Exception:
                pass

        llm = self.get_agent_llm(temperature=0.1)
        response = llm.invoke(prompt)
        return response.content
