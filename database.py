"""SQLite database for DTN Chat - users, messages, cleanup."""

import sqlite3
import threading
import time
import config

_lock = threading.Lock()
_db_path = None


def init_db():
    global _db_path
    _db_path = config.DATABASE_PATH
    conn = sqlite3.connect(_db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            uid TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            ipn_number INTEGER,
            user_type TEXT NOT NULL,
            last_seen TEXT DEFAULT (datetime('now')),
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL,
            display_name TEXT NOT NULL,
            room TEXT NOT NULL,
            message TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(room, id);
        CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);

        -- Keep old nodes table for peer discovery
        CREATE TABLE IF NOT EXISTS nodes (
            node_number INTEGER PRIMARY KEY,
            node_name TEXT,
            description TEXT,
            last_seen TEXT,
            source TEXT
        );
    """)
    conn.close()


def _get_conn():
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    return conn


# --- User operations ---

def upsert_user(uid, display_name, ipn_number, user_type):
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO users (uid, display_name, ipn_number, user_type) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET "
            "display_name=excluded.display_name, "
            "ipn_number=excluded.ipn_number, "
            "last_seen=datetime('now')",
            (uid, display_name, ipn_number, user_type),
        )
        conn.commit()
        conn.close()


def get_user(uid):
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE uid = ?", (uid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def touch_user(uid):
    with _lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE users SET last_seen = datetime('now') WHERE uid = ?", (uid,)
        )
        conn.commit()
        conn.close()


def get_all_users():
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM users ORDER BY last_seen DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Message operations ---

def insert_message(uid, display_name, room, message, timestamp):
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "INSERT INTO messages (uid, display_name, room, message, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (uid, display_name, room, message, timestamp),
        )
        msg_id = cur.lastrowid
        conn.commit()
        conn.close()
        return msg_id


def get_messages(room, limit=200, after_id=0):
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM messages WHERE room = ? AND id > ? ORDER BY id ASC LIMIT ?",
        (room, after_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_node_by_number(node_number):
    conn = _get_conn()
    row = conn.execute("SELECT * FROM nodes WHERE node_number = ?", (node_number,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_dm_room_id(uid1, uid2):
    """Generate consistent DM room ID from two UIDs."""
    pair = sorted([uid1, uid2])
    return f"dm:{pair[0]}-{pair[1]}"


def get_user_dm_rooms(uid):
    """Get all DM rooms a user participates in."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT DISTINCT room FROM messages WHERE room LIKE 'dm:%' "
        "AND (room LIKE ? OR room LIKE ?)",
        (f"dm:{uid}-%", f"dm:%-{uid}"),
    ).fetchall()
    conn.close()
    return [row["room"] for row in rows]


# --- Node operations (kept for peer discovery) ---

def upsert_node(node_number, node_name, description="", source="metadata"):
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO nodes (node_number, node_name, description, last_seen, source) "
            "VALUES (?, ?, ?, datetime('now'), ?) "
            "ON CONFLICT(node_number) DO UPDATE SET "
            "node_name=COALESCE(excluded.node_name, node_name), "
            "description=COALESCE(excluded.description, description), "
            "last_seen=datetime('now'), source=excluded.source",
            (node_number, node_name, description, source),
        )
        conn.commit()
        conn.close()


def get_nodes():
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM nodes ORDER BY node_name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Cleanup ---

def cleanup_old_messages():
    """Delete messages older than 24 hours and enforce 1000 message cap."""
    with _lock:
        conn = _get_conn()
        conn.execute(
            "DELETE FROM messages WHERE created_at < datetime('now', '-24 hours')"
        )
        # Safety cap
        count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        if count > 1000:
            conn.execute(
                "DELETE FROM messages WHERE id IN "
                "(SELECT id FROM messages ORDER BY id ASC LIMIT ?)",
                (count - 1000,),
            )
        conn.commit()
        conn.close()


def start_cleanup_thread():
    """Run cleanup every hour in background."""
    def _loop():
        while True:
            time.sleep(3600)
            try:
                cleanup_old_messages()
            except Exception as e:
                print(f"[cleanup] error: {e}")
    t = threading.Thread(target=_loop, daemon=True)
    t.start()
