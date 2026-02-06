"""Agent memory persisted in Google Drive — with learning & consolidation.

All agent state is stored inside the user's Drive under a dedicated
folder so that context is **never lost** across sessions or devices.

Folder layout::

    /AI_AGENT_MEMORY/
    ├── profile.json              – user preferences & learned patterns
    ├── conversation_log.json     – rolling log of summarised interactions
    ├── consolidated_memory.json  – condensed archive of old conversations
    ├── deep_summary.json         – LLM-generated long-term memory summary
    └── summaries/                – per-topic research summaries

Key capabilities:
  • **Intelligent summarisation** — interactions are LLM-summarised before
    storage, not just truncated.
  • **Preference learning** — the agent extracts recurring patterns
    (preferred folder names, writing style, topics of interest) and stores
    them in ``profile.json``.
  • **Memory consolidation** — when the conversation log exceeds its cap
    the oldest batch is condensed into ``consolidated_memory.json`` so
    context is never truly lost.
  • **Proactive recall** — the agent can search across conversations,
    summaries, consolidated memory, and profile to inform future decisions.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

from backend.config import get_settings

logger = logging.getLogger(__name__)

FOLDER_MIME = "application/vnd.google-apps.folder"
JSON_MIME = "application/json"

_MAX_LOG_ENTRIES = 200  # when exceeded, oldest batch is consolidated
_CONSOLIDATION_BATCH = 50  # how many entries to condense at once
_MAX_CONSOLIDATED_ENTRIES = 100  # cap on condensed memory blocks


class DriveMemory:
    """Read/write the agent's persistent memory inside Google Drive.

    Every public method is safe to call repeatedly — folder creation and
    file lookups are idempotent.
    """

    def __init__(self, credentials: Credentials) -> None:
        self._creds = credentials
        self._settings = get_settings()
        self._drive = build(
            "drive", "v3", credentials=credentials, cache_discovery=False
        )
        self._memory_folder_id: Optional[str] = None
        self._summaries_folder_id: Optional[str] = None

    # ══════════════════════════════════════════════════════════════
    #  Internal — folder bootstrapping
    # ══════════════════════════════════════════════════════════════

    def _find_or_create_folder(
        self, name: str, parent_id: str = "root"
    ) -> str:
        """Return the ID of *name* under *parent_id*, creating it if needed."""
        q = (
            f"name = '{name}' and mimeType = '{FOLDER_MIME}' "
            f"and '{parent_id}' in parents and trashed = false"
        )
        resp = (
            self._drive.files()
            .list(q=q, pageSize=1, fields="files(id)")
            .execute()
        )
        files = resp.get("files", [])
        if files:
            return files[0]["id"]

        meta = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
        folder = self._drive.files().create(body=meta, fields="id").execute()
        logger.info("Created memory folder '%s' → %s", name, folder["id"])
        return folder["id"]

    def _ensure_folders(self) -> None:
        """Lazily bootstrap the memory folder tree."""
        if self._memory_folder_id is None:
            self._memory_folder_id = self._find_or_create_folder(
                self._settings.MEMORY_FOLDER_NAME
            )
            self._summaries_folder_id = self._find_or_create_folder(
                self._settings.SUMMARIES_FOLDER_NAME,
                parent_id=self._memory_folder_id,
            )

    # ══════════════════════════════════════════════════════════════
    #  Internal — low-level JSON read / write
    # ══════════════════════════════════════════════════════════════

    def _find_file(self, name: str, parent_id: str) -> Optional[str]:
        """Return the file ID for *name* under *parent_id*, or ``None``."""
        q = f"name = '{name}' and '{parent_id}' in parents and trashed = false"
        resp = (
            self._drive.files()
            .list(q=q, pageSize=1, fields="files(id)")
            .execute()
        )
        files = resp.get("files", [])
        return files[0]["id"] if files else None

    def _read_json(self, file_id: str) -> Any:
        """Download and parse a JSON file from Drive."""
        content = self._drive.files().get_media(fileId=file_id).execute()
        text = content.decode("utf-8") if isinstance(content, bytes) else content
        return json.loads(text)

    def _write_json(self, name: str, parent_id: str, data: Any) -> str:
        """Create or overwrite a JSON file in Drive. Returns the file ID."""
        payload = json.dumps(data, indent=2, default=str).encode("utf-8")
        media = MediaInMemoryUpload(payload, mimetype=JSON_MIME, resumable=False)

        existing_id = self._find_file(name, parent_id)
        if existing_id:
            self._drive.files().update(
                fileId=existing_id, media_body=media
            ).execute()
            return existing_id

        meta = {"name": name, "parents": [parent_id], "mimeType": JSON_MIME}
        created = (
            self._drive.files()
            .create(body=meta, media_body=media, fields="id")
            .execute()
        )
        return created["id"]

    # ══════════════════════════════════════════════════════════════
    #  Profile — user preferences & learned patterns
    # ══════════════════════════════════════════════════════════════

    def load_profile(self) -> dict:
        """Load the user profile from Drive, returning defaults if absent."""
        self._ensure_folders()
        assert self._memory_folder_id is not None
        fid = self._find_file("profile.json", self._memory_folder_id)
        if fid:
            try:
                return self._read_json(fid)
            except Exception:
                logger.warning("Corrupt profile.json — returning defaults")
        return {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "preferences": {},
            "learned_patterns": [],
            "frequently_used_folders": {},
            "topics_of_interest": [],
            "interaction_count": 0,
        }

    def save_profile(self, profile: dict) -> None:
        """Persist the user profile to Drive."""
        self._ensure_folders()
        assert self._memory_folder_id is not None
        profile["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_json("profile.json", self._memory_folder_id, profile)

    def update_learned_patterns(
        self,
        command: str,
        tools_used: list[str],
        folders_touched: list[str],
        topics: list[str],
    ) -> None:
        """Extract and persist behavioural patterns from this interaction.

        Over time this builds a rich picture of the user's habits:
        • Which folders they use most
        • Which topics they research
        • How many total interactions they've had
        """
        profile = self.load_profile()
        profile["interaction_count"] = profile.get("interaction_count", 0) + 1

        # Track folder frequency
        freq: dict = profile.get("frequently_used_folders", {})
        for folder in folders_touched:
            freq[folder] = freq.get(folder, 0) + 1
        profile["frequently_used_folders"] = freq

        # Track topics of interest (deduplicated, last-50)
        existing_topics: list = profile.get("topics_of_interest", [])
        for t in topics:
            t_lower = t.lower().strip()
            if t_lower and t_lower not in existing_topics:
                existing_topics.append(t_lower)
        profile["topics_of_interest"] = existing_topics[-50:]

        # Store a learned pattern entry (capped at 30)
        patterns: list = profile.get("learned_patterns", [])
        patterns.append(
            {
                "command_type": _classify_command(command),
                "tools": tools_used,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        profile["learned_patterns"] = patterns[-30:]

        self.save_profile(profile)

    # ══════════════════════════════════════════════════════════════
    #  Conversation log
    # ══════════════════════════════════════════════════════════════

    def load_conversation_log(self) -> list[dict]:
        """Return the conversation log (most-recent entry last)."""
        self._ensure_folders()
        assert self._memory_folder_id is not None
        fid = self._find_file("conversation_log.json", self._memory_folder_id)
        if fid:
            try:
                data = self._read_json(fid)
                return data if isinstance(data, list) else []
            except Exception:
                logger.warning("Corrupt conversation_log.json — starting fresh")
        return []

    def append_conversation(self, entry: dict) -> None:
        """Append *entry* to the conversation log.

        When the log exceeds ``_MAX_LOG_ENTRIES``, the oldest batch is
        **consolidated** (summarised) into ``consolidated_memory.json``
        so that context is never truly deleted.
        """
        self._ensure_folders()
        assert self._memory_folder_id is not None
        log = self.load_conversation_log()
        entry["timestamp"] = datetime.now(timezone.utc).isoformat()
        log.append(entry)

        if len(log) > _MAX_LOG_ENTRIES:
            # Consolidate oldest entries instead of silently dropping them
            overflow = log[:_CONSOLIDATION_BATCH]
            log = log[_CONSOLIDATION_BATCH:]
            self._consolidate(overflow)

        self._write_json(
            "conversation_log.json", self._memory_folder_id, log
        )

    # ══════════════════════════════════════════════════════════════
    #  Consolidated memory — compressed archive of old conversations
    # ══════════════════════════════════════════════════════════════

    def _load_consolidated(self) -> list[dict]:
        """Load the consolidated memory archive."""
        self._ensure_folders()
        assert self._memory_folder_id is not None
        fid = self._find_file(
            "consolidated_memory.json", self._memory_folder_id
        )
        if fid:
            try:
                data = self._read_json(fid)
                return data if isinstance(data, list) else []
            except Exception:
                return []
        return []

    def _save_consolidated(self, data: list[dict]) -> None:
        assert self._memory_folder_id is not None
        self._write_json(
            "consolidated_memory.json", self._memory_folder_id, data
        )

    def _consolidate(self, entries: list[dict]) -> None:
        """Compress a batch of conversation entries into a single block.

        This runs **without** an LLM call (no extra latency).  It extracts
        structured statistics.  The LLM-based deep summary happens via
        ``deep_consolidate()``.
        """
        if not entries:
            return

        commands = [e.get("command", "") for e in entries]
        tools: set[str] = set()
        topics: set[str] = set()
        for e in entries:
            tools.update(e.get("tools_used", []))
            for t in e.get("topics", []):
                topics.add(t)

        block = {
            "period_start": entries[0].get("timestamp", ""),
            "period_end": entries[-1].get("timestamp", ""),
            "entry_count": len(entries),
            "command_samples": commands[:5]
            + (["..."] if len(commands) > 5 else []),
            "all_tools_used": sorted(tools),
            "topics_mentioned": sorted(topics),
            "condensed_at": datetime.now(timezone.utc).isoformat(),
        }

        archive = self._load_consolidated()
        archive.append(block)
        if len(archive) > _MAX_CONSOLIDATED_ENTRIES:
            archive = archive[-_MAX_CONSOLIDATED_ENTRIES:]
        self._save_consolidated(archive)
        logger.info("Consolidated %d entries into archive", len(entries))

    def deep_consolidate(self, summarise_fn: Callable[[str], str]) -> str:
        """Use an LLM to create a rich summary of the consolidated archive.

        Args:
            summarise_fn: A callable ``(text) -> str`` that produces a
                natural-language summary (typically
                ``GeminiService.summarise``).

        Returns:
            The generated summary text (also persisted to Drive).
        """
        archive = self._load_consolidated()
        if not archive:
            return "No archived memory to consolidate."

        text_blob = json.dumps(archive, indent=2, default=str)
        summary = summarise_fn(
            f"Summarise the following archived agent interaction history. "
            f"Highlight: recurring user goals, preferred workflows, "
            f"frequently used folders and topics, and any notable "
            f"patterns.\n\n{text_blob}"
        )

        # Save the deep summary as its own file
        self._ensure_folders()
        assert self._memory_folder_id is not None
        self._write_json(
            "deep_summary.json",
            self._memory_folder_id,
            {
                "summary": summary,
                "source_blocks": len(archive),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return summary

    # ══════════════════════════════════════════════════════════════
    #  Research summaries
    # ══════════════════════════════════════════════════════════════

    def save_summary(self, topic: str, summary: str) -> str:
        """Save a research summary under ``summaries/<topic>.json``.

        Returns the Drive file ID.
        """
        self._ensure_folders()
        assert self._summaries_folder_id is not None
        safe_name = topic.lower().replace(" ", "_")[:60] + ".json"
        data = {
            "topic": topic,
            "summary": summary,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        fid = self._write_json(safe_name, self._summaries_folder_id, data)
        logger.info("Saved summary for '%s' → %s", topic, fid)
        return fid

    def search_summaries(self, keyword: str) -> list[dict]:
        """Search stored summaries whose filename contains *keyword*."""
        self._ensure_folders()
        assert self._summaries_folder_id is not None
        q = (
            f"name contains '{keyword.lower()}' "
            f"and '{self._summaries_folder_id}' in parents "
            f"and trashed = false"
        )
        resp = (
            self._drive.files()
            .list(q=q, pageSize=10, fields="files(id,name)")
            .execute()
        )
        results: list[dict] = []
        for f in resp.get("files", []):
            try:
                results.append(self._read_json(f["id"]))
            except Exception:
                continue
        return results

    # ══════════════════════════════════════════════════════════════
    #  High-level recall — the agent's "remember" ability
    # ══════════════════════════════════════════════════════════════

    def recall(self, query: str) -> dict[str, Any]:
        """Search across **all** memory layers for *query*.

        Searches:
          1. Conversation log (keyword match)
          2. Research summaries (filename match)
          3. Consolidated archive (keyword match)
          4. User profile (preferences & patterns)

        Returns a dict with keys ``conversations``, ``summaries``,
        ``consolidated``, and ``profile_context``.
        """
        self._ensure_folders()
        query_lower = query.lower()
        matches: dict[str, Any] = {
            "conversations": [],
            "summaries": [],
            "consolidated": [],
            "profile_context": "",
        }

        # 1. Recent conversation log
        for entry in self.load_conversation_log():
            if query_lower in json.dumps(entry).lower():
                matches["conversations"].append(entry)

        # 2. Research summaries
        matches["summaries"] = self.search_summaries(query)

        # 3. Consolidated archive
        for block in self._load_consolidated():
            block_text = json.dumps(block).lower()
            if query_lower in block_text:
                matches["consolidated"].append(block)

        # 4. Profile context
        profile = self.load_profile()
        profile_str = json.dumps(profile).lower()
        if query_lower in profile_str:
            matches["profile_context"] = (
                f"User has researched these topics before: "
                f"{profile.get('topics_of_interest', [])}.  "
                f"Frequently used folders: "
                f"{profile.get('frequently_used_folders', {})}.  "
                f"Total interactions: {profile.get('interaction_count', 0)}."
            )

        return matches

    def get_context_for_agent(self, max_recent: int = 10) -> str:
        """Build a rich context block the agent sees on every request.

        Combines:
          • Last N conversation entries (with timestamps)
          • User profile highlights (interaction count, fav topics, folders)
          • Deep summary (if available)

        Returns a formatted string ready for prompt injection.
        """
        parts: list[str] = []

        # Recent interactions
        recent = self.load_conversation_log()[-max_recent:]
        if recent:
            lines = []
            for e in recent:
                cmd = e.get("command", "?")
                summary = e.get("summary", "?")
                ts = e.get("timestamp", "")[:10]
                lines.append(f"  [{ts}] {cmd} → {summary}")
            parts.append("[RECENT INTERACTIONS]\n" + "\n".join(lines))

        # Profile
        profile = self.load_profile()
        interaction_count = profile.get("interaction_count", 0)
        topics = profile.get("topics_of_interest", [])
        folders = profile.get("frequently_used_folders", {})
        if interaction_count > 0:
            top_folders = sorted(
                folders.items(), key=lambda x: x[1], reverse=True
            )[:5]
            parts.append(
                f"[USER PROFILE]\n"
                f"  Total interactions: {interaction_count}\n"
                f"  Topics of interest: "
                f"{', '.join(topics[-10:]) or 'none yet'}\n"
                f"  Preferred folders: "
                f"{', '.join(f'{k} ({v}x)' for k, v in top_folders) or 'none yet'}"
            )

        # Deep summary
        self._ensure_folders()
        assert self._memory_folder_id is not None
        ds_id = self._find_file("deep_summary.json", self._memory_folder_id)
        if ds_id:
            try:
                ds = self._read_json(ds_id)
                parts.append(
                    f"[LONG-TERM MEMORY SUMMARY]\n"
                    f"  {ds.get('summary', '')[:500]}"
                )
            except Exception:
                pass

        if not parts:
            return "[No prior memory — this is a new user.]"

        return "\n\n".join(parts)


# ══════════════════════════════════════════════════════════════════
#  Module-level helpers
# ══════════════════════════════════════════════════════════════════


def _classify_command(command: str) -> str:
    """Rough classification of a user command for pattern analysis."""
    cmd = command.lower()
    if any(w in cmd for w in ("research", "write about", "article")):
        return "research"
    if any(w in cmd for w in ("create folder", "new folder", "mkdir")):
        return "folder_management"
    if any(w in cmd for w in ("create doc", "new doc", "new document")):
        return "doc_creation"
    if any(w in cmd for w in ("search", "find", "look for", "list")):
        return "search"
    if any(w in cmd for w in ("read", "open", "show", "get content")):
        return "read"
    if any(w in cmd for w in ("delete", "remove", "trash")):
        return "delete"
    return "general"
