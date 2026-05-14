/* DTN Chat - Pure DTN messaging client */

const ME = window.CURRENT_USER;
let currentRoom = "lobby";
let lastMsgId = 0;
let users = [];
let evtSource = null;

// --- SSE ---

function connectSSE() {
    if (evtSource) evtSource.close();
    evtSource = new EventSource("/api/stream");

    evtSource.addEventListener("connected", () => {
        updateStatus(true);
    });

    evtSource.onmessage = (e) => {
        try {
            const msg = JSON.parse(e.data);
            if (msg.room === currentRoom) {
                appendMessage(msg);
            }
            if (msg.room !== currentRoom && msg.room.startsWith("dm:") && msg.room.includes(ME.uid)) {
                markDmUnread(msg.room);
            }
        } catch {}
    };

    evtSource.addEventListener("status", (e) => {
        try {
            const data = JSON.parse(e.data);
            updateMessageStatus(data.id, data.status);
        } catch {}
    });

    evtSource.addEventListener("users", () => {
        loadUsers();
    });

    evtSource.onerror = () => {
        updateStatus(false);
        setTimeout(() => {
            if (evtSource.readyState === EventSource.CLOSED) connectSSE();
        }, 5000);
    };
}

// --- Rooms ---

function switchRoom(room, label) {
    currentRoom = room;
    lastMsgId = 0;
    document.getElementById("messages").innerHTML = "";

    const header = document.getElementById("roomHeader");
    if (room === "lobby") {
        header.innerHTML = '<span class="room-icon">#</span><span class="room-title">Lobby</span>';
    } else {
        header.innerHTML = `<span class="room-icon">@</span><span class="room-title">${esc(label || room)}</span>`;
    }

    document.querySelectorAll(".room-item, .dm-item").forEach(el => el.classList.remove("active"));
    const activeEl = document.querySelector(`[data-room="${CSS.escape(room)}"]`);
    if (activeEl) {
        activeEl.classList.add("active");
        activeEl.classList.remove("unread");
    }

    loadMessages();
    document.getElementById("messageInput").focus();
}

function startDM(uid, displayName) {
    if (uid === ME.uid) return;
    const room = dmRoomId(ME.uid, uid);
    addDmTab(room, displayName);
    switchRoom(room, displayName);
}

function dmRoomId(uid1, uid2) {
    const pair = [uid1, uid2].sort();
    return `dm:${pair[0]}-${pair[1]}`;
}

function addDmTab(room, label) {
    const list = document.getElementById("dmList");
    if (list.querySelector(`[data-room="${CSS.escape(room)}"]`)) return;
    const div = document.createElement("div");
    div.className = "dm-item";
    div.dataset.room = room;
    div.onclick = () => switchRoom(room, label);
    div.innerHTML = `<span class="room-icon">@</span><span class="room-name">${esc(label)}</span>`;
    list.appendChild(div);
}

function markDmUnread(room) {
    const el = document.querySelector(`[data-room="${CSS.escape(room)}"]`);
    if (el) el.classList.add("unread");
    else {
        const parts = room.replace("dm:", "").split("-");
        let otherUid = parts.filter(p => !ME.uid.startsWith(p)).join("-");
        if (!otherUid) otherUid = parts[1];
        const other = users.find(u => u.uid.includes(otherUid));
        addDmTab(room, other ? other.display_name : otherUid);
        const newEl = document.querySelector(`[data-room="${CSS.escape(room)}"]`);
        if (newEl) newEl.classList.add("unread");
    }
}

// --- Messages ---

async function loadMessages() {
    try {
        const res = await fetch(`/api/messages/${encodeURIComponent(currentRoom)}?after_id=${lastMsgId}`);
        const msgs = await res.json();
        if (msgs.length) {
            const container = document.getElementById("messages");
            const emptyState = container.querySelector(".empty-state");
            if (emptyState) emptyState.remove();

            const wasAtBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 60;
            msgs.forEach(m => {
                if (m.id > lastMsgId) lastMsgId = m.id;
                appendMessageEl(container, m);
            });
            if (wasAtBottom) container.scrollTop = container.scrollHeight;
        }
    } catch {}
}

function appendMessage(msg) {
    if (msg.id <= lastMsgId) return;
    lastMsgId = msg.id;
    const container = document.getElementById("messages");
    const emptyState = container.querySelector(".empty-state");
    if (emptyState) emptyState.remove();

    const wasAtBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 60;
    appendMessageEl(container, msg);
    if (wasAtBottom) container.scrollTop = container.scrollHeight;
}

function appendMessageEl(container, m) {
    const div = document.createElement("div");
    const isMine = m.uid === ME.uid;
    div.className = `msg ${isMine ? "sent" : "received"}`;
    div.dataset.msgId = m.id;
    const time = formatTime(m.timestamp);
    const sender = isMine ? "You" : m.display_name;
    const statusHtml = isMine ? `<div class="msg-status ${m.status || 'sent'}" data-status-id="${m.id}">${statusLabel(m.status)}</div>` : '';
    div.innerHTML = `<div class="meta">${esc(sender)} &middot; ${time}</div><div class="text">${esc(m.message)}</div>${statusHtml}`;
    container.appendChild(div);
}

function statusLabel(status) {
    if (!status || status === "sent") return "sent via DTN";
    if (status === "delivered") return "delivered";
    if (status === "failed") return "send failed";
    if (status === "local") return "local (no remote nodes)";
    if (status.startsWith("ack:")) return status.replace("ack:", "") + " delivered";
    return status;
}

function updateMessageStatus(msgId, status) {
    const el = document.querySelector(`[data-status-id="${msgId}"]`);
    if (el) {
        el.textContent = statusLabel(status);
        el.className = `msg-status ${status.startsWith("ack:") ? "delivered" : status}`;
    }
}

// --- Users ---

async function loadUsers() {
    try {
        const res = await fetch("/api/users");
        users = await res.json();
        renderUsers();
    } catch {}
}

function renderUsers() {
    const list = document.getElementById("userList");
    if (!users.length) {
        list.innerHTML = '<div class="loading">No users yet</div>';
        return;
    }
    list.innerHTML = users.map(u => `
        <div class="user-item ${u.uid === ME.uid ? 'is-me' : ''}" onclick="startDM('${esc(u.uid)}', '${esc(u.display_name)}')">
            <div class="user-item-name">
                ${esc(u.display_name)}
                ${u.uid === ME.uid ? '<span class="you-tag">you</span>' : ''}
            </div>
            <div class="user-item-detail">
                <span class="paired-badge">ipn:${u.ipn_number}</span>
            </div>
        </div>
    `).join("");
}

// --- Send ---

async function sendMessage(e) {
    e.preventDefault();
    const input = document.getElementById("messageInput");
    const msg = input.value.trim();
    if (!msg) return;

    const btn = e.target.querySelector("button[type=submit]");
    btn.disabled = true;

    const body = { message: msg, room: currentRoom };
    if (currentRoom.startsWith("dm:")) {
        const myIdx = currentRoom.indexOf(ME.uid);
        if (myIdx >= 0) {
            const roomContent = currentRoom.replace("dm:", "");
            body.to_uid = roomContent.replace(ME.uid, "").replace(/^-|-$/g, "");
        }
    }

    try {
        const res = await fetch("/api/send", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (data.ok) {
            input.value = "";
        } else if (data.error) {
            alert(data.error);
        }
    } finally {
        btn.disabled = false;
        input.focus();
    }
}

// --- Status ---

async function checkStatus() {
    try {
        const s = await fetch("/api/status").then(r => r.json());
        updateStatus(s.ion_running);
    } catch {
        updateStatus(false);
    }
}

function updateStatus(online) {
    const dot = document.getElementById("statusDot");
    dot.className = "status-dot " + (online ? "online" : "offline");
    dot.title = online ? "ION running" : "ION not running";
}

// --- Utilities ---

function formatTime(ts) {
    try {
        return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch { return ts; }
}

function esc(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
}

// --- Logout ---

document.getElementById("logoutBtn").addEventListener("click", async () => {
    await fetch("/api/logout", { method: "POST" });
    window.location.href = "/join";
});

// --- Events ---

document.getElementById("composeForm").addEventListener("submit", sendMessage);
document.getElementById("refreshNodes").addEventListener("click", async () => {
    await fetch("/api/nodes/refresh", { method: "POST" });
    loadUsers();
});

// --- Init ---

connectSSE();
loadMessages();
loadUsers();
checkStatus();
setInterval(checkStatus, 30000);
