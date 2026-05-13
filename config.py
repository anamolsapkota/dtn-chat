import subprocess

LOCAL_NODE_NUMBER = None  # Auto-detected at startup
LOCAL_NODE_NAME = None
CHAT_SERVICE_NUMBER = 7
NODES_METADATA_PATH = "/home/pi05/dtn/nodesmetadata.txt"
DATABASE_PATH = "/home/pi05/dtn/dtn-chat/chat.db"
FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5000


def detect_node():
    """Auto-detect local IPN node number from ionadmin."""
    global LOCAL_NODE_NUMBER, LOCAL_NODE_NAME
    try:
        proc = subprocess.run(
            ["ionadmin"], input=b"l\nq\n", capture_output=True, text=True, timeout=5
        )
        for line in proc.stdout.splitlines():
            if "node number" in line.lower():
                import re
                m = re.search(r"(\d{6,})", line)
                if m:
                    LOCAL_NODE_NUMBER = int(m.group(1))
                    break
    except Exception:
        pass

    if LOCAL_NODE_NUMBER is None:
        # Fallback: parse the rc file
        import glob
        for f in glob.glob("/home/pi05/dtn/ione-code/host*.rc"):
            import re
            m = re.search(r"host(\d+)\.rc", f)
            if m:
                LOCAL_NODE_NUMBER = int(m.group(1))
                break

    if LOCAL_NODE_NUMBER is None:
        LOCAL_NODE_NUMBER = 268485091  # Final fallback

    # Get name from metadata
    try:
        with open(NODES_METADATA_PATH) as f:
            for line in f:
                if line.startswith(str(LOCAL_NODE_NUMBER) + ":"):
                    parts = line.strip().split(":", 1)[1].split(",")
                    LOCAL_NODE_NAME = parts[0] if parts else f"node-{LOCAL_NODE_NUMBER}"
                    return
    except Exception:
        pass
    LOCAL_NODE_NAME = f"node-{LOCAL_NODE_NUMBER}"
