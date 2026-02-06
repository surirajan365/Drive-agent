"""Google Docs API tools.

Handles creation, reading, and writing of Google Docs with structured
content.  A lightweight Markdown-to-Docs converter translates ``#``
headings into native Google Docs heading styles.
"""

import logging
import re
from typing import Any, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

DOC_MIME = "application/vnd.google-apps.document"


def _docs_service(credentials: Credentials):
    return build("docs", "v1", credentials=credentials, cache_discovery=False)


def _drive_service(credentials: Credentials):
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


# ═══════════════════════════════════════════════════════════════════
#  Markdown → Docs batch-update request builder
# ═══════════════════════════════════════════════════════════════════


def _markdown_to_docs_requests(text: str) -> list[dict]:
    """Convert simplified Markdown into Docs ``batchUpdate`` requests.

    Supported syntax:
        # Heading 1   → HEADING_1
        ## Heading 2  → HEADING_2
        ### Heading 3 → HEADING_3
        Plain text    → NORMAL_TEXT
    """
    lines = text.split("\n")
    requests: list[dict] = []
    index = 1  # Google Docs body starts at index 1

    for line in lines:
        heading_match = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading_match:
            level = len(heading_match.group(1))
            content = heading_match.group(2).strip() + "\n"
            style = f"HEADING_{level}"
        else:
            content = line + "\n"
            style = "NORMAL_TEXT"

        # Insert the text
        requests.append(
            {"insertText": {"location": {"index": index}, "text": content}}
        )
        end_index = index + len(content)

        # Apply paragraph style
        requests.append(
            {
                "updateParagraphStyle": {
                    "range": {"startIndex": index, "endIndex": end_index},
                    "paragraphStyle": {"namedStyleType": style},
                    "fields": "namedStyleType",
                }
            }
        )
        index = end_index

    return requests


# ═══════════════════════════════════════════════════════════════════
#  Core operations
# ═══════════════════════════════════════════════════════════════════


def create_document(
    credentials: Credentials,
    title: str,
    folder_id: Optional[str] = None,
) -> dict[str, Any]:
    """Create a new Google Doc, optionally inside *folder_id*.

    Returns:
        ``{"success": True, "document_id": str, "title": str, "link": str}``
    """
    try:
        docs = _docs_service(credentials)
        doc = docs.documents().create(body={"title": title}).execute()
        doc_id: str = doc["documentId"]

        # Move into the target folder when specified
        if folder_id:
            drive = _drive_service(credentials)
            drive.files().update(
                fileId=doc_id,
                addParents=folder_id,
                removeParents="root",
                fields="id, parents",
            ).execute()

        link = f"https://docs.google.com/document/d/{doc_id}/edit"
        logger.info("Created document '%s' (id=%s)", title, doc_id)
        return {
            "success": True,
            "document_id": doc_id,
            "title": title,
            "link": link,
        }
    except HttpError as exc:
        logger.exception("create_document failed")
        return {"success": False, "error": str(exc)}


def write_to_document(
    credentials: Credentials,
    document_id: str,
    content: str,
) -> dict[str, Any]:
    """Write Markdown-formatted *content* to a Google Doc.

    The existing body is **cleared** first so the document is overwritten.
    Use ``append_to_document`` if you want to add content.

    Supports ``# H1``, ``## H2``, ``### H3`` heading syntax.
    """
    try:
        docs = _docs_service(credentials)

        # ── Clear existing content ────────────────────────────────
        doc = docs.documents().get(documentId=document_id).execute()
        body_content = doc.get("body", {}).get("content", [])
        end_index = body_content[-1].get("endIndex", 1) if body_content else 1

        all_requests: list[dict] = []
        if end_index > 2:
            all_requests.append(
                {
                    "deleteContentRange": {
                        "range": {"startIndex": 1, "endIndex": end_index - 1}
                    }
                }
            )

        # ── Insert new content ────────────────────────────────────
        all_requests.extend(_markdown_to_docs_requests(content))

        if all_requests:
            docs.documents().batchUpdate(
                documentId=document_id,
                body={"requests": all_requests},
            ).execute()

        logger.info("Wrote %d chars to document %s", len(content), document_id)
        return {
            "success": True,
            "document_id": document_id,
            "characters_written": len(content),
        }
    except HttpError as exc:
        logger.exception("write_to_document failed")
        return {"success": False, "error": str(exc)}


def append_to_document(
    credentials: Credentials,
    document_id: str,
    content: str,
) -> dict[str, Any]:
    """Append Markdown-formatted *content* to the **end** of a Google Doc."""
    try:
        docs = _docs_service(credentials)

        # Find the current end of the document
        doc = docs.documents().get(documentId=document_id).execute()
        body_content = doc.get("body", {}).get("content", [])
        end_index = body_content[-1].get("endIndex", 1) if body_content else 1
        insert_at = max(end_index - 1, 1)

        # Build insert requests starting at the end
        lines = content.split("\n")
        requests: list[dict] = []
        index = insert_at

        for line in lines:
            heading_match = re.match(r"^(#{1,3})\s+(.+)$", line)
            if heading_match:
                level = len(heading_match.group(1))
                text = heading_match.group(2).strip() + "\n"
                style = f"HEADING_{level}"
            else:
                text = line + "\n"
                style = "NORMAL_TEXT"

            requests.append(
                {"insertText": {"location": {"index": index}, "text": text}}
            )
            new_end = index + len(text)
            requests.append(
                {
                    "updateParagraphStyle": {
                        "range": {"startIndex": index, "endIndex": new_end},
                        "paragraphStyle": {"namedStyleType": style},
                        "fields": "namedStyleType",
                    }
                }
            )
            index = new_end

        if requests:
            docs.documents().batchUpdate(
                documentId=document_id,
                body={"requests": requests},
            ).execute()

        return {
            "success": True,
            "document_id": document_id,
            "characters_appended": len(content),
        }
    except HttpError as exc:
        logger.exception("append_to_document failed")
        return {"success": False, "error": str(exc)}


def read_document(
    credentials: Credentials,
    document_id: str,
) -> dict[str, Any]:
    """Read the full plain-text content of a Google Doc.

    Returns:
        ``{"success": True, "title": str, "document_id": str, "content": str}``
    """
    try:
        docs = _docs_service(credentials)
        doc = docs.documents().get(documentId=document_id).execute()
        title = doc.get("title", "")

        # Walk structural elements to extract text
        text_parts: list[str] = []
        for element in doc.get("body", {}).get("content", []):
            paragraph = element.get("paragraph")
            if paragraph:
                for elem in paragraph.get("elements", []):
                    text_run = elem.get("textRun")
                    if text_run:
                        text_parts.append(text_run.get("content", ""))

        full_text = "".join(text_parts)
        return {
            "success": True,
            "title": title,
            "document_id": document_id,
            "content": full_text,
        }
    except HttpError as exc:
        logger.exception("read_document failed")
        return {"success": False, "error": str(exc)}
