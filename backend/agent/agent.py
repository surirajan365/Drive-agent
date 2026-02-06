"""Main AI Agent — LLM + Tools + Memory.

Combines Gemini (via LangChain), Google Drive / Docs tools, and persistent
Drive-based memory into an autonomous agent that executes multi-step tasks
on a user's Google Drive.

Usage::

    agent = DriveAgent(credentials, user_id="alice@example.com")
    result = agent.execute("Research data science and create a doc in datascience folder")
"""

import json
import logging
import re
import uuid
from typing import Any, Optional

from google.oauth2.credentials import Credentials
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from backend.agent.memory import DriveMemory
from backend.agent.prompt import SYSTEM_PROMPT
from backend.services.gemini import GeminiService
from backend.tools import docs_tools, drive_tools

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Pydantic input schemas for LangChain tools
# ═══════════════════════════════════════════════════════════════════


class ListFilesInput(BaseModel):
    folder_id: str = Field(
        default="root",
        description="Google Drive folder ID to list (use 'root' for top-level).",
    )
    max_results: int = Field(default=25, description="Maximum files to return.")


class SearchDriveInput(BaseModel):
    query: str = Field(description="Filename or keyword to search for in Drive.")
    max_results: int = Field(default=15, description="Maximum results to return.")


class CreateFolderInput(BaseModel):
    name: str = Field(description="Name of the folder to create.")
    parent_id: str = Field(
        default="root", description="Parent folder ID ('root' for top-level)."
    )


class CreateDocInput(BaseModel):
    title: str = Field(description="Title of the new Google Doc.")
    folder_id: str = Field(
        default="",
        description="Folder ID to place the document in (empty string for root).",
    )


class WriteDocInput(BaseModel):
    document_id: str = Field(description="ID of the Google Doc to write to.")
    content: str = Field(
        description=(
            "Markdown-formatted content to write. "
            "Use # for H1, ## for H2, ### for H3 headings."
        ),
    )


class AppendDocInput(BaseModel):
    document_id: str = Field(description="ID of the Google Doc to append to.")
    content: str = Field(description="Markdown content to append at the end.")


class ReadDocInput(BaseModel):
    document_id: str = Field(description="ID of the Google Doc to read.")


class ReadFileInput(BaseModel):
    file_id: str = Field(description="Drive file ID to read (plain-text export).")


class ResearchInput(BaseModel):
    topic: str = Field(description="The topic to research comprehensively.")


class RecallMemoryInput(BaseModel):
    query: str = Field(
        description="Keyword or topic to search in the agent's long-term memory.",
    )


class SaveMemoryNoteInput(BaseModel):
    topic: str = Field(
        description="Short label for this memory (e.g. 'user prefers markdown').",
    )
    content: str = Field(
        description="The note or summary to remember for next time.",
    )


# ═══════════════════════════════════════════════════════════════════
#  Pending-action store (in-process dict; swap for Redis in prod)
# ═══════════════════════════════════════════════════════════════════

_pending_actions: dict[str, dict] = {}


# ═══════════════════════════════════════════════════════════════════
#  Agent
# ═══════════════════════════════════════════════════════════════════


class DriveAgent:
    """Autonomous agent that operates on a user's Google Drive.

    A new instance should be created **per request** so that the
    correct user credentials are always in scope.
    """

    def __init__(self, credentials: Credentials, user_id: str) -> None:
        self._creds = credentials
        self._user_id = user_id
        self._gemini = GeminiService()
        self._memory = DriveMemory(credentials)
        self._tools = self._build_tools()
        self._executor = self._build_agent()

    # ──────────────────────────────────────────────────────────────
    #  Tool construction (credentials are captured via closure)
    # ──────────────────────────────────────────────────────────────

    def _build_tools(self) -> list[StructuredTool]:
        creds = self._creds
        gemini = self._gemini
        memory = self._memory

        # Each nested function closes over `creds` / `gemini` / `memory`
        # so the LLM never needs to know about credentials.

        def _list_drive_files(
            folder_id: str = "root", max_results: int = 25
        ) -> str:
            """List files in a Google Drive folder."""
            return json.dumps(
                drive_tools.list_files(creds, folder_id, max_results),
                default=str,
            )

        def _search_drive(query: str, max_results: int = 15) -> str:
            """Search Google Drive by filename / keyword."""
            return json.dumps(
                drive_tools.search_files(creds, query, max_results),
                default=str,
            )

        def _create_folder(name: str, parent_id: str = "root") -> str:
            """Create a folder (or return existing) in Google Drive."""
            return json.dumps(
                drive_tools.get_or_create_folder(creds, name, parent_id),
                default=str,
            )

        def _create_document(title: str, folder_id: str = "") -> str:
            """Create a new Google Doc."""
            fid = folder_id if folder_id else None
            return json.dumps(
                docs_tools.create_document(creds, title, fid),
                default=str,
            )

        def _write_to_document(document_id: str, content: str) -> str:
            """Overwrite a Google Doc with Markdown content."""
            return json.dumps(
                docs_tools.write_to_document(creds, document_id, content),
                default=str,
            )

        def _append_to_document(document_id: str, content: str) -> str:
            """Append Markdown content to a Google Doc."""
            return json.dumps(
                docs_tools.append_to_document(creds, document_id, content),
                default=str,
            )

        def _read_document(document_id: str) -> str:
            """Read the text of a Google Doc."""
            return json.dumps(
                docs_tools.read_document(creds, document_id),
                default=str,
            )

        def _read_file_content(file_id: str) -> str:
            """Read any Drive file as plain text."""
            return json.dumps(
                drive_tools.read_file_content(creds, file_id),
                default=str,
            )

        def _research_topic(topic: str) -> str:
            """Research a topic via AI and return a Markdown article."""
            return gemini.research_topic(topic)

        def _recall_memory(query: str) -> str:
            """Search the agent's long-term memory."""
            return json.dumps(memory.recall(query), indent=2, default=str)

        def _save_memory_note(topic: str, content: str) -> str:
            """Save a note to long-term memory for future reference."""
            fid = memory.save_summary(topic, content)
            return json.dumps(
                {"success": True, "topic": topic, "file_id": fid},
                default=str,
            )

        return [
            StructuredTool.from_function(
                func=_list_drive_files,
                name="list_drive_files",
                description=(
                    "List files in a Google Drive folder. "
                    "Returns names, IDs, MIME types, and links."
                ),
                args_schema=ListFilesInput,
            ),
            StructuredTool.from_function(
                func=_search_drive,
                name="search_drive",
                description=(
                    "Search Google Drive for files or folders by name. "
                    "Use this to check whether a resource already exists."
                ),
                args_schema=SearchDriveInput,
            ),
            StructuredTool.from_function(
                func=_create_folder,
                name="create_folder",
                description=(
                    "Create a folder in Google Drive (returns existing one "
                    "if a folder with the same name already exists). "
                    "Returns folder ID and link."
                ),
                args_schema=CreateFolderInput,
            ),
            StructuredTool.from_function(
                func=_create_document,
                name="create_document",
                description=(
                    "Create a new, empty Google Doc. Optionally place it in "
                    "a folder by providing folder_id."
                ),
                args_schema=CreateDocInput,
            ),
            StructuredTool.from_function(
                func=_write_to_document,
                name="write_to_document",
                description=(
                    "Write Markdown content to a Google Doc (overwrites "
                    "existing body). Use # / ## / ### for headings."
                ),
                args_schema=WriteDocInput,
            ),
            StructuredTool.from_function(
                func=_append_to_document,
                name="append_to_document",
                description="Append Markdown content to the end of a Google Doc.",
                args_schema=AppendDocInput,
            ),
            StructuredTool.from_function(
                func=_read_document,
                name="read_document",
                description="Read the full text content of a Google Doc.",
                args_schema=ReadDocInput,
            ),
            StructuredTool.from_function(
                func=_read_file_content,
                name="read_file_content",
                description="Export and read any Drive file as plain text.",
                args_schema=ReadFileInput,
            ),
            StructuredTool.from_function(
                func=_research_topic,
                name="research_topic",
                description=(
                    "Research a topic using AI. Returns a comprehensive, "
                    "structured Markdown article ready for a Google Doc."
                ),
                args_schema=ResearchInput,
            ),
            StructuredTool.from_function(
                func=_recall_memory,
                name="recall_memory",
                description=(
                    "Search the agent's persistent memory for past "
                    "interactions and stored research summaries."
                ),
                args_schema=RecallMemoryInput,
            ),
            StructuredTool.from_function(
                func=_save_memory_note,
                name="save_memory_note",
                description=(
                    "Save an important note, user preference, or summary "
                    "to long-term memory so it can be recalled in future "
                    "sessions. Use this whenever you learn something worth "
                    "remembering about the user or their work."
                ),
                args_schema=SaveMemoryNoteInput,
            ),
        ]

    # ──────────────────────────────────────────────────────────────
    #  Agent executor
    # ──────────────────────────────────────────────────────────────

    def _build_agent(self) -> AgentExecutor:
        llm = self._gemini.get_agent_llm(temperature=0.1)

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", SYSTEM_PROMPT),
                MessagesPlaceholder("chat_history", optional=True),
                ("human", "{input}"),
                MessagesPlaceholder("agent_scratchpad"),
            ]
        )

        agent = create_tool_calling_agent(llm, self._tools, prompt)

        return AgentExecutor(
            agent=agent,
            tools=self._tools,
            verbose=True,
            max_iterations=15,
            handle_parsing_errors=True,
            return_intermediate_steps=True,
        )

    # ══════════════════════════════════════════════════════════════
    #  Public interface
    # ══════════════════════════════════════════════════════════════

    def execute(
        self,
        command: str,
        chat_history: Optional[list] = None,
    ) -> dict[str, Any]:
        """Execute a natural-language command via the agent loop.

        The full lifecycle:
          1. Load rich memory context (profile, recent history, deep summary)
          2. Inject context into the prompt alongside the command
          3. Run the agent executor (LLM + tools)
          4. Summarise the interaction with the LLM
          5. Save: conversation log entry, research summaries, learned prefs

        Returns a dict with keys:
            ``status``  — ``"completed"`` or ``"error"``
            ``result``  — human-readable outcome
            ``steps``   — list of intermediate tool calls
        """
        logger.info("[%s] Executing: %s", self._user_id, command)

        try:
            # ── 1. Build rich memory context ──────────────────────
            memory_context = self._memory.get_context_for_agent(
                max_recent=10
            )

            enriched_input = (
                f"{command}\n\n"
                f"═══ MEMORY CONTEXT (use to inform your actions) ═══\n"
                f"{memory_context}"
            )

            # ── 2. Run the agent ──────────────────────────────────
            result = self._executor.invoke(
                {
                    "input": enriched_input,
                    "chat_history": chat_history or [],
                }
            )

            output: str = result.get("output", "")

            # ── 3. Flatten intermediate steps ─────────────────────
            steps: list[dict] = []
            for action, observation in result.get("intermediate_steps", []):
                steps.append(
                    {
                        "tool": action.tool,
                        "input": str(action.tool_input),
                        "output": str(observation)[:500],
                    }
                )

            # ── 4. LLM-summarise this interaction ─────────────────
            interaction_text = (
                f"User command: {command}\n"
                f"Tools used: {[s['tool'] for s in steps]}\n"
                f"Agent response: {output[:600]}"
            )
            try:
                smart_summary = self._gemini.summarise(
                    interaction_text, max_words=80
                )
            except Exception:
                smart_summary = output[:300]

            # ── 5a. Extract topics & folders from tool outputs ────
            topics_found = _extract_topics(command, steps)
            folders_found = _extract_folders(steps)

            # ── 5b. Save research summaries to Drive memory ──────
            for step in steps:
                if step["tool"] == "research_topic":
                    try:
                        topic_input = json.loads(step["input"]) if step["input"].startswith("{") else {"topic": step["input"]}
                        topic_name = topic_input.get("topic", "unknown")
                        research_summary = self._gemini.summarise(
                            step["output"], max_words=150
                        )
                        self._memory.save_summary(
                            topic_name, research_summary
                        )
                    except Exception:
                        logger.debug("Could not save research summary")

            # ── 5c. Save conversation log entry ───────────────────
            self._memory.append_conversation(
                {
                    "command": command,
                    "summary": smart_summary,
                    "tools_used": [s["tool"] for s in steps],
                    "topics": topics_found,
                    "folders": folders_found,
                }
            )

            # ── 5d. Learn user patterns ───────────────────────────
            try:
                self._memory.update_learned_patterns(
                    command=command,
                    tools_used=[s["tool"] for s in steps],
                    folders_touched=folders_found,
                    topics=topics_found,
                )
            except Exception:
                logger.debug("Pattern learning failed (non-fatal)")

            return {"status": "completed", "result": output, "steps": steps}

        except Exception as exc:
            logger.exception("Agent execution failed")
            return {
                "status": "error",
                "result": f"Agent encountered an error: {exc}",
                "steps": [],
            }

    # ──────────────────────────────────────────────────────────────
    #  Confirmation helpers (destructive actions)
    # ──────────────────────────────────────────────────────────────

    def preview_destructive(
        self, command: str, actions: list[dict]
    ) -> dict[str, Any]:
        """Stage a destructive action plan for user confirmation.

        Returns an ``action_id`` the client can use to confirm or reject.
        """
        action_id = str(uuid.uuid4())
        _pending_actions[action_id] = {
            "user_id": self._user_id,
            "command": command,
            "actions": actions,
        }
        return {
            "status": "confirmation_required",
            "action_id": action_id,
            "preview": actions,
            "message": (
                "The following actions require your confirmation "
                "before they are executed."
            ),
        }

    @staticmethod
    def confirm_action(action_id: str, user_id: str) -> Optional[dict]:
        """Pop and return the pending action if it belongs to *user_id*."""
        action = _pending_actions.pop(action_id, None)
        if action and action["user_id"] == user_id:
            return action
        return None

    @staticmethod
    def reject_action(action_id: str, user_id: str) -> bool:
        """Reject and discard a pending action. Returns ``True`` on success."""
        action = _pending_actions.pop(action_id, None)
        return action is not None and action["user_id"] == user_id


# ═══════════════════════════════════════════════════════════════════
#  Module-level helpers — topic & folder extraction
# ═══════════════════════════════════════════════════════════════════


def _extract_topics(command: str, steps: list[dict]) -> list[str]:
    """Extract topic names from the command and tool calls."""
    topics: list[str] = []

    # From research_topic tool inputs
    for step in steps:
        if step["tool"] == "research_topic":
            try:
                inp = step["input"]
                if inp.startswith("{"):
                    parsed = json.loads(inp)
                    topics.append(parsed.get("topic", ""))
                else:
                    topics.append(inp.strip("\"' "))
            except Exception:
                pass

    # Fallback: simple keyword extraction from the command
    if not topics:
        match = re.search(
            r"(?:research|write about|article on|learn about)\s+(.+?)(?:\s+and\s+|\s+in\s+|$)",
            command,
            re.IGNORECASE,
        )
        if match:
            topics.append(match.group(1).strip())

    return [t for t in topics if t]


def _extract_folders(steps: list[dict]) -> list[str]:
    """Extract folder names from create_folder tool outputs."""
    folders: list[str] = []
    for step in steps:
        if step["tool"] == "create_folder":
            try:
                out = json.loads(step["output"])
                name = out.get("folder", {}).get("name", "")
                if name:
                    folders.append(name)
            except Exception:
                pass
    return folders
