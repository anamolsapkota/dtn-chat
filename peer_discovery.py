import subprocess
import re
import config
import database


def discover_from_metadata():
    """Parse nodesmetadata.txt for known nodes."""
    try:
        with open(config.NODES_METADATA_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or ":" not in line:
                    continue
                node_str, rest = line.split(":", 1)
                try:
                    node_number = int(node_str)
                except ValueError:
                    continue
                parts = rest.split(",")
                node_name = parts[0] if parts else f"node-{node_number}"
                description = rest
                if node_number != config.LOCAL_NODE_NUMBER:
                    database.upsert_node(node_number, node_name, description, "metadata")
    except FileNotFoundError:
        pass


def discover_from_ipnadmin():
    """Query ION's egress plans to find configured neighbors."""
    try:
        proc = subprocess.run(
            ["ipnadmin"], input="l plan\nq\n",
            capture_output=True, text=True, timeout=5,
        )
        for line in proc.stdout.splitlines():
            m = re.search(r"(\d{6,})\s+xmit", line)
            if m:
                node_number = int(m.group(1))
                if node_number != config.LOCAL_NODE_NUMBER:
                    database.upsert_node(node_number, f"node-{node_number}", "", "ipnadmin")
    except Exception:
        pass


def discover_from_contacts():
    """Query ION's contact graph for all known nodes."""
    try:
        proc = subprocess.run(
            ["ionadmin"], input="l contact\nq\n",
            capture_output=True, text=True, timeout=5,
        )
        seen = set()
        for line in proc.stdout.splitlines():
            for m in re.finditer(r"node (\d{6,})", line):
                node_number = int(m.group(1))
                if node_number != config.LOCAL_NODE_NUMBER and node_number not in seen:
                    seen.add(node_number)
                    database.upsert_node(node_number, f"node-{node_number}", "", "contact")
    except Exception:
        pass


def get_ip_to_ipn_map():
    """Build a mapping of Tailscale/network IP -> IPN node number from ipnadmin plans."""
    ip_map = {}
    try:
        proc = subprocess.run(
            ["ipnadmin"], input="l plan\nq\n",
            capture_output=True, text=True, timeout=5,
        )
        for line in proc.stdout.splitlines():
            # Format: "268485000 xmit 100.96.108.37:4556 xmit rate: 0"
            m = re.match(r"\s*(\d{6,})\s+xmit\s+(\d+\.\d+\.\d+\.\d+):", line)
            if m:
                node_number = int(m.group(1))
                ip = m.group(2)
                if ip != "127.0.0.1" and node_number != config.LOCAL_NODE_NUMBER:
                    ip_map[ip] = node_number
    except Exception:
        pass
    # Add ALL local IPs (loopback, LAN, Tailscale, ZeroTier) -> local node
    ip_map["127.0.0.1"] = config.LOCAL_NODE_NUMBER
    try:
        proc = subprocess.run(
            ["hostname", "-I"],
            capture_output=True, text=True, timeout=5,
        )
        for ip in proc.stdout.strip().split():
            if ":" not in ip:  # skip IPv6
                ip_map[ip] = config.LOCAL_NODE_NUMBER
    except Exception:
        pass
    return ip_map


def resolve_ipn_from_ip(client_ip):
    """Given a client IP, return (ipn_number, node_name) or (None, None)."""
    ip_map = get_ip_to_ipn_map()
    ipn = ip_map.get(client_ip)
    if ipn:
        # Try to get node name from DB
        node = database.get_node_by_number(ipn)
        name = node["node_name"] if node else f"node-{ipn}"
        return ipn, name
    return None, None


def refresh_all():
    """Run all discovery methods."""
    discover_from_metadata()
    discover_from_ipnadmin()
    discover_from_contacts()
