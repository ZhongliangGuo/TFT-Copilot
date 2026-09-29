"""Start both services in one process: the main backend (:8000) and the
vision recognition service (:8010), then open the web GUI in your browser.

    python run.py

Env vars: HOST, BACKEND_PORT, VISION_PORT, VISION_API_KEY, NO_BROWSER=1
"""
import asyncio
import os
import signal
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

_servers = []
_stop_event = threading.Event()
_stopping = False
_browser_timer = None
_win_handler_ref = None


def serve(app, host, port):
    import uvicorn
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None   # can't install signal handlers off the main thread
    _servers.append(server)
    asyncio.run(server.serve())


def stop_all(forced: bool = False):
    global _stopping, _browser_timer
    if _stopping and forced:
        # Force immediate exit on repeated interrupt
        os._exit(0)
    _stopping = True
    _stop_event.set()
    if _browser_timer and _browser_timer.is_alive():
        _browser_timer.cancel()
    for server in list(_servers):
        server.should_exit = True


def _handle_signal(sig, frame):
    stop_all(forced=_stopping)


def main():
    global _browser_timer, _win_handler_ref
    try:
        from affinity import optimize_cpu_affinity
        optimize_cpu_affinity(verbose=True)
    except Exception:
        pass
    from backend.app import app as backend_app
    import vision_api

    # Register standard signal handlers
    try:
        signal.signal(signal.SIGINT, _handle_signal)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _handle_signal)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _handle_signal)
    except Exception:
        pass

    # On Windows, hook console control handler directly to reliably catch Ctrl+C in CMD/PowerShell
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)

            def _win_ctrl_handler(dwCtrlType):
                # dwCtrlType: 0 (CTRL_C), 1 (CTRL_BREAK), 2 (CTRL_CLOSE)
                stop_all(forced=_stopping)
                return True

            _win_handler_ref = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong)(_win_ctrl_handler)
            ctypes.windll.kernel32.SetConsoleCtrlHandler(_win_handler_ref, True)
        except Exception:
            pass

    t1 = threading.Thread(target=serve, args=(backend_app, HOST, BACKEND_PORT), daemon=True)
    t2 = threading.Thread(target=serve, args=(vision_api.app, HOST, VISION_PORT), daemon=True)
    t1.start()
    t2.start()

    url = f"http://{HOST}:{BACKEND_PORT}"
    print(f"Backend:  {url}")
    print(f"Vision:   http://{HOST}:{VISION_PORT}")
    if not os.environ.get("NO_BROWSER"):
        _browser_timer = threading.Timer(1.2, lambda: webbrowser.open(url))
        _browser_timer.start()

    print("Ctrl+C to stop.")
    try:
        while not _stop_event.wait(timeout=0.2):
            pass
    except KeyboardInterrupt:
        stop_all(forced=_stopping)

    print("\nStopping services...")
    stop_all()
    t1.join(timeout=2.0)
    t2.join(timeout=2.0)
    if t1.is_alive() or t2.is_alive():
        os._exit(0)
    print("Stopped.")


if __name__ == "__main__":
    main()

