"""Start both services in one process: the main backend (:8000) and the
vision recognition service (:8010), then open the web GUI in your browser.

    python run.py

Env vars: HOST, BACKEND_PORT, VISION_PORT, VISION_API_KEY, NO_BROWSER=1
"""
import asyncio
import os
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "vision"))
sys.path.insert(0, str(ROOT / "vision" / "hud"))

HOST = os.environ.get("HOST", "127.0.0.1")
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "8000"))
VISION_PORT = int(os.environ.get("VISION_PORT", "8010"))


def serve(app, host, port):
    import uvicorn
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None   # can't install signal handlers off the main thread
    asyncio.run(server.serve())


def main():
    from backend.app import app as backend_app
    import vision_api

    t1 = threading.Thread(target=serve, args=(backend_app, HOST, BACKEND_PORT), daemon=True)
    t2 = threading.Thread(target=serve, args=(vision_api.app, HOST, VISION_PORT), daemon=True)
    t1.start()
    t2.start()

    url = f"http://{HOST}:{BACKEND_PORT}"
    print(f"Backend:  {url}")
    print(f"Vision:   http://{HOST}:{VISION_PORT}")
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    print("Ctrl+C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
