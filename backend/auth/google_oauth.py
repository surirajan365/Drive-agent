"""Google OAuth 2.0 authentication flow.

Handles the complete OAuth lifecycle:
  • Generating the Google consent URL
  • Exchanging the authorization code for tokens
  • Loading / refreshing credentials per user
  • Revoking access and cleaning up tokens
"""

import logging
from typing import Optional

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from backend.config import get_settings
from backend.utils.token_store import TokenStore

logger = logging.getLogger(__name__)


class GoogleOAuth:
    """Manages the full OAuth 2.0 lifecycle for each user."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._token_store = TokenStore(self._settings.ENCRYPTION_KEY)

    # ── Internal helpers ──────────────────────────────────────────

    def _client_config(self) -> dict:
        """Return the client config dict expected by ``google_auth_oauthlib``."""
        return {
            "web": {
                "client_id": self._settings.GOOGLE_CLIENT_ID,
                "client_secret": self._settings.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [self._settings.GOOGLE_REDIRECT_URI],
            }
        }

    def _build_flow(self) -> Flow:
        """Create a fresh ``Flow`` instance."""
        flow = Flow.from_client_config(
            client_config=self._client_config(),
            scopes=self._settings.GOOGLE_SCOPES,
        )
        flow.redirect_uri = self._settings.GOOGLE_REDIRECT_URI
        return flow

    # ── Public API ────────────────────────────────────────────────

    def get_authorization_url(self, state: Optional[str] = None) -> tuple[str, str]:
        """Return ``(auth_url, state)`` to redirect the user to Google consent.

        Args:
            state: Optional opaque value forwarded through the OAuth redirect.

        Returns:
            A tuple of *(authorization_url, state_token)*.
        """
        flow = self._build_flow()
        url, state_out = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=state,
        )
        return url, state_out

    def handle_callback(self, code: str) -> dict:
        """Exchange the authorization *code* for tokens.

        Tokens are encrypted and persisted.  Basic user info is fetched
        from the Google ``oauth2`` API.

        Returns:
            ``{"user_id": str, "email": str, "name": str}``
        """
        flow = self._build_flow()
        flow.fetch_token(code=code)
        creds = flow.credentials

        # Fetch the authenticated user's profile
        oauth2_service = build("oauth2", "v2", credentials=creds)
        user_info: dict = oauth2_service.userinfo().get().execute()

        user_id: str = user_info["email"]

        # Persist encrypted token bundle
        token_data = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes or []),
        }
        self._token_store.save(user_id, token_data)
        logger.info("Authenticated user %s", user_id)

        return {
            "user_id": user_id,
            "email": user_info.get("email", ""),
            "name": user_info.get("name", ""),
        }

    def get_credentials(self, user_id: str) -> Optional[Credentials]:
        """Return valid ``Credentials`` for *user_id*, refreshing if needed.

        Returns ``None`` when no tokens are stored or refresh fails.
        """
        token_data = self._token_store.load(user_id)
        if token_data is None:
            return None

        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get(
                "token_uri", "https://oauth2.googleapis.com/token"
            ),
            client_id=token_data.get("client_id"),
            client_secret=token_data.get("client_secret"),
            scopes=token_data.get("scopes"),
        )

        # Transparently refresh expired tokens
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(GoogleAuthRequest())
                token_data["token"] = creds.token
                self._token_store.save(user_id, token_data)
                logger.debug("Refreshed credentials for %s", user_id)
            except Exception:
                logger.exception("Token refresh failed for %s", user_id)
                return None

        return creds

    def revoke(self, user_id: str) -> bool:
        """Revoke the user's tokens with Google and delete local storage.

        Returns ``True`` on success (or if there was nothing to revoke).
        """
        creds = self.get_credentials(user_id)
        if creds and creds.token:
            try:
                httpx.post(
                    "https://oauth2.googleapis.com/revoke",
                    params={"token": creds.token},
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded"
                    },
                )
            except httpx.HTTPError:
                logger.warning("Revocation HTTP call failed for %s", user_id)

        self._token_store.delete(user_id)
        logger.info("Revoked credentials for %s", user_id)
        return True
