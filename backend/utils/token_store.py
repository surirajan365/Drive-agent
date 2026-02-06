"""Encrypted storage for Google OAuth refresh tokens.

Tokens at rest are **never** stored in plaintext.  Each user's token bundle
is encrypted with Fernet (AES-128-CBC + HMAC-SHA256) and written to a
separate file keyed by user ID.

The encryption key is sourced from the ``ENCRYPTION_KEY`` environment variable.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_TOKEN_DIR = Path(__file__).resolve().parent.parent / ".tokens"


class TokenStore:
    """File-backed, Fernet-encrypted token store."""

    def __init__(self, encryption_key: str) -> None:
        """
        Args:
            encryption_key: A URL-safe base64 Fernet key (32 bytes encoded).
                            Generate with ``Fernet.generate_key()``.
        """
        key = (
            encryption_key.encode("utf-8")
            if isinstance(encryption_key, str)
            else encryption_key
        )
        self._fernet = Fernet(key)
        _TOKEN_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("Token store initialised → %s", _TOKEN_DIR)

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _safe_filename(user_id: str) -> str:
        """Turn an email into a filesystem-safe name."""
        return user_id.replace("@", "_at_").replace(".", "_dot_")

    def _path(self, user_id: str) -> Path:
        return _TOKEN_DIR / f"{self._safe_filename(user_id)}.enc"

    # ── Public API ────────────────────────────────────────────────

    def save(self, user_id: str, token_data: dict) -> None:
        """Encrypt and persist *token_data* for *user_id*."""
        payload = json.dumps(token_data).encode("utf-8")
        self._path(user_id).write_bytes(self._fernet.encrypt(payload))
        logger.debug("Tokens saved for user %s", user_id)

    def load(self, user_id: str) -> Optional[dict]:
        """Return decrypted token data or ``None`` if absent / corrupt."""
        path = self._path(user_id)
        if not path.exists():
            return None
        try:
            return json.loads(self._fernet.decrypt(path.read_bytes()))
        except (InvalidToken, json.JSONDecodeError) as exc:
            logger.warning("Cannot load tokens for %s: %s", user_id, exc)
            return None

    def delete(self, user_id: str) -> bool:
        """Remove stored tokens. Returns ``True`` if a file was deleted."""
        path = self._path(user_id)
        if path.exists():
            path.unlink()
            logger.info("Tokens deleted for %s", user_id)
            return True
        return False

    def exists(self, user_id: str) -> bool:
        """Check whether tokens are stored for *user_id*."""
        return self._path(user_id).exists()
