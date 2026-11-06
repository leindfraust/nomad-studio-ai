"""Run the API and the Vite dev server together, and stop both on Ctrl+C.

One command for development: `npm run dev` (or `python scripts/dev.py`).

Two Windows traps this avoids:
- `python` on PATH may not be the project venv, which has the dependencies.
- launching Vite through npm hands the script to Git Bash, whose `dirname.exe`
  fails to create a signal pipe when stdout is already a pipe. Starting the Vite
  binary with node directly sidesteps the shell entirely.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
BACKEND_PORT = os.environ.get("NOMADGUARD_PORT", "8787")
REQUIRED_MODULES = "fastapi, uvicorn, pydantic"


def resolve_python() -> Path:
    """The interpreter that actually has the project dependencies."""
    candidates = [Path(sys.executable), ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python"]
    for candidate in candidates:
        if candidate.is_file() and _has_dependencies(candidate):
            return candidate
    print(
        "No Python with the project dependencies was found.\n"
        "Create one first:\n"
        "  python -m venv .venv\n"
        "  .venv\\Scripts\\activate        # or: source .venv/bin/activate\n"
        "  pip install -e .\n",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _has_dependencies(python: Path) -> bool:
    result = subprocess.run(
        [str(python), "-c", f"import {REQUIRED_MODULES}"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def vite_command() -> list[str]:
    """Invoke Vite's own entry point instead of going through an npm shell."""
    entry = FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"
    if not entry.is_file():
        print(
            "The frontend dependencies are not installed.\n"
            "Run:  npm --prefix frontend ci\n",
            file=sys.stderr,
        )
        raise SystemExit(1)
    node = shutil.which("node")
    if not node:
        print("Node was not found on PATH. Install Node 20 or newer.\n", file=sys.stderr)
        raise SystemExit(1)
    return [node, str(entry)]


def pump(prefix: str, process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        print(f"[{prefix}] {line.rstrip()}", flush=True)


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def require_free(port: int, label: str) -> None:
    """Refuse to half-start. A dead API behind a live Vite looks like a UI bug."""
    if not port_in_use(port):
        return
    print(
        f"{label} port {port} is already in use, so a second instance would fail to bind.\n"
        f"Stop the other server first, for example:\n"
        f'  Get-NetTCPConnection -LocalPort {port} -State Listen | '
        f"ForEach-Object {{ taskkill /F /T /PID $_.OwningProcess }}\n",
        file=sys.stderr,
    )
    raise SystemExit(1)


def main() -> int:
    python = resolve_python()
    require_free(int(BACKEND_PORT), "API")
    require_free(5173, "UI")
    backend = subprocess.Popen(
        [str(python), "-m", "nomadguard", "serve", "--no-reload", "--port", BACKEND_PORT],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    frontend = subprocess.Popen(
        vite_command(),
        cwd=FRONTEND,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    print(f"python   {python}")
    print(f"API      http://127.0.0.1:{BACKEND_PORT}  (docs at /docs)")
    print("UI       http://localhost:5173")
    print("Ctrl+C stops both.\n", flush=True)

    threads = [
        threading.Thread(target=pump, args=("api", backend), daemon=True),
        threading.Thread(target=pump, args=("web", frontend), daemon=True),
    ]
    for thread in threads:
        thread.start()

    try:
        while backend.poll() is None and frontend.poll() is None:
            threading.Event().wait(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        _stop(backend)
        _stop(frontend)

    code = backend.returncode or frontend.returncode or 0
    if code:
        print(f"\nOne side exited with code {code}. The other was stopped.", file=sys.stderr)
    return 0 if code == 0 else 1


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
