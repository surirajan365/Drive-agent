"""Google Drive API tools.

Every function in this module is **deterministic** and **side-effect-transparent**.
Each returns a structured ``dict`` with a ``success`` flag so the agent can
reason over the outcome.  No AI logic lives here.
"""

import logging
from typing import Any, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

FOLDER_MIME = "application/vnd.google-apps.folder"
DOC_MIME = "application/vnd.google-apps.document"


def _drive_service(credentials: Credentials):
    """Build a cached-discovery Drive v3 service."""
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


# ═══════════════════════════════════════════════════════════════════
#  Listing & Searching
# ═══════════════════════════════════════════════════════════════════


def list_files(
    credentials: Credentials,
    folder_id: str = "root",
    max_results: int = 25,
    file_type: Optional[str] = None,
) -> dict[str, Any]:
    """List files inside a Drive folder.

    Args:
        folder_id: The ID of the parent folder (``"root"`` for top-level).
        max_results: Maximum number of files to return.
        file_type: Optional MIME type filter.

    Returns:
        ``{"success": True, "files": [...], "count": int}``
    """
    try:
        service = _drive_service(credentials)
        query_parts = [f"'{folder_id}' in parents", "trashed = false"]
        if file_type:
            query_parts.append(f"mimeType = '{file_type}'")
        query = " and ".join(query_parts)

        response = (
            service.files()
            .list(
                q=query,
                pageSize=max_results,
                fields="files(id, name, mimeType, modifiedTime, webViewLink)",
                orderBy="modifiedTime desc",
            )
            .execute()
        )
        files = response.get("files", [])
        return {"success": True, "files": files, "count": len(files)}
    except HttpError as exc:
        logger.exception("list_files failed")
        return {"success": False, "error": str(exc)}


def search_files(
    credentials: Credentials,
    query: str,
    max_results: int = 15,
) -> dict[str, Any]:
    """Full-text and metadata search across the user's Drive.

    Args:
        query: Filename or keyword to search for.
        max_results: Cap on returned results.

    Returns:
        ``{"success": True, "files": [...], "count": int}``
    """
    try:
        service = _drive_service(credentials)
        q = f"name contains '{query}' and trashed = false"
        response = (
            service.files()
            .list(
                q=q,
                pageSize=max_results,
                fields="files(id, name, mimeType, modifiedTime, webViewLink, parents)",
                orderBy="modifiedTime desc",
            )
            .execute()
        )
        files = response.get("files", [])
        return {"success": True, "files": files, "count": len(files)}
    except HttpError as exc:
        logger.exception("search_files failed")
        return {"success": False, "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════
#  Creating
# ═══════════════════════════════════════════════════════════════════


def create_folder(
    credentials: Credentials,
    name: str,
    parent_id: str = "root",
) -> dict[str, Any]:
    """Create a new folder in Drive.

    Returns:
        ``{"success": True, "folder": {"id": ..., "name": ..., "webViewLink": ...}}``
    """
    try:
        service = _drive_service(credentials)
        metadata = {
            "name": name,
            "mimeType": FOLDER_MIME,
            "parents": [parent_id],
        }
        folder = (
            service.files()
            .create(body=metadata, fields="id, name, webViewLink")
            .execute()
        )
        logger.info("Created folder '%s' (id=%s)", name, folder["id"])
        return {"success": True, "folder": folder}
    except HttpError as exc:
        logger.exception("create_folder failed")
        return {"success": False, "error": str(exc)}


def get_or_create_folder(
    credentials: Credentials,
    name: str,
    parent_id: str = "root",
) -> dict[str, Any]:
    """Return an existing folder by *name* or create one.

    Returns:
        Dict with ``folder``, ``created`` (bool), and ``success``.
    """
    try:
        service = _drive_service(credentials)
        q = (
            f"name = '{name}' and mimeType = '{FOLDER_MIME}' "
            f"and '{parent_id}' in parents and trashed = false"
        )
        response = (
            service.files()
            .list(q=q, pageSize=1, fields="files(id, name, webViewLink)")
            .execute()
        )
        files = response.get("files", [])
        if files:
            return {"success": True, "folder": files[0], "created": False}
        result = create_folder(credentials, name, parent_id)
        result["created"] = True
        return result
    except HttpError as exc:
        logger.exception("get_or_create_folder failed")
        return {"success": False, "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════
#  Reading
# ═══════════════════════════════════════════════════════════════════


def read_file_content(
    credentials: Credentials,
    file_id: str,
) -> dict[str, Any]:
    """Export a Drive file (Docs, Sheets, etc.) as plain text.

    Returns:
        ``{"success": True, "content": str}``
    """
    try:
        service = _drive_service(credentials)
        content = (
            service.files()
            .export(fileId=file_id, mimeType="text/plain")
            .execute()
        )
        text = content.decode("utf-8") if isinstance(content, bytes) else content
        return {"success": True, "content": text}
    except HttpError as exc:
        logger.exception("read_file_content failed")
        return {"success": False, "error": str(exc)}


def get_file_metadata(
    credentials: Credentials,
    file_id: str,
) -> dict[str, Any]:
    """Return metadata for a single file."""
    try:
        service = _drive_service(credentials)
        meta = (
            service.files()
            .get(
                fileId=file_id,
                fields="id,name,mimeType,modifiedTime,webViewLink,parents,size",
            )
            .execute()
        )
        return {"success": True, "file": meta}
    except HttpError as exc:
        logger.exception("get_file_metadata failed")
        return {"success": False, "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════
#  Moving & Deleting
# ═══════════════════════════════════════════════════════════════════


def move_file(
    credentials: Credentials,
    file_id: str,
    new_parent_id: str,
) -> dict[str, Any]:
    """Move a file to a different folder."""
    try:
        service = _drive_service(credentials)
        current = service.files().get(fileId=file_id, fields="parents").execute()
        previous_parents = ",".join(current.get("parents", []))
        updated = (
            service.files()
            .update(
                fileId=file_id,
                addParents=new_parent_id,
                removeParents=previous_parents,
                fields="id, name, parents",
            )
            .execute()
        )
        return {"success": True, "file": updated}
    except HttpError as exc:
        logger.exception("move_file failed")
        return {"success": False, "error": str(exc)}


def delete_file(
    credentials: Credentials,
    file_id: str,
    permanent: bool = False,
) -> dict[str, Any]:
    """Trash (or permanently delete) a file.

    **Destructive** — the agent must request user confirmation first.
    """
    try:
        service = _drive_service(credentials)
        if permanent:
            service.files().delete(fileId=file_id).execute()
        else:
            service.files().update(
                fileId=file_id, body={"trashed": True}
            ).execute()
        return {"success": True, "file_id": file_id, "permanent": permanent}
    except HttpError as exc:
        logger.exception("delete_file failed")
        return {"success": False, "error": str(exc)}
