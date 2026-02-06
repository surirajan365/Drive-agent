# AI Drive Agent

> An autonomous AI agent that operates on your Google Drive — creates folders, researches topics, writes structured Google Docs, and retains long-term memory across sessions.

---

## 1 · System Architecture

```
┌─────────────┐    HTTP/JSON     ┌────────────────────────────────────────┐
│   Client     │ ◄──────────────► │           FastAPI Backend              │
│  (any HTTP   │                  │                                        │
│   client)    │                  │  ┌──────────┐  ┌───────────────────┐   │
└─────────────┘                  │  │ Auth     │  │ Agent Executor    │   │
                                 │  │ (OAuth)  │  │  LLM + Tools +    │   │
                                 │  └────┬─────┘  │  Memory           │   │
                                 │       │        └─────┬─────────────┘   │
                                 │       │              │                  │
                                 └───────┼──────────────┼──────────────────┘
                                         │              │
                              ┌──────────▼──────────────▼──────────┐
                              │        Google Cloud APIs            │
                              │  ┌──────────┐  ┌───────────────┐   │
                              │  │ Drive v3 │  │ Docs v1       │   │
                              │  └──────────┘  └───────────────┘   │
                              │  ┌──────────┐  ┌───────────────┐   │
                              │  │ OAuth2   │  │ Gemini API    │   │
                              │  └──────────┘  └───────────────┘   │
                              └────────────────────────────────────┘
```

### Agent = LLM + Memory + Tools

| Component | Responsibility |
|-----------|---------------|
| **LLM** (Gemini) | Reasoning, planning, research, summarisation |
| **Tools** | Deterministic Google Drive / Docs operations |
| **Memory** | Persistent storage in Google Drive (`/AI_AGENT_MEMORY/`) |

---

## 2 · API Flow

```
1. Client → GET  /auth/login          → receives Google consent URL
2. User   → Google consent screen     → grants access
3. Google → GET  /auth/callback?code=… → backend exchanges code for tokens
4. Backend→ returns JWT session token  → client stores it
5. Client → POST /agent/command        → sends natural-language instruction
             { "command": "Research AI and create a doc in ai-research folder" }
6. Agent  → reasons → calls tools → returns result with links
7. Client → GET  /agent/history        → retrieves past interactions
```

---

## 3 · Project Structure

```
ai-agent/
├── .env.example              ← environment template
├── .gitignore
├── README.md
└── backend/
    ├── main.py               ← FastAPI app + all HTTP endpoints
    ├── config.py             ← Pydantic settings (env-driven)
    ├── __init__.py
    ├── auth/
    │   ├── __init__.py
    │   └── google_oauth.py   ← OAuth 2.0 flow + credential mgmt
    ├── agent/
    │   ├── __init__.py
    │   ├── agent.py          ← DriveAgent (LangChain executor)
    │   ├── prompt.py         ← system & helper prompts
    │   └── memory.py         ← Drive-backed persistent memory
    ├── tools/
    │   ├── __init__.py
    │   ├── drive_tools.py    ← list / search / create / delete
    │   └── docs_tools.py     ← create / write / read Google Docs
    ├── services/
    │   ├── __init__.py
    │   └── gemini.py         ← Gemini SDK wrapper + LangChain factory
    ├── utils/
    │   ├── __init__.py
    │   └── token_store.py    ← Fernet-encrypted token persistence
    └── requirements.txt
```

---

## 4 · Setup & Run Guide

### 4.1 Prerequisites

- **Python 3.11+**
- A **Google Cloud project** with the following APIs enabled:
  - Google Drive API
  - Google Docs API
  - Google People API (for userinfo)
- A **Gemini API key** from [Google AI Studio](https://aistudio.google.com/apikey)

### 4.2 Google Cloud Configuration

1. Go to [Google Cloud Console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials).
2. Create an **OAuth 2.0 Client ID** (type: *Web application*).
3. Add `http://localhost:8000/auth/callback` as an **Authorized redirect URI**.
4. Download the client ID and secret.
5. Under **APIs & Services → Library**, enable:
   - *Google Drive API*
   - *Google Docs API*
6. If you see a "Publishing status: Testing" banner, add your Google account as a **test user** under the OAuth consent screen.

### 4.3 Environment Setup

```bash
# Clone / enter the project
cd ai-agent

# Create a virtual environment
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# Install dependencies
pip install -r backend/requirements.txt

# Create your .env from the template
copy .env.example .env        # Windows
# cp .env.example .env        # macOS / Linux
```

Edit `.env` and fill in the real values:

| Variable | How to obtain |
|----------|--------------|
| `GOOGLE_CLIENT_ID` | OAuth 2.0 credential in Cloud Console |
| `GOOGLE_CLIENT_SECRET` | Same credential |
| `GOOGLE_REDIRECT_URI` | `http://localhost:8000/auth/callback` |
| `GEMINI_API_KEY` | Google AI Studio |
| `ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `JWT_SECRET` | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |

### 4.4 Run the Server

```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000/docs** for the interactive Swagger UI.

---

## 5 · Example Usage

### 5.1 Authenticate

```bash
# 1. Get the Google consent URL
curl http://localhost:8000/auth/login

# Response:
# { "authorization_url": "https://accounts.google.com/o/oauth2/auth?...", "state": "..." }

# 2. Open that URL in a browser, sign in, grant access.
#    Google redirects to /auth/callback?code=…
#    The backend returns a JWT:
# { "access_token": "eyJ...", "token_type": "bearer", "user": { ... } }
```

### 5.2 Execute a Command

```bash
curl -X POST http://localhost:8000/agent/command \
  -H "Authorization: Bearer eyJ..." \
  -H "Content-Type: application/json" \
  -d '{"command": "Research data science and create a doc in a folder named datascience"}'
```

**What the agent does internally:**
1. Searches Drive for a folder named *datascience*
2. Creates the folder if it doesn't exist
3. Researches "data science" via Gemini → structured Markdown
4. Creates a new Google Doc titled *Data Science Research*
5. Writes the research content with headings into the Doc
6. Saves a summary to long-term memory
7. Returns the Doc link and a summary to the client

### 5.3 Preview Before Executing

```bash
curl -X POST http://localhost:8000/agent/preview \
  -H "Authorization: Bearer eyJ..." \
  -H "Content-Type: application/json" \
  -d '{"command": "Delete everything in my old-projects folder"}'
```

### 5.4 Retrieve History

```bash
curl http://localhost:8000/agent/history \
  -H "Authorization: Bearer eyJ..."
```

---

## 6 · Security Considerations

| Concern | Mitigation |
|---------|-----------|
| **Token storage** | Refresh tokens are AES-encrypted at rest via Fernet. Encryption key is in env vars, never in code. |
| **Frontend tokens** | The client receives only a short-lived JWT. Google tokens never leave the backend. |
| **Minimal scopes** | Only `drive` and `documents` scopes are requested — the minimum for full agent capability. |
| **Destructive ops** | Delete / overwrite operations can be staged via `/agent/preview` → `/agent/confirm` flow. |
| **CORS** | Currently `allow_origins=["*"]` — **must** be restricted to your frontend domain in production. |
| **Token revocation** | `/auth/logout` revokes the token with Google's endpoint and deletes local encrypted storage. |
| **No hard-coded secrets** | All credentials are loaded from environment variables via Pydantic Settings. |

---

## 7 · Memory Structure (in Google Drive)

```
/AI_AGENT_MEMORY/
├── profile.json              ← user preferences, metadata
├── conversation_log.json     ← rolling log of summarised interactions (capped at 200)
└── summaries/
    ├── data_science.json     ← research summary
    ├── machine_learning.json
    └── ...
```

- Memory is **never** stored on the local filesystem.
- The agent reads recent memory before every command for context continuity.
- The conversation log is automatically pruned to prevent unbounded growth.

---

## 8 · Future Improvements (v2)

| Feature | Description |
|---------|------------|
| **Redis-backed pending actions** | Replace the in-process dict with Redis for multi-instance deployments. |
| **Streaming responses** | Use Server-Sent Events (SSE) so the client sees tool calls in real time. |
| **Voice input** | Accept audio via Whisper or Google Speech-to-Text before passing to the agent. |
| **Google Sheets support** | Add tools for creating and editing spreadsheets. |
| **Rate limiting** | Per-user request throttling to prevent API quota exhaustion. |
| **Webhook / scheduled tasks** | Let the agent run periodic jobs (e.g., weekly summaries). |
| **Multi-user database** | Move token storage from file-based Fernet to a proper encrypted DB (Vault, Cloud KMS). |
| **Fine-grained scopes** | Use `drive.file` instead of `drive` where possible, prompting for broader access only when needed. |
| **Frontend UI** | React / Next.js dashboard with chat interface and Drive file browser. |
| **Observability** | OpenTelemetry tracing for every agent run, including per-tool latency. |

---

## License

MIT — see `LICENSE` for details.
