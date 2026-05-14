# DTN Chat — Architecture & Message Flow

## Overview

Pure DTN chat — **all communication via ION bundles, no fallback**. The web UI requires an active ION-DTN node. Non-DTN visitors are blocked. Message delivery is confirmed via DTN acknowledgment bundles.

## Network Topology

```
                    ┌─────────────────────┐
                    │   OpenIPN Gateway    │
                    │  ipn:268485000       │
                    │  100.96.108.37       │
                    └────────┬────────────┘
                             │ Tailscale (samo.grasic@)
              ┌──────────────┼──────────────┐
              │              │              │
     ┌────────┴───────┐     │     ┌────────┴───────┐
     │  Pi05           │     │     │  Other nodes   │
     │  ipn:268485091  │     │     │  268485095-101 │
     │  TS: 100.75.250.100   │     │  Tailscale IPs │
     │  ZT: 10.16.16.169│    │     └────────────────┘
     └────────┬────────┘     │
              │ ZeroTier (10.16.16.x)
              │ (Tailscale unreachable — different networks)
     ┌────────┴────────┐
     │  Echo-Dhulikhel  │
     │  ipn:268485111   │
     │  TS: 100.67.241.111 (anamolsapkota@)
     │  ZT: 10.16.16.17 │
     └─────────────────┘
```

**Routing priority:** Tailscale always, ZeroTier fallback for cross-network nodes (e.g., echo).

## ION Endpoints

| Endpoint | Purpose |
|----------|---------|
| `ipn:<node>.7` | Chat receive — bprecvfile listens here |
| `ipn:<node>.1` | ION admin |
| `ipn:<node>.12160-12161` | dtnex metadata exchange |

## Components

```
┌──────────────────────────────────────────────────────┐
│                    Pi05 (or any node)                 │
│                                                      │
│  ┌──────────┐  ┌───────────┐  ┌───────────────────┐ │
│  │ Flask App │  │ SQLite DB │  │ ION-DTN Stack     │ │
│  │ (app.py)  │──│ (chat.db) │  │                   │ │
│  │           │  │           │  │ bprecvfile (.7)   │ │
│  │ /api/send │  │ messages  │  │ bpsource (send)   │ │
│  │ /api/stream│ │ users     │  │ udpcli (recv UDP) │ │
│  │ SSE push  │  │ nodes     │  │ udpclo (send UDP) │ │
│  └──────────┘  └───────────┘  └───────────────────┘ │
│       │                              │               │
│       └──── BundleReceiver ──────────┘               │
│             (polls /tmp/dtn-chat-recv/)               │
└──────────────────────────────────────────────────────┘
```

## Message Flows

### Flow 1: Sending a Message

Any paired DTN user sends from the web UI on this node.

```
Browser → POST /api/send (HTTP to local Flask)
    │
    ├─► SQLite: INSERT message (status='sent', bundle_id=<uuid>)
    ├─► SSE: push to local browsers (shows "sent via DTN")
    │
    └─► For each remote paired node:
            bpsource ipn:<remote>.7 '{"s":268485091,"bid":"<uuid>",...}'
                │
                ▼
            ION routes via UDP outduct
                │
                ▼
            Remote node's udpcli (port 4556)
                │
                ▼
            Remote bprecvfile writes testfileN
                │
                ▼
            Remote BundleReceiver:
                ├─► Dedup check (bundle_id)
                ├─► Store in DB (status='delivered')
                ├─► SSE push to remote browsers
                └─► Send ACK bundle back:
                        bpsource ipn:<sender>.7 '{"type":"ack","bid":"<uuid>",...}'
```

### Flow 2: Receiving an ACK

When the ACK bundle arrives back at the sender node:

```
ACK bundle arrives at sender's bprecvfile
    │
    ▼
BundleReceiver detects type="ack"
    │
    ├─► Record in acks table (bundle_id, from_node)
    ├─► Update messages.ack_count
    ├─► If ack_count >= dest_count → status = 'delivered'
    │   Else → status = 'ack:N/M'
    └─► SSE push status update to sender's browser
        (UI updates from "sent via DTN" → "1/3 delivered" → "delivered")
```

### Flow 3: Incoming Bundle from Remote Node

A remote node sends a chat message via DTN:

```
Remote node: bpsource ipn:268485091.7 '{"s":268485111,...}'
    │
    ▼
ION routes via UDP (Tailscale or ZeroTier)
    │
    ▼
Local udpcli (0.0.0.0:4556) receives
    │
    ▼
ION delivers to endpoint ipn:268485091.7
    │
    ▼
bprecvfile writes /tmp/dtn-chat-recv/testfileN
    │
    ▼
BundleReceiver thread (polls every 0.5s):
    ├─► Reads file, parses JSON payload
    ├─► Dedup check by bundle_id (skip if already stored)
    ├─► Creates sender user in DB if unknown
    ├─► INSERT into messages (status='delivered')
    ├─► SSE push to all connected browsers
    ├─► Send ACK bundle back to sender via DTN
    └─► Deletes the file
```

### Flow 4: Viewing Messages

```
Browser loads page:
    GET /api/messages/lobby?after_id=0  →  SQLite query  →  JSON array

Browser connects SSE:
    GET /api/stream  →  EventSource (persistent connection)
        │
        Receives:
        ├─► "data: {message JSON}\n\n"     →  append to chat
        ├─► "event: users\ndata: updated"  →  refresh user list
        └─► ": keepalive\n\n"              →  every 30s
```

### Flow 5: Direct Messages (DMs)

```
Sender clicks user → switchRoom("dm:<uid1>-<uid2>")
    │
    POST /api/send { to_uid: "target-uid", room: "dm:..." }
        │
        ├─► Store in SQLite (room = "dm:<sorted-uids>")
        ├─► SSE push (all clients receive; JS filters by room)
        │
        └─► bpsource to recipient's IPN node only
            (not broadcast to all paired users)
```

## Bundle Payload Format

**Chat message:**
```json
{
    "s": 268485091,
    "n": "display-name",
    "t": "2026-05-13T12:00:00.000000Z",
    "m": "message text",
    "room": "lobby",
    "uid": "display-name-268485091",
    "bid": "a1b2c3d4e5f6",
    "to_uid": "target-uid"
}
```

**ACK bundle:**
```json
{
    "type": "ack",
    "bid": "a1b2c3d4e5f6",
    "from": 268485111
}
```

| Field | Description |
|-------|-------------|
| `s` | Sender's IPN node number |
| `n` | Display name |
| `t` | UTC timestamp (ISO 8601) |
| `m` | Message text (max 500 chars) |
| `room` | `lobby` or `dm:<uid1>-<uid2>` |
| `uid` | Sender's user ID |
| `bid` | Bundle ID for dedup + ACK tracking |
| `to_uid` | DM recipient (optional) |
| `type` | `"ack"` for acknowledgment bundles |
| `from` | ACK sender's IPN node number |

## Message Status Lifecycle

```
sent → ack:1/3 → ack:2/3 → delivered
  │                              │
  └──► failed (if bpsource fails for all destinations)
```

| Status | Meaning |
|--------|---------|
| `sent` | Bundle handed to ION for delivery |
| `ack:N/M` | N of M destination nodes confirmed receipt |
| `delivered` | All destination nodes confirmed receipt |
| `failed` | bpsource failed for all destinations |
| `local` | No remote nodes to send to |

## Data Storage

```
SQLite: chat.db
├── messages (id, uid, display_name, room, message, timestamp,
│             bundle_id, status, dest_count, ack_count, created_at)
├── users (uid, display_name, ipn_number, user_type, last_seen, created_at)
├── acks (id, bundle_id, from_node, received_at)
└── nodes (node_number, node_name, description, source, updated_at)
```

- Messages auto-deleted after 24 hours (cleanup thread, hourly)
- Max 1000 messages retained
- Cookie-based sessions (`dtn_uid`, 30-day expiry)
- No passwords — display name only
- All users must be DTN-paired (no web-only users)

## User Types

| Type | Detection | Access |
|------|-----------|--------|
| `paired` | IP matches a known IPN node | Full chat via DTN |
| Non-DTN visitor | IP not in any IPN plan | **Blocked** — cannot join |

There are no web-only users. All participants must have an active ION-DTN node.

## Auto-Detection (IP → IPN)

```
peer_discovery.get_ip_to_ipn_map():
    1. Parse `ipnadmin l plan` output
       "268485100 xmit 100.64.115.74:4556" → {100.64.115.74: 268485100}
    2. Add local IPs → LOCAL_NODE_NUMBER
       127.0.0.1, all IPs from `hostname -I`
    3. Return map

On /join or /api/detect:
    client_ip = request.remote_addr
    ipn = ip_map.get(client_ip)
    → "DTN Node Detected" or "Web Visitor"
```

## ION Configuration

Pi05's ION rc file defines:
- **Induct:** `a induct udp 0.0.0.0:4556 udpcli` (receive bundles)
- **Outducts:** One per known node IP (e.g., `a outduct udp 100.64.115.74:4556 udpclo`)
- **Plans:** Map IPN numbers to outducts (e.g., `a plan 268485100 udp/100.64.115.74:4556`)
- **Contacts:** Bidirectional xmit rates between node pairs

## Key Constraints

| Constraint | Detail |
|-----------|--------|
| No ION loopback | ION cannot deliver bundles to the same node — local messages stored directly in DB |
| bprecvfile not bpchat | bpchat only receives from other bpchat instances; bprecvfile receives any bundle source |
| Tailscale primary | All nodes on same Tailscale network use Tailscale IPs for ION plans |
| ZeroTier fallback | Cross-network nodes (e.g., echo on different Tailscale) use ZeroTier IPs |
| UDP port 4556 | `udpcli` must be running to receive bundles — check with `ss -uln \| grep 4556` |
| Bundle TTL | Default 300s for bpsource; dtnex uses 1800s |
