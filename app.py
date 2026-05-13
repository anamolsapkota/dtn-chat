#!/usr/bin/env python3
"""DTN Chat - Multi-user web chat over Delay-Tolerant Networking."""

import json
import queue
import subprocess
import datetime
import threading

from flask import Flask, render_template, jsonify, request, redirect, make_response

import config
import database
import dtn_transport
import peer_discovery

app = Flask(__name__)
app.secret_key = "dtn-chat-secret-key"
receiver = None

# SSE subscribers: dict of uid -> list of queue.Queue
_sse_clients = {}
_sse_lock = threading.Lock()


def sse_publish(room, event_data):
    """Push an event to all SSE clients subscribed to a room."""
    with _sse_lock:
        for uid, clients in list(_sse_clients.items()):
            for q in clients:
                try:
                    q.put_nowait(event_data)
                except queue.Full:
                    pass


def sse_publish_to_room(room, msg_dict):
    """Publish a message event. All clients receive it; JS filters by room."""
    event_data = f"data: {json.dumps(msg_dict)}\n\n"
    sse_publish(room, event_data)


def sse_publish_user_update():
    """Notify all clients that the user list changed."""
    event_data = f"event: users\ndata: updated\n\n"
    with _sse_lock:
        for uid, clients in list(_sse_clients.items()):
            for q in clients:
                try:
                    q.put_nowait(event_data)
                except queue.Full:
                    pass


def get_current_user():
    """Get user info from cookie."""
    uid = request.cookies.get("dtn_uid")
    if not uid:
        return None
    user = database.get_user(uid)
    if user:
        database.touch_user(uid)
    return user


# --- Routes ---

@app.route("/")
def index():
    user = get_current_user()
    if not user:
        return redirect("/join")
    return render_template(
        "index.html",
        user=user,
        node_number=config.LOCAL_NODE_NUMBER,
        node_name=config.LOCAL_NODE_NAME,
        service_number=config.CHAT_SERVICE_NUMBER,
    )


@app.route("/join")
def join_page():
    # Auto-detect IPN from visitor's IP
    client_ip = request.remote_addr
    detected_ipn, detected_name = peer_discovery.resolve_ipn_from_ip(client_ip)
    return render_template("join.html",
        node_number=config.LOCAL_NODE_NUMBER,
        node_name=config.LOCAL_NODE_NAME,
        detected_ipn=detected_ipn,
        detected_name=detected_name,
        client_ip=client_ip,
    )


@app.route("/api/detect")
def api_detect():
    """Auto-detect the visitor's IPN node from their IP."""
    client_ip = request.remote_addr
    ipn, name = peer_discovery.resolve_ipn_from_ip(client_ip)
    return jsonify({
        "client_ip": client_ip,
        "ipn_number": ipn,
        "node_name": name,
        "is_dtn_node": ipn is not None,
    })


@app.route("/api/join", methods=["POST"])
def api_join():
    data = request.get_json()
    if not data:
        return jsonify({"error": "no data"}), 400

    display_name = data.get("display_name", "").strip()
    if not display_name:
        return jsonify({"error": "display_name required"}), 400

    # Auto-detect IPN from visitor's IP
    client_ip = request.remote_addr
    detected_ipn, _ = peer_discovery.resolve_ipn_from_ip(client_ip)

    if detected_ipn:
        # Visitor is from a known DTN node — auto-pair
        user_type = "paired"
        ipn_number = detected_ipn
        uid = f"{display_name.lower()}-{ipn_number}"
    else:
        # Unknown IP — nickname user (web-only)
        user_type = "nickname"
        ipn_number = None
        uid = f"{display_name.lower()}-web"

    database.upsert_user(uid, display_name, ipn_number, user_type)
    sse_publish_user_update()

    resp = make_response(jsonify({
        "ok": True,
        "uid": uid,
        "user_type": user_type,
        "ipn_number": ipn_number,
    }))
    resp.set_cookie("dtn_uid", uid, max_age=86400 * 30, samesite="Lax")
    return resp


@app.route("/api/me")
def api_me():
    user = get_current_user()
    if not user:
        return jsonify({"error": "not logged in"}), 401
    return jsonify(user)


@app.route("/api/users")
def api_users():
    users = database.get_all_users()
    return jsonify(users)


@app.route("/api/messages/<room>")
def api_messages(room):
    after_id = request.args.get("after_id", 0, type=int)
    # Validate room access for DMs
    user = get_current_user()
    if not user:
        return jsonify({"error": "not logged in"}), 401
    if room.startswith("dm:") and user["uid"] not in room:
        return jsonify({"error": "access denied"}), 403
    messages = database.get_messages(room, limit=200, after_id=after_id)
    return jsonify(messages)


@app.route("/api/send", methods=["POST"])
def api_send():
    """Send a chat message. Store locally in DB + SSE, send DTN bundles to remote nodes.
    ION does not deliver bundles to the same node (loopback), so local storage is direct."""
    user = get_current_user()
    if not user:
        return jsonify({"error": "not logged in"}), 401

    data = request.get_json()
    if not data:
        return jsonify({"error": "no data"}), 400

    message = data.get("message", "").strip()
    room = data.get("room", "lobby")
    to_uid = data.get("to_uid")

    if not message:
        return jsonify({"error": "message required"}), 400
    if len(message) > 500:
        return jsonify({"error": "message too long (max 500 chars)"}), 400

    # For DMs, compute the room ID
    if to_uid:
        room = database.get_dm_room_id(user["uid"], to_uid)

    timestamp = datetime.datetime.utcnow().isoformat() + "Z"

    # Store locally (ION cannot loopback bundles to the same node)
    msg_id = database.insert_message(
        uid=user["uid"],
        display_name=user["display_name"],
        room=room,
        message=message,
        timestamp=timestamp,
    )

    # Push to all connected SSE clients
    full_msg = {
        "id": msg_id,
        "uid": user["uid"],
        "display_name": user["display_name"],
        "room": room,
        "message": message,
        "timestamp": timestamp,
    }
    sse_publish_to_room(room, full_msg)

    # Build bundle payload for remote DTN delivery
    payload = {
        "s": config.LOCAL_NODE_NUMBER,
        "n": user["display_name"],
        "t": timestamp,
        "m": message,
        "room": room,
        "uid": user["uid"],
    }
    if to_uid:
        payload["to_uid"] = to_uid

    # Send DTN bundles to remote paired IPN nodes
    if room == "lobby":
        _send_to_paired_users(user, payload)
    elif to_uid:
        _send_dm_bundle(user, to_uid, payload)

    return jsonify({"ok": True})


def _send_to_paired_users(sender, payload):
    """Send lobby message as DTN bundle to all paired IPN users."""
    users = database.get_all_users()
    for u in users:
        if u["user_type"] == "paired" and u["uid"] != sender["uid"] and u["ipn_number"]:
            dtn_transport.send_bundle_to_remote(dest_node=u["ipn_number"], payload=payload)


def _send_dm_bundle(sender, to_uid, payload):
    """Send DM as DTN bundle to recipient if they have a paired IPN node."""
    recipient = database.get_user(to_uid)
    if recipient and recipient["user_type"] == "paired" and recipient["ipn_number"]:
        dtn_transport.send_bundle_to_remote(dest_node=recipient["ipn_number"], payload=payload)


@app.route("/api/nodes")
def api_nodes():
    nodes = database.get_nodes()
    return jsonify(nodes)


@app.route("/api/nodes/refresh", methods=["POST"])
def api_refresh_nodes():
    peer_discovery.refresh_all()
    return jsonify({"ok": True})


@app.route("/api/status")
def api_status():
    ion_running = False
    try:
        proc = subprocess.run(
            ["bpadmin"], input="l\nq\n", capture_output=True, text=True, timeout=5
        )
        ion_running = "List what?" in proc.stdout or "Stopping" in proc.stdout
    except Exception:
        pass
    if not ion_running:
        try:
            proc = subprocess.run(["pgrep", "-x", "bpclock"], capture_output=True, timeout=3)
            ion_running = proc.returncode == 0
        except Exception:
            pass
    return jsonify({
        "node_number": config.LOCAL_NODE_NUMBER,
        "node_name": config.LOCAL_NODE_NAME,
        "chat_eid": f"ipn:{config.LOCAL_NODE_NUMBER}.{config.CHAT_SERVICE_NUMBER}",
        "ion_running": ion_running,
    })


@app.route("/api/stream")
def api_stream():
    """SSE endpoint - clients connect here for real-time updates."""
    user = get_current_user()
    if not user:
        return jsonify({"error": "not logged in"}), 401

    uid = user["uid"]
    q = queue.Queue(maxsize=100)

    with _sse_lock:
        if uid not in _sse_clients:
            _sse_clients[uid] = []
        _sse_clients[uid].append(q)

    def generate():
        try:
            yield "event: connected\ndata: ok\n\n"
            while True:
                try:
                    data = q.get(timeout=30)
                    yield data
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            with _sse_lock:
                if uid in _sse_clients:
                    try:
                        _sse_clients[uid].remove(q)
                    except ValueError:
                        pass
                    if not _sse_clients[uid]:
                        del _sse_clients[uid]

    resp = make_response(generate())
    resp.headers["Content-Type"] = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@app.route("/api/logout", methods=["POST"])
def api_logout():
    resp = make_response(jsonify({"ok": True}))
    resp.delete_cookie("dtn_uid")
    return resp


def ensure_endpoint():
    """Ensure our chat endpoint (.7) is registered in ION."""
    eid = f"ipn:{config.LOCAL_NODE_NUMBER}.{config.CHAT_SERVICE_NUMBER}"
    try:
        subprocess.run(
            ["bpadmin"], input=f"a endpoint {eid} q\nq\n",
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        pass


def main():
    config.detect_node()
    print(f"[dtnchat] Node: ipn:{config.LOCAL_NODE_NUMBER} ({config.LOCAL_NODE_NAME})")
    print(f"[dtnchat] Chat endpoint: ipn:{config.LOCAL_NODE_NUMBER}.{config.CHAT_SERVICE_NUMBER}")

    database.init_db()
    database.start_cleanup_thread()
    ensure_endpoint()
    peer_discovery.refresh_all()

    global receiver
    receiver = dtn_transport.BundleReceiver(on_message=handle_incoming_bundle)
    receiver.start()
    print("[dtnchat] Bundle receiver (.7) started via bprecvfile")

    dtn_transport.init_sender()

    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT, debug=False, threaded=True)


def handle_incoming_bundle(msg_dict):
    """Called by BundleReceiver when a chat bundle arrives."""
    uid = msg_dict.get("uid", f"unknown-{msg_dict.get('s', 0)}")
    display_name = msg_dict.get("n", "Unknown")
    room = msg_dict.get("room", "lobby")
    to_uid = msg_dict.get("to_uid")
    message = msg_dict.get("m", "")
    timestamp = msg_dict.get("t", datetime.datetime.utcnow().isoformat() + "Z")

    if not message:
        return

    # For DMs, compute proper room ID
    if room == "dm" and to_uid:
        room = database.get_dm_room_id(uid, to_uid)

    # Ensure user exists
    sender_node = msg_dict.get("s")
    if sender_node:
        user_type = "paired"
        if not database.get_user(uid):
            database.upsert_user(uid, display_name, sender_node, user_type)
        database.upsert_node(sender_node, display_name, source="chat")

    msg_id = database.insert_message(
        uid=uid,
        display_name=display_name,
        room=room,
        message=message,
        timestamp=timestamp,
    )

    full_msg = {
        "id": msg_id,
        "uid": uid,
        "display_name": display_name,
        "room": room,
        "message": message,
        "timestamp": timestamp,
    }
    sse_publish_to_room(room, full_msg)


if __name__ == "__main__":
    main()
