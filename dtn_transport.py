"""DTN bundle transport - send and receive chat bundles via ION.

Architecture:
- bpsource: sends bundles to remote nodes (ipn:<dest>.7)
- bprecvfile: receives bundles on local .7 endpoint as files
- BundleReceiver polls for received files in a watch directory
- Local messages stored directly in DB (ION cannot loopback to same node)
"""

import subprocess
import threading
import json
import time
import os
import glob
import config


def send_bundle_to_remote(dest_node, payload):
    """Send a bundle to a remote node via bpsource. Returns True on success."""
    payload_str = json.dumps(payload)
    dest_eid = f"ipn:{dest_node}.{config.CHAT_SERVICE_NUMBER}"
    try:
        proc = subprocess.run(
            ["bpsource", dest_eid, payload_str],
            timeout=10,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            print(f"[dtn] sent bundle to {dest_eid}")
            return True
        else:
            print(f"[dtn] bpsource error for {dest_eid}: {proc.stderr.strip()}")
            return False
    except subprocess.TimeoutExpired:
        print(f"[dtn] timeout sending to {dest_eid}")
        return False
    except Exception as e:
        print(f"[dtn] send error for {dest_eid}: {e}")
        return False


class BundleReceiver(threading.Thread):
    """Receives incoming DTN bundles on .7 using bprecvfile.
    bprecvfile writes each received bundle as a file in the CWD.
    This thread polls for new files and processes them."""

    def __init__(self, on_message=None, watch_dir=None):
        super().__init__(daemon=True)
        self._running = True
        self.on_message = on_message
        self.watch_dir = watch_dir or "/tmp/dtn-chat-recv"
        self._process = None

    def run(self):
        os.makedirs(self.watch_dir, exist_ok=True)
        # Clean old files
        for f in glob.glob(os.path.join(self.watch_dir, "testfile*")):
            try:
                os.remove(f)
            except OSError:
                pass

        local_eid = f"ipn:{config.LOCAL_NODE_NUMBER}.{config.CHAT_SERVICE_NUMBER}"

        while self._running:
            try:
                print(f"[receiver] starting bprecvfile on {local_eid} in {self.watch_dir}")
                self._process = subprocess.Popen(
                    ["bprecvfile", local_eid],
                    cwd=self.watch_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                # bprecvfile runs indefinitely, receiving bundles as files
                # We poll the directory for new files
                self._poll_for_files()
                self._process.wait()
            except Exception as e:
                print(f"[receiver] error: {e}")
            if self._running:
                print("[receiver] restarting in 5s...")
                time.sleep(5)

    def _poll_for_files(self):
        """Poll watch_dir for new files from bprecvfile."""
        seen = set()
        while self._running and self._process and self._process.poll() is None:
            try:
                files = glob.glob(os.path.join(self.watch_dir, "testfile*"))
                for fpath in files:
                    if fpath in seen:
                        continue
                    seen.add(fpath)
                    self._process_file(fpath)
            except Exception as e:
                print(f"[receiver] poll error: {e}")
            time.sleep(0.5)

    def _process_file(self, fpath):
        """Read a received bundle file and extract the chat message."""
        try:
            with open(fpath, "r") as f:
                content = f.read().strip()
            os.remove(fpath)
            if not content:
                return
            try:
                data = json.loads(content)
                if data.get("m") and self.on_message:
                    self.on_message(data)
                    print(f"[received] {data.get('n', '?')} ({data.get('s', '?')}): {data.get('m', '')}")
            except json.JSONDecodeError:
                print(f"[receiver] non-JSON bundle: {content[:100]}")
        except Exception as e:
            print(f"[receiver] file error: {e}")

    def stop(self):
        self._running = False
        if self._process:
            self._process.terminate()


def init_sender():
    """No persistent sender needed - bpsource is called per-message."""
    print("[dtn] sender ready (using bpsource per-message)")
