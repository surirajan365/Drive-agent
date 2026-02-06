"""FastAPI application — AI Drive Agent.

Endpoints
─────────
Auth:
    GET  /auth/login      → Google OAuth consent URL
    GET  /auth/callback   → Exchange code → JWT session token
    GET  /auth/status     → Check authentication state
    POST /auth/logout     → Revoke tokens & end session

Agent:
    POST /agent/command   → Execute a natural-language command
    POST /agent/preview   → Preview the action plan without executing
    POST /agent/confirm   → Confirm a pending destructive action
    POST /agent/reject    → Reject a pending destructive action
    GET  /agent/history   → Retrieve past interactions from memory

System:
    GET  /health          → Liveness / readiness probe
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from pydantic import BaseModel

from backend.agent.agent import DriveAgent
from backend.agent.memory import DriveMemory
from backend.auth.google_oauth import GoogleOAuth
from backend.config import get_settings
from backend.services.gemini import GeminiService

# ═══════════════════════════════════════════════════════════════════
#  Bootstrap
# ═══════════════════════════════════════════════════════════════════

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s │ %(levelname)-8s │ %(name)s │ %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description="AI agent that autonomously operates on your Google Drive.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Frontend static files ─────────────────────────────────────────
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

oauth = GoogleOAuth()
security = HTTPBearer(auto_error=False)


# ═══════════════════════════════════════════════════════════════════
#  JWT session helpers
# ═══════════════════════════════════════════════════════════════════


def _create_token(user_id: str) -> str:
    """Sign a short-lived JWT containing the user's identity."""
    payload = {
        "sub": user_id,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc)
        + timedelta(hours=settings.JWT_EXPIRY_HOURS),
    }
    return jwt.encode(
        payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM
    )


def _decode_token(token: str) -> str:
    """Validate and extract user_id from a JWT."""
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        user_id: str = payload.get("sub", "")
        if not user_id:
            raise HTTPException(401, "Invalid token payload")
        return user_id
    except JWTError as exc:
        raise HTTPException(401, f"Token error: {exc}")


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> str:
    """FastAPI dependency — resolves the authenticated user ID."""
    if credentials is None:
        raise HTTPException(401, "Missing Authorization header")
    return _decode_token(credentials.credentials)


# ═══════════════════════════════════════════════════════════════════
#  Request / response schemas
# ═══════════════════════════════════════════════════════════════════


class CommandRequest(BaseModel):
    """Payload for ``/agent/command`` and ``/agent/preview``."""
    command: str
    chat_history: list[dict] = []


class ConfirmRequest(BaseModel):
    """Payload for ``/agent/confirm`` and ``/agent/reject``."""
    action_id: str


class CommandResponse(BaseModel):
    """Unified response envelope for all agent endpoints."""
    status: str
    result: str = ""
    steps: list[dict] = []
    action_id: str = ""
    preview: list[dict] = []
    message: str = ""


# ═══════════════════════════════════════════════════════════════════
#  Health
# ═══════════════════════════════════════════════════════════════════


@app.get("/health", tags=["system"])
def health_check():
    """Liveness probe."""
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": "1.0.0",
    }


# ═══════════════════════════════════════════════════════════════════
#  Auth
# ═══════════════════════════════════════════════════════════════════


@app.get("/auth/login", tags=["auth"])
def auth_login(redirect: Optional[str] = Query(None)):
    """Return the Google OAuth consent URL the client should redirect to."""
    url, state = oauth.get_authorization_url(state=redirect)
    return {"authorization_url": url, "state": state}


@app.get("/auth/callback", tags=["auth"])
def auth_callback(code: str, state: Optional[str] = None):
    """Exchange the authorization *code* for tokens and return a JWT.

    If the request came from the web UI (Accept: text/html or no
    explicit JSON preference), redirect back to the frontend with
    the token in a URL fragment so JavaScript can capture it.
    Otherwise return plain JSON for API consumers.
    """
    try:
        user_info = oauth.handle_callback(code)
        jwt_token = _create_token(user_info["user_id"])

        import urllib.parse, json as _json
        frag = urllib.parse.urlencode({
            "access_token": jwt_token,
            "user": _json.dumps(user_info),
        })
        return RedirectResponse(url=f"/?{frag}")
    except Exception as exc:
        logger.exception("OAuth callback failed")
        raise HTTPException(400, detail=str(exc))


@app.get("/auth/status", tags=["auth"])
def auth_status(user_id: str = Depends(get_current_user)):
    """Check whether Google credentials are still valid."""
    creds = oauth.get_credentials(user_id)
    return {
        "authenticated": creds is not None,
        "user_id": user_id,
    }


@app.post("/auth/logout", tags=["auth"])
def auth_logout(user_id: str = Depends(get_current_user)):
    """Revoke Google tokens and clear server-side storage."""
    oauth.revoke(user_id)
    return {"message": "Logged out successfully"}


# ═══════════════════════════════════════════════════════════════════
#  Agent
# ═══════════════════════════════════════════════════════════════════


def _get_agent(user_id: str) -> DriveAgent:
    """Resolve Google credentials and instantiate a per-request agent."""
    creds = oauth.get_credentials(user_id)
    if creds is None:
        raise HTTPException(
            401, "Google credentials expired — please re-authenticate."
        )
    return DriveAgent(creds, user_id)


@app.post("/agent/command", response_model=CommandResponse, tags=["agent"])
def agent_command(
    req: CommandRequest,
    user_id: str = Depends(get_current_user),
):
    """Execute a natural-language command via the autonomous agent.

    The agent will reason, plan, and invoke tools to fulfil the request.
    """
    agent = _get_agent(user_id)
    result = agent.execute(req.command, req.chat_history)
    return CommandResponse(**result)


@app.post("/agent/preview", response_model=CommandResponse, tags=["agent"])
def agent_preview(
    req: CommandRequest,
    user_id: str = Depends(get_current_user),
):
    """Preview the action plan the agent *would* execute, without
    actually performing any Drive operations.

    Uses the Gemini planner to generate a structured JSON plan.
    """
    gemini = GeminiService()
    plan_text = gemini.plan_actions(req.command)
    return CommandResponse(
        status="preview",
        result=plan_text,
        message="This is a preview. No actions were taken.",
    )


@app.post("/agent/confirm", response_model=CommandResponse, tags=["agent"])
def agent_confirm(
    req: ConfirmRequest,
    user_id: str = Depends(get_current_user),
):
    """Confirm and execute a previously staged destructive action."""
    action = DriveAgent.confirm_action(req.action_id, user_id)
    if action is None:
        raise HTTPException(404, "Pending action not found or expired")

    agent = _get_agent(user_id)
    result = agent.execute(action["command"])
    return CommandResponse(**result)


@app.post("/agent/reject", tags=["agent"])
def agent_reject(
    req: ConfirmRequest,
    user_id: str = Depends(get_current_user),
):
    """Reject and discard a previously staged destructive action."""
    if not DriveAgent.reject_action(req.action_id, user_id):
        raise HTTPException(404, "Pending action not found")
    return {"message": "Action rejected and discarded"}


@app.get("/agent/history", tags=["agent"])
def agent_history(user_id: str = Depends(get_current_user)):
    """Retrieve the conversation history stored in Drive memory."""
    creds = oauth.get_credentials(user_id)
    if creds is None:
        raise HTTPException(401, "Credentials expired")

    memory = DriveMemory(creds)
    log = memory.load_conversation_log()
    return {"history": log, "count": len(log)}


# ═══════════════════════════════════════════════════════════════════
#  Memory management
# ═══════════════════════════════════════════════════════════════════


@app.get("/agent/memory/profile", tags=["memory"])
def get_profile(user_id: str = Depends(get_current_user)):
    """Return the learned user profile (preferences, topics, patterns)."""
    creds = oauth.get_credentials(user_id)
    if creds is None:
        raise HTTPException(401, "Credentials expired")

    memory = DriveMemory(creds)
    return memory.load_profile()


@app.get("/agent/memory/recall", tags=["memory"])
def recall_memory(
    query: str = Query(..., description="Topic or keyword to search memory for"),
    user_id: str = Depends(get_current_user),
):
    """Search across all memory layers for a given keyword."""
    creds = oauth.get_credentials(user_id)
    if creds is None:
        raise HTTPException(401, "Credentials expired")

    memory = DriveMemory(creds)
    return memory.recall(query)


@app.get("/agent/memory/context", tags=["memory"])
def get_memory_context(user_id: str = Depends(get_current_user)):
    """Return the full memory context block the agent sees each request."""
    creds = oauth.get_credentials(user_id)
    if creds is None:
        raise HTTPException(401, "Credentials expired")

    memory = DriveMemory(creds)
    return {"context": memory.get_context_for_agent()}


@app.post("/agent/memory/consolidate", tags=["memory"])
def consolidate_memory(user_id: str = Depends(get_current_user)):
    """Trigger a deep LLM-based consolidation of archived memory.

    This summarises the oldest conversation batches into a rich long-term
    memory summary stored in the user's Drive.
    """
    creds = oauth.get_credentials(user_id)
    if creds is None:
        raise HTTPException(401, "Credentials expired")

    memory = DriveMemory(creds)
    gemini = GeminiService()
    summary = memory.deep_consolidate(gemini.summarise)
    return {"summary": summary}


# ═══════════════════════════════════════════════════════════════════
#  Frontend
# ═══════════════════════════════════════════════════════════════════


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def serve_index():
    """Serve the frontend SPA."""
    index = FRONTEND_DIR / "index.html"
    return HTMLResponse(content=index.read_text(encoding="utf-8"))


# Static assets (CSS, JS) — must be mounted last so it doesn't
# shadow API routes.
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend")

