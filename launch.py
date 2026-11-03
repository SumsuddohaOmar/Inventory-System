"""Windows shortcut helper: run the server and open its page once it is ready.

Uses only Python's standard library. Never installs software, registers Windows
startup tasks or changes firewall settings. LAN HTTPS is explicit opt-in only.
"""

from pathlib import Path
import subprocess
import socket
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
import webbrowser
import json
import os
import ssl
from app.version import VERSION

ROOT = Path(__file__).resolve().parent
BASE_PORT = 8000


def refuse_legacy_servers():
    """Do not leave an old unauthenticated API beside the protected new app.

    We only inspect the known development ports and never terminate processes.
    An old server may use the same SQLite file, so changing ports alone cannot
    safely isolate the account-protected release from its old write endpoints.
    """
    opener=build_opener(ProxyHandler({}))
    for port in range(BASE_PORT,BASE_PORT+10):
        try:
            with opener.open(f"http://127.0.0.1:{port}/openapi.json",timeout=0.5) as response:
                description=json.load(response)
        except (URLError,OSError,ValueError):
            continue
        info=description.get("info",{})
        if info.get("title")=="Local Inventory · Phase 1" and info.get("version")!=VERSION:
            raise RuntimeError(f"An older inventory server is running on port {port}. Close its server window with Ctrl+C, then reopen Start Inventory.cmd. Your saved data stays in place.")


def inventory_is_ready(port: int = BASE_PORT) -> bool:
    """Check our application identity, without sending localhost through a proxy.

    A busy port may belong to a different app. Check the API title before reusing
    it; never stop another app to take its port.
    """
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/openapi.json", timeout=0.5) as response:
            import json
            description = json.load(response)
        return (
            description.get("info", {}).get("title") == "Local Inventory · Phase 1"
            and description.get("info", {}).get("version") == VERSION
            and "/api/categories" in description.get("paths", {})
        )
    except (URLError, OSError, ValueError):
        return False


def port_is_available(port: int) -> bool:
    """Probe loopback availability without stopping any process using that port.

    Close the probe immediately; Uvicorn owns the actual listening socket. There
    is a small race if another app takes the port afterward; Uvicorn reports it.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        # On Windows, exclusive use avoids mistaking an occupied port for free.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def choose_server() -> tuple[int, bool]:
    """Reuse a current server, or select a free local port; never reuse old code.

    Search all candidates for a current server before choosing an empty port.
    This prevents a second launcher click from starting duplicate new servers
    while an older process still occupies port 8000.
    """
    ports = range(BASE_PORT, BASE_PORT + 10)
    for port in ports:
        if inventory_is_ready(port):
            return port, True
    for port in ports:
        if port_is_available(port):
            return port, False
    raise RuntimeError("Ports 8000–8009 are busy. Close an old inventory server and try again.")


def show_when_ready(process: subprocess.Popen, port: int):
    """Wait up to 15 seconds, then open the browser only if this server responds."""
    for _ in range(30):
        if process.poll() is not None:
            return
        if inventory_is_ready(port):
            webbrowser.open(f"http://127.0.0.1:{port}")
            return
        time.sleep(0.5)
    print(f"Browser did not open automatically. Try http://127.0.0.1:{port} after startup finishes.")


def main() -> int:
    """Reuse an existing inventory server or run one until the user presses Ctrl+C."""
    try:
        refuse_legacy_servers()
    except RuntimeError as error:
        print(error,flush=True)
        return 1
    configuration=ROOT/"data"/"network.json"
    if configuration.exists():
        network=json.loads(configuration.read_text(encoding="utf-8"))
        if network.get("enabled"):
            return launch_https(network)
    try:
        port, running = choose_server()
    except RuntimeError as error:
        print(error, flush=True)
        return 1
    url = f"http://127.0.0.1:{port}"
    if running:
        print(f"Updated inventory is already running: {url}")
        webbrowser.open(url)
        return 0

    print(f"Starting updated Local Inventory. Open {url}", flush=True)
    if port != BASE_PORT:
        print("Another server is using port 8000. This window uses a different port and the same saved database.", flush=True)
    print("Keep this window open. Press Ctrl+C to stop the server.", flush=True)
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
    )
    threading.Thread(target=show_when_ready, args=(process, port), daemon=True).start()
    try:
        return process.wait()
    except KeyboardInterrupt:
        # The console normally delivers Ctrl+C to Uvicorn too. Give it time to
        # shut down cleanly before terminating only the child we started.
        try:
            return process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait()
            return 0


def launch_https(configuration):
    """Start opt-in LAN HTTPS using the certificate generated for this address.

    The readiness client trusts only our local CA; it NEVER disables certificate
    verification. Browser/Android trust must be installed by the device owner.
    """
    from urllib.request import HTTPSHandler
    address=configuration["address"];port=int(configuration["port"])
    url=f"https://{address}:{port}"
    context=ssl.create_default_context(cafile=str(ROOT/"data"/configuration["ca"]))
    opener=build_opener(ProxyHandler({}),HTTPSHandler(context=context))
    def ready():
        try:
            with opener.open(url+"/openapi.json",timeout=1) as response:return json.load(response).get("info",{}).get("version")==VERSION
        except (OSError,ValueError):return False
    if ready():webbrowser.open(url);return 0
    environment=os.environ.copy();environment["INVENTORY_ALLOWED_HOSTS"]=address
    process=subprocess.Popen([sys.executable,"-m","uvicorn","app.main:app","--host",address,"--port",str(port),
        "--ssl-certfile",str(ROOT/"data"/configuration["certificate"]),"--ssl-keyfile",str(ROOT/"data"/configuration["key"])],cwd=ROOT,env=environment)
    print(f"Local HTTPS: {url}. Devices must trust data/tls/ca.crt.",flush=True)
    def open_page():
        for _ in range(30):
            if process.poll() is not None:return
            if ready():webbrowser.open(url);return
            time.sleep(0.5)
    threading.Thread(target=open_page,daemon=True).start()
    try:return process.wait()
    except KeyboardInterrupt:
        try:return process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.terminate();process.wait();return 0


def run_launcher():
    """Keep unexpected startup failures readable and save a local error log.

    A File Explorer launch otherwise provides no durable traceback once its
    console closes. The private data directory is omitted from source archives.
    Logging must never hide the original error if that folder is unwritable.
    """
    try:
        return main()
    except Exception as error:
        import traceback
        details=traceback.format_exc()
        print(f"Inventory could not start: {error}",flush=True)
        try:
            folder=ROOT/"data"
            folder.mkdir(parents=True,exist_ok=True)
            log=folder/"launcher-error.log"
            log.write_text(details,encoding="utf-8")
            print(f"Startup details saved to: {log}",flush=True)
        except OSError:
            print(details,flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(run_launcher())
