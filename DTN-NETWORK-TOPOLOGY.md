# DTN Network Topology: Pi05, Echo, and Gateway

## Overview

This document describes how the DTN (Delay-Tolerant Networking) nodes communicate
using ION-DTN and the dtnex metadata exchange protocol.

## Nodes

| Node | IPN | Location | Network | Role |
|------|-----|----------|---------|------|
| **Pi05** | ipn:268485091 | Ek Ra Sunya Inc, Kathmandu | Samo's Tailscale + ZeroTier | Hub / Relay |
| **Echo** | ipn:268485111 | Kathmandu University, Dhulikhel | Anamol's Tailscale + ZeroTier | Edge node |
| **Gateway (DTNGW)** | ipn:268485000 | Sweden | Samo's Tailscale | DTN Gateway |
| **OpenIPNNode** | ipn:268484800 | Sweden | Samo's Tailscale | Monitoring / bpecho |

## Network Topology

```
                    Samo's Tailscale Network
    ┌──────────────────────────────────────────────────┐
    │                                                  │
    │  ipn:268484800 ◄──► ipn:268485000 (Gateway)     │
    │  (OpenIPNNode)       100.96.108.37               │
    │  Monitoring/bpecho        │                      │
    │                           │ Tailscale            │
    │                           ▼                      │
    │                    ipn:268485091 (Pi05)           │
    │                    100.75.250.100                 │
    │                           │                      │
    │   ipn:268485099 ◄─────────┤                      │
    │   ipn:268485100 ◄─────────┤  Tailscale           │
    │   ipn:268485101 ◄─────────┤  (100.x.x.x)        │
    │   ipn:268485096 ◄─────────┤                      │
    │   ipn:268485097 ◄─────────┤                      │
    │   ipn:268485095 ◄─────────┘                      │
    │                                                  │
    └──────────────────────────────────────────────────┘
                            │
                            │ ZeroTier (10.16.16.x)
                            │ Pi05: 10.16.16.169
                            │ Echo: 10.16.16.17
                            ▼
    ┌──────────────────────────────────────────────────┐
    │           Anamol's Tailscale Network             │
    │                                                  │
    │        ipn:268485111 (Echo)                      │
    │        100.67.241.111 (Tailscale)                │
    │        10.16.16.17 (ZeroTier)                    │
    │                                                  │
    └──────────────────────────────────────────────────┘
```

## Key Constraint

Pi05 and Echo are on **different Tailscale networks**:
- Pi05 is on `samo.grasic@` Tailscale (can reach gateway, other DTN nodes)
- Echo is on `anamolsapkota@` Tailscale (isolated from other DTN nodes)

The **only direct link** between Pi05 and Echo is via **ZeroTier** (10.16.16.x subnet).
All traffic from Echo to the wider DTN network **must relay through Pi05**.

## ION Configuration

### Echo (ipn:268485111) - Outducts and Plans

Echo has 3 outducts, all pointing to Pi05 via ZeroTier on different ports:

| Destination | Plan | Outduct | Port | Purpose |
|-------------|------|---------|------|---------|
| Pi05 (268485091) | `udp/10.16.16.169:4556` | Direct | 4556 | Direct dtnex + chat traffic |
| Gateway (268485000) | `udp/10.16.16.169:4557` | Relay | 4557 | Gateway bundles relayed via Pi05 |
| OpenIPNNode (268484800) | `udp/10.16.16.169:4558` | Relay | 4558 | bpecho responses relayed via Pi05 |

Each plan in ION requires a **unique outduct** (one outduct per plan). Since Echo
routes multiple destinations through the same physical host (Pi05), we use different
UDP ports to create distinct outducts.

### Pi05 (ipn:268485091) - Inducts

Pi05 listens on 3 UDP ports to receive traffic from Echo:

| Port | Induct | Traffic |
|------|--------|---------|
| 4556 | `udp/0.0.0.0:4556` | Standard DTN bundles (from Echo + all Tailscale nodes) |
| 4557 | `udp/0.0.0.0:4557` | Gateway-destined bundles from Echo |
| 4558 | `udp/0.0.0.0:4558` | Monitoring-destined bundles from Echo |

Pi05 has direct plans to all Tailscale nodes via `100.x.x.x` addresses.

### Pi05 (ipn:268485091) - Outducts and Plans

| Destination | Plan | Outduct |
|-------------|------|---------|
| Gateway (268485000) | `udp/100.96.108.37:4556` | Tailscale |
| Echo (268485111) | `udp/10.16.16.17:4556` | ZeroTier |
| Other nodes (099-101, 095-097) | `udp/100.x.x.x:4556` | Tailscale |

## Communication Flows

### 1. dtnex Metadata Exchange

dtnex broadcasts CBOR-encoded contact and metadata information to all neighbors
(nodes with ION plans). The flow for Echo's metadata reaching openipn.org:

```
Echo dtnex                    Pi05 dtnex                    Gateway
    │                              │                            │
    ├── CBOR metadata ──────────►  │                            │
    │   (via port 4556)            │                            │
    │                              ├── [FRWD] metadata ──────► │
    │                              │   (via Tailscale)          │
    │                              │                            ├── Publish to
    │                              │                            │   openipn.org
```

**Key**: Pi05's dtnex receives Echo's metadata (`[RECV]`), then **forwards** it
(`[FRWD]`) to all other neighbors including the gateway. This is application-layer
forwarding within dtnex, not ION bundle forwarding.

### 2. bpecho Monitoring (openipn.org UP/DOWN status)

The OpenIPNNode (268484800) sends bpecho pings to discovered nodes. For Echo:

```
OpenIPNNode (268484800)        Gateway        Pi05           Echo
    │                            │              │              │
    ├── bpecho ping ──────────► │              │              │
    │   (dest: ipn:268485111)   ├── forward ──►│              │
    │                           │              ├── forward ──►│
    │                           │              │   (ZeroTier) │
    │                           │              │              │
    │                           │              │◄── response ─┤
    │                           │◄── forward ──┤   (port 4558)│
    │◄── bpecho response ──────┤              │              │
    │                           │              │              │
```

**Requirement**: For this flow to work:
- Gateway must have contacts for 268485111 and CGR route through Pi05
- Pi05 must forward transit bundles (ipnfw)
- Echo must have plan for 268484800 (via Pi05:4558)

### 3. DTN Chat Messages

Messages between Pi05 and Echo flow directly over ZeroTier:

```
Pi05 dtn-chat                              Echo dtn-chat
    │                                          │
    ├── bpsource ipn:268485111.7 ────────────► │ (received by bprecvfile)
    │                                          │
    │ ◄──────────── bpsource ipn:268485091.7 ──┤
    │   (received by bprecvfile)               │
```

## Current Status (Last Updated: 2026-05-14 11:40 NPT)

| Node | openipn.org | Metadata Visible | bpecho | Notes |
|------|-------------|------------------|--------|-------|
| Pi05 (268485091) | **UP** | Yes | Working | 87.6% uptime, 0% loss |
| Echo (268485111) | **DOWN** | **Yes** | Not receiving pings | Metadata forwarded via Pi05, bpecho path needs gateway routing |

### Issue: Echo shows DOWN

Echo's metadata is visible on openipn.org (forwarded by Pi05's dtnex), but bpecho
pings from 268484800 are not reaching Echo. The monitoring node needs the
gateway to have a CGR route to Echo through Pi05. The contacts have been
exchanged via dtnex but the gateway may not yet have established the forwarding
path.

### Routing Summary

```
Echo → Pi05:  ZeroTier 10.16.16.169:4556  (WORKING)
Echo → GW:    Pi05:4557 relay → Pi05 CGR → 100.96.108.37  (WORKING - metadata arrives)
Echo → Monitor: Pi05:4558 relay → Pi05 CGR → GW → 268484800  (CONFIGURED - needs testing)
GW → Echo:    GW → Pi05 (Tailscale) → Echo (ZeroTier)  (PENDING - needs GW CGR route)
Monitor → Echo: 268484800 → GW → Pi05 → Echo  (PENDING - needs GW forwarding)
```

## Files

| File | Node | Purpose |
|------|------|---------|
| `/home/echo/dtn/host268485111.rc` | Echo | ION startup configuration |
| `/home/echo/dtn/dtnex.conf` | Echo | dtnex metadata exchange config |
| `/home/echo/dtn/dtn-chat/` | Echo | DTN Chat application |
| `/home/pi05/dtn/host268485091.rc` | Pi05 | ION startup configuration |
| `/home/pi05/dtn/dtnex.conf` | Pi05 | dtnex metadata exchange config |
| `/home/pi05/dtn/dtn-chat/` | Pi05 | DTN Chat application |
