# DTN Chat — Architecture & Message Flow

## Overview

Multi-user web chat over Delay-Tolerant Networking (ION-DTN). Messages between nodes travel exclusively as DTN bundles via the Bundle Protocol. The web UI is for local display and input only — remote paired nodes cannot send via HTTP.

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

### Flow 1: Local User Sends to Lobby

User on Pi05 itself (127.0.0.1 or Pi05's own IPs).

```
Browser → POST /api/send (HTTP)
    │
    ├─► SQLite: INSERT into messages
    ├─► SSE: push to all connected browsers
    │
    └─► For each remote paired user:
            bpsource ipn:<remote>.7 '{"s":268485091,...}'
                │
                ▼
            ION routes via UDP outduct
                │
                ▼
            Remote node's udpcli (port 4556)
                │
                ▼
            bprecvfile writes testfileN
                │
                ▼
            Remote BundleReceiver → DB + SSE
```

### Flow 2: Web-Only User Sends

User with no DTN node (phone, laptop without ION).

```
Browser → POST /api/send (HTTP)
    │
    ├─► SQLite: INSERT into messages
    ├─► SSE: push to all connected browsers
    │
    └─► bpsource to each remote paired node
        (same as Flow 1)
```

### Flow 3: Remote DTN Node Sends

A paired user on a remote node (e.g., echo). **Web UI sending is blocked** — must use DTN.

```
Remote node runs:
    bpsource ipn:268485091.7 '{"s":268485111,"n":"echo-user",...}'
        │
        ▼
    ION routes bundle via UDP
    (ZeroTier 10.16.16.169:4556 or Tailscale 100.75.250.100:4556)
        │
        ▼
    Pi05: udpcli receives on 0.0.0.0:4556
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
        ├─► Creates user in DB if unknown
        ├─► INSERT into messages table
        ├─► SSE push to all connected browsers
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

```json
{
    "s": 268485091,
    "n": "display-name",
    "t": "2026-05-13T12:00:00.000000Z",
    "m": "message text",
    "room": "lobby",
    "uid": "display-name-268485091",
    "to_uid": "target-uid"
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
| `to_uid` | DM recipient (optional) |

## Data Storage

```
SQLite: chat.db
├── messages (id, uid, display_name, room, message, timestamp, created_at)
├── users (uid, display_name, ipn_number, user_type, last_seen, created_at)
└── nodes (node_number, node_name, description, source, updated_at)
```

- Messages auto-deleted after 24 hours (cleanup thread, hourly)
- Max 1000 messages retained
- Cookie-based sessions (`dtn_uid`, 30-day expiry)
- No passwords — display name only

## User Types

| Type | Detection | Can Send via Web | Messages via |
|------|-----------|-----------------|-------------|
| `paired` (local) | IP matches this node | Yes | DB + bpsource to remotes |
| `paired` (remote) | IP matches a remote IPN plan | **No** (403) | DTN bundles only |
| `nickname` (web) | IP not in any IPN plan | Yes | DB + bpsource to remotes |

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
