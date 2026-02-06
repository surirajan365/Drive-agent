/* ═══════════════════════════════════════════════════════════════════
   AI Drive Agent — Frontend Application
   ═══════════════════════════════════════════════════════════════════ */

const API = window.location.origin;

// ── State ────────────────────────────────────────────────────────
let token = localStorage.getItem("agent_token") || null;
let userInfo = JSON.parse(localStorage.getItem("agent_user") || "null");
let chatHistory = [];
let isSending = false;

// ── DOM refs ─────────────────────────────────────────────────────
const $ = (s) => document.querySelector(s);
const loginOverlay = $("#loginOverlay");
const chatArea = $("#chatArea");
const messages = $("#messages");
const welcomeScreen = $("#welcomeScreen");
const input = $("#messageInput");
const sendBtn = $("#sendBtn");
const loginBtn = $("#loginBtn");
const logoutBtn = $("#logoutBtn");
const newChatBtn = $("#newChatBtn");
const userInfoEl = $("#userInfo");
const userName = $("#userName");
const userEmail = $("#userEmail");
const userAvatar = $("#userAvatar");
const statusDot = $("#statusDot");
const sidebarToggle = $("#sidebarToggle");
const sidebar = $("#sidebar");

// ═══════════════════════════════════════════════════════════════════
//  Auth
// ═══════════════════════════════════════════════════════════════════

function saveSession(data) {
    token = data.access_token;
    userInfo = data.user;
    localStorage.setItem("agent_token", token);
    localStorage.setItem("agent_user", JSON.stringify(userInfo));
}

function clearSession() {
    token = null;
    userInfo = null;
    chatHistory = [];
    localStorage.removeItem("agent_token");
    localStorage.removeItem("agent_user");
}

async function checkAuth() {
    if (!token) return showLogin();
    try {
        const res = await fetch(`${API}/auth/status`, {
            headers: {
                Authorization: `Bearer ${token}`
            },
        });
        if (!res.ok) throw new Error("Unauthorized");
        const data = await res.json();
        if (!data.authenticated) throw new Error("Not authenticated");
        showChat();
    } catch {
        clearSession();
        showLogin();
    }
}

function showLogin() {
    loginOverlay.classList.remove("hidden");
    chatArea.classList.add("hidden");
    userInfoEl.classList.add("hidden");
    statusDot.className = "status-dot offline";
}

function showChat() {
    loginOverlay.classList.add("hidden");
    chatArea.classList.remove("hidden");
    statusDot.className = "status-dot online";

    if (userInfo) {
        userInfoEl.classList.remove("hidden");
        userName.textContent = userInfo.name || userInfo.email || "User";
        userEmail.textContent = userInfo.email || "";
        userAvatar.textContent = (userInfo.name || userInfo.email || "U")[0].toUpperCase();
    }
    input.focus();
}

// ── Login flow ───────────────────────────────────────────────────

loginBtn.addEventListener("click", async () => {
    try {
        const res = await fetch(`${API}/auth/login`);
        const data = await res.json();
        // Open Google consent in same window
        window.location.href = data.authorization_url;
    } catch (err) {
        alert("Failed to start login: " + err.message);
    }
});

// Handle OAuth callback — server redirects to /auth/callback,
// which returns JSON. We need to intercept that.
// Instead, we'll open in a popup or handle via query params.
// The cleanest approach: the callback page will post a message.

async function handleOAuthCallback() {
    // The server redirects back to /?access_token=...&user=...
    const url = new URL(window.location.href);
    const tkn = url.searchParams.get("access_token");
    const rawUsr = url.searchParams.get("user");

    if (!tkn) return false;

    try {
        const usr = JSON.parse(rawUsr || "{}");
        saveSession({
            access_token: tkn,
            user: usr
        });
        // Clean URL
        window.history.replaceState({}, document.title, "/");
        showChat();
        return true;
    } catch (err) {
        alert("Authentication failed: " + err.message);
        showLogin();
        return false;
    }
}

logoutBtn.addEventListener("click", async () => {
    try {
        await fetch(`${API}/auth/logout`, {
            method: "POST",
            headers: {
                Authorization: `Bearer ${token}`
            },
        });
    } catch {
        /* ignore */
    }
    clearSession();
    messages.innerHTML = "";
    messages.appendChild(welcomeScreen);
    welcomeScreen.classList.remove("hidden");
    showLogin();
});

// ═══════════════════════════════════════════════════════════════════
//  Chat
// ═══════════════════════════════════════════════════════════════════

function addMessage(role, content, steps) {
    welcomeScreen.classList.add("hidden");

    const row = document.createElement("div");
    row.className = `msg-row ${role}`;

    const avatar = document.createElement("div");
    avatar.className = "msg-avatar";
    avatar.textContent = role === "user" ?
        ((userInfo && userInfo.name) || "U")[0].toUpperCase() :
        "⚡";

    const bubble = document.createElement("div");
    bubble.className = "msg-bubble";
    bubble.innerHTML = formatMarkdown(content);

    // Add steps if present
    if (steps && steps.length > 0) {
        const details = document.createElement("details");
        details.className = "msg-steps";
        details.innerHTML = `<summary>🔧 ${steps.length} tool call${steps.length > 1 ? "s" : ""}</summary>` +
            steps.map(s => `<div class="step-item">${escapeHtml(typeof s === "string" ? s : JSON.stringify(s))}</div>`).join("");
        bubble.appendChild(details);
    }

    row.appendChild(avatar);
    row.appendChild(bubble);
    messages.appendChild(row);
    scrollToBottom();
}

function addTypingIndicator() {
    const row = document.createElement("div");
    row.className = "msg-row agent";
    row.id = "typingRow";

    const avatar = document.createElement("div");
    avatar.className = "msg-avatar";
    avatar.textContent = "⚡";

    const bubble = document.createElement("div");
    bubble.className = "msg-bubble";
    bubble.innerHTML = `<div class="typing-indicator"><span></span><span></span><span></span></div>`;

    row.appendChild(avatar);
    row.appendChild(bubble);
    messages.appendChild(row);
    scrollToBottom();
}

function removeTypingIndicator() {
    const el = document.getElementById("typingRow");
    if (el) el.remove();
}

async function sendMessage(text) {
    if (!text.trim() || isSending) return;
    isSending = true;
    sendBtn.disabled = true;
    input.value = "";
    autoResize();

    addMessage("user", text);
    chatHistory.push({
        role: "user",
        content: text
    });
    addTypingIndicator();

    try {
        const res = await fetch(`${API}/agent/command`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                Authorization: `Bearer ${token}`,
            },
            body: JSON.stringify({
                command: text,
                chat_history: chatHistory.slice(-10),
            }),
        });

        removeTypingIndicator();

        if (res.status === 401) {
            clearSession();
            showLogin();
            addMessage("agent", "⚠️ Session expired — please sign in again.");
            return;
        }

        const data = await res.json();
        const answer = data.result || data.message || "Done.";
        addMessage("agent", answer, data.steps);
        chatHistory.push({
            role: "assistant",
            content: answer
        });

    } catch (err) {
        removeTypingIndicator();
        addMessage("agent", `❌ Network error: ${err.message}. Please try again.`);
    } finally {
        isSending = false;
        sendBtn.disabled = !input.value.trim();
        input.focus();
    }
}

// ═══════════════════════════════════════════════════════════════════
//  Markdown-lite renderer
// ═══════════════════════════════════════════════════════════════════

function escapeHtml(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function formatMarkdown(text) {
    if (!text) return "";
    let html = escapeHtml(text);

    // Code blocks
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // Italic
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    // Links — make raw URLs clickable
    html = html.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
    // Bullet lists
    html = html.replace(/^[\*\-]\s+(.+)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');
    // Newlines to <br> (but not inside <pre>)
    html = html.replace(/\n/g, '<br>');

    return html;
}

// ═══════════════════════════════════════════════════════════════════
//  Event listeners
// ═══════════════════════════════════════════════════════════════════

// Send
sendBtn.addEventListener("click", () => sendMessage(input.value));

input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage(input.value);
    }
});

// Auto-resize textarea
function autoResize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 160) + "px";
}
input.addEventListener("input", () => {
    autoResize();
    sendBtn.disabled = !input.value.trim() || isSending;
});

// Quick actions & chips
document.querySelectorAll(".quick-action, .chip").forEach((el) => {
    el.addEventListener("click", () => {
        const cmd = el.dataset.cmd;
        if (cmd) sendMessage(cmd);
    });
});

// New chat
newChatBtn.addEventListener("click", () => {
    chatHistory = [];
    messages.querySelectorAll(".msg-row").forEach(el => el.remove());
    welcomeScreen.classList.remove("hidden");
    messages.prepend(welcomeScreen);
    input.focus();
});

// Sidebar toggle
sidebarToggle.addEventListener("click", () => {
    sidebar.classList.toggle("collapsed");
});

// Scroll helper
function scrollToBottom() {
    requestAnimationFrame(() => {
        messages.scrollTop = messages.scrollHeight;
    });
}

// ═══════════════════════════════════════════════════════════════════
//  Voice Assistant (Web Speech API)
// ═══════════════════════════════════════════════════════════════════

const voiceBtn = $("#voiceBtn");
const ttsToggle = $("#ttsToggle");
let isListening = false;
let recognition = null;
let ttsEnabled = localStorage.getItem("agent_tts") === "true";
let transcriptEl = null;

// ── Initialise TTS toggle state ──────────────────────────────────
if (ttsEnabled) ttsToggle.classList.add("active");

ttsToggle.addEventListener("click", () => {
    ttsEnabled = !ttsEnabled;
    localStorage.setItem("agent_tts", ttsEnabled);
    ttsToggle.classList.toggle("active", ttsEnabled);
    if (!ttsEnabled) window.speechSynthesis.cancel();
});

// ── Text-to-Speech helper ────────────────────────────────────────
function speakText(text) {
    if (!ttsEnabled || !window.speechSynthesis) return;
    window.speechSynthesis.cancel();

    // Strip markdown / HTML-ish stuff for cleaner speech
    const clean = text
        .replace(/```[\s\S]*?```/g, "code block omitted")
        .replace(/`([^`]+)`/g, "$1")
        .replace(/\*\*(.+?)\*\*/g, "$1")
        .replace(/\*(.+?)\*/g, "$1")
        .replace(/https?:\/\/[^\s]+/g, "link")
        .replace(/[#\-\*>]/g, "")
        .trim();

    if (!clean) return;

    // Split into chunks (speechSynthesis has ~200-char limits in some browsers)
    const chunks = clean.match(/[^.!?\n]{1,180}[.!?\n]?/g) || [clean];
    chunks.forEach((chunk, i) => {
        const utter = new SpeechSynthesisUtterance(chunk.trim());
        utter.rate = 1.05;
        utter.pitch = 1;
        utter.lang = "en-US";
        window.speechSynthesis.speak(utter);
    });
}

// ── Speech-to-Text (SpeechRecognition) ───────────────────────────
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

if (!SpeechRecognition) {
    // Browser doesn't support it — hide the mic button
    voiceBtn.style.display = "none";
}

function showTranscript(text) {
    if (!transcriptEl) {
        transcriptEl = document.createElement("div");
        transcriptEl.className = "voice-transcript";
        transcriptEl.innerHTML = `
            <div class="voice-wave"><span></span><span></span><span></span><span></span><span></span></div>
            <span class="voice-text">Listening…</span>
        `;
        const wrapper = $(".input-wrapper");
        wrapper.style.position = "relative";
        wrapper.appendChild(transcriptEl);
    }
    transcriptEl.querySelector(".voice-text").textContent = text || "Listening…";
}

function hideTranscript() {
    if (transcriptEl) {
        transcriptEl.remove();
        transcriptEl = null;
    }
}

function startListening() {
    if (!SpeechRecognition || isListening) return;

    recognition = new SpeechRecognition();
    recognition.lang = "en-US";
    recognition.interimResults = true;
    recognition.continuous = false;
    recognition.maxAlternatives = 1;

    let finalTranscript = "";

    recognition.onstart = () => {
        isListening = true;
        voiceBtn.classList.add("listening");
        showTranscript();
    };

    recognition.onresult = (event) => {
        let interim = "";
        finalTranscript = "";
        for (let i = 0; i < event.results.length; i++) {
            const t = event.results[i][0].transcript;
            if (event.results[i].isFinal) {
                finalTranscript += t;
            } else {
                interim += t;
            }
        }
        // Show live transcript
        const display = finalTranscript || interim;
        showTranscript(display || "Listening…");
        // Put interim text in the input field so user can see / edit
        input.value = finalTranscript || interim;
        autoResize();
        sendBtn.disabled = !input.value.trim();
    };

    recognition.onend = () => {
        isListening = false;
        voiceBtn.classList.remove("listening");
        hideTranscript();

        // Auto-send if we got a final transcript
        if (finalTranscript.trim()) {
            sendMessage(finalTranscript.trim());
        }
    };

    recognition.onerror = (event) => {
        console.warn("Speech recognition error:", event.error);
        isListening = false;
        voiceBtn.classList.remove("listening");
        hideTranscript();

        if (event.error === "not-allowed") {
            alert("Microphone access denied. Please allow microphone permission in your browser settings.");
        }
    };

    recognition.start();
}

function stopListening() {
    if (recognition && isListening) {
        recognition.stop();
    }
}

voiceBtn.addEventListener("click", () => {
    if (isListening) {
        stopListening();
    } else {
        // Stop any TTS that's playing
        window.speechSynthesis.cancel();
        startListening();
    }
});

// ── Patch addMessage to auto-speak agent replies ─────────────────
const _originalAddMessage = addMessage;
addMessage = function (role, content, steps) {
    _originalAddMessage(role, content, steps);
    if (role === "agent" && ttsEnabled) {
        speakText(content);
    }
};

// ═══════════════════════════════════════════════════════════════════
//  Boot
// ═══════════════════════════════════════════════════════════════════

(async () => {
    // Check if this is an OAuth callback redirect (token in query string)
    const url = new URL(window.location.href);
    if (url.searchParams.has("access_token")) {
        await handleOAuthCallback();
    } else {
        await checkAuth();
    }
})();