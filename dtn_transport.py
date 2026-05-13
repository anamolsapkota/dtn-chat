"""DTN bundle transport - send and receive chat bundles.

Architecture: ALL messages flow through DTN bundles.
- Endpoint .7: receives all chat bundles (BundleReceiver reads stdout)
- Endpoint .8: used by BundleSender to send bundles TO .7 (loopback)
- bpsource: sends bundles to remote nodes

Local messages: written to BundleSender stdin -> bundle from .8 to .7 -> received by BundleReceiver
Remote messages: bpsource to remote ipn:<node>.7 -> arrives at remote bpchat
"""

import subprocess
import threading
import json
import time
import config

_sender = None


def send_bundle_to_local(payload):
    """Send a bundle that will be received by our own BundleReceiver.
    Uses bpchat .8 -> .7 loopback so the message traverses DTN."""
    global _sender
    if _sender and _sender.is_alive():
        _sender.write(payload)
    else:
        # Fallback: use bpsource
        _send_via_bpsource(config.LOCAL_NODE_NUMBER, payload)


def send_bundle_to_remote(dest_node, payload):
    """Send a bundle to a remote node via bpsource."""
    _send_via_bpsource(dest_node, payload)


def _send_via_bpsource(dest_node, payload):
    payload_str = json.dumps(payload)
    dest_eid = f"ipn:{dest_node}.{config.CHAT_SERVICE_NUMBER}"
    try:
        subprocess.run(
            ["bpsource", dest_eid, payload_str],
            timeout=10,
            capture_output=True,
        )
    except subprocess.TimeoutExpired:
        pass


class BundleSender(threading.Thread):
    """Maintains a bpchat process on .8 that sends to .7.
    Writing JSON to its stdin creates a DTN bundle from .8 to .7."""

    def __init__(self):
        super().__init__(daemon=True)
        self._process = None
        self._running = True
        self._lock = threading.Lock()

    def run(self):
        send_eid = f"ipn:{config.LOCAL_NODE_NUMBER}.8"
        recv_eid = f"ipn:{config.LOCAL_NODE_NUMBER}.{config.CHAT_SERVICE_NUMBER}"
        while self._running:
            try:
                self._process = subprocess.Popen(
                    ["bpchat", send_eid, recv_eid],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
                print(f"[sender] bpchat {send_eid} -> {recv_eid} started")
                # Keep alive by reading stdout (discard output)
                for line in self._process.stdout:
                    pass
                self._process.wait()
            except Exception as e:
                print(f"[sender] error: {e}")
            if self._running:
                print("[sender] restarting in 5s...")
                time.sleep(5)

    def write(self, payload):
        """Write a JSON payload to bpchat stdin, creating a DTN bundle."""
        with self._lock:
            if self._process and self._process.stdin:
                try:
                    line = json.dumps(payload) + "\n"
                    self._process.stdin.write(line)
                    self._process.stdin.flush()
                    return True
                except (BrokenPipeError, OSError) as e:
                    print(f"[sender] write error: {e}")
        return False

    def stop(self):
        self._running = False
        if self._process:
            try:
                self._process.stdin.close()
            except Exception:
                pass
            self._process.terminate()


class BundleReceiver(threading.Thread):
    """Listens for incoming chat bundles on .7 via bpchat."""

    def __init__(self, on_message=None):
        super().__init__(daemon=True)
        self._process = None
        self._running = True
        self.on_message = on_message

    def run(self):
        while self._running:
            try:
                local_eid = f"ipn:{config.LOCAL_NODE_NUMBER}.{config.CHAT_SERVICE_NUMBER}"
                # bpchat <own_eid> <dest_eid_for_sending>
                # We use .7 for both since this instance only receives
                self._process = subprocess.Popen(
                    ["bpchat", local_eid, local_eid],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
                print(f"[receiver] listening on {local_eid}")
                for line in self._process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    self._handle_line(line)
                self._process.wait()
            except Exception as e:
                print(f"[receiver] error: {e}")
            if self._running:
                print("[receiver] restarting in 5s...")
                time.sleep(5)

    def _handle_line(self, line):
        try:
            data = json.loads(line)
            if not data.get("m"):
                return
            if self.on_message:
                self.on_message(data)
            print(f"[received] {data.get('n', '?')} ({data.get('s', '?')}): {data.get('m', '')}")
        except json.JSONDecodeError:
            pass

    def stop(self):
        self._running = False
        if self._process:
            try:
                self._process.stdin.close()
            except Exception:
                pass
            self._process.terminate()


def init_sender():
    """Start the bundle sender thread."""
    global _sender
    _sender = BundleSender()
    _sender.start()
    # Give it time to start
    time.sleep(1)
