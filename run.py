"""STOCKY single-command launcher.

``python run.py`` is the single entry point for running the whole STOCKY app
(frontend + backend) without needing two separate terminals.

What it does, in order:
1. Resolves all paths relative to this file's own location, so it works no
   matter what directory it's invoked from.
2. Builds the frontend (npm install + npm run build) if ``frontend/dist``
   doesn't exist yet (or is empty), or unconditionally if ``--rebuild-frontend``
   is passed. Subsequent runs skip the build for a fast startup, since
   ``backend/app/main.py`` mounts ``frontend/dist`` as static files at ``/``
   whenever that directory is present.
3. Starts the FastAPI backend with the project's venv Python, running uvicorn
   with the backend directory as the working directory (so relative paths
   used by app.config / app.db resolve exactly as they do when someone
   manually ``cd``s into backend and runs uvicorn themselves).
4. Polls ``/api/health`` until the server is up (or times out), then opens
   the default browser to the app.
5. Streams backend logs live to the console. Ctrl+C stops the backend
   cleanly.

No third-party Python packages are required to run this script itself; it
only uses the standard library. It then shells out to the venv's Python to
run the actual backend, and to npm to build the frontend.
"""

import argparse
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

# --- Paths -------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"
FRONTEND_DIR = ROOT_DIR / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"

# Prefer the Windows venv layout; fall back to the POSIX layout so this also
# works on macOS/Linux checkouts of the same repo.
_VENV_PY_WINDOWS = BACKEND_DIR / ".venv" / "Scripts" / "python.exe"
_VENV_PY_POSIX = BACKEND_DIR / ".venv" / "bin" / "python"
VENV_PY = _VENV_PY_WINDOWS if _VENV_PY_WINDOWS.exists() else _VENV_PY_POSIX

HEALTH_URL_TEMPLATE = "http://127.0.0.1:{port}/api/health"
APP_URL_TEMPLATE = "http://127.0.0.1:{port}/"


def _find_npm() -> str:
    """Locate the npm binary to invoke.

    On Windows, ``npm.ps1`` (the default resolved by a bare ``npm`` in some
    shells) can be blocked by execution policy, so we explicitly look for
    ``npm.cmd`` first and only fall back to a bare ``npm`` as a last resort.
    """
    npm_cmd = shutil.which("npm.cmd")
    if npm_cmd:
        return npm_cmd
    npm_plain = shutil.which("npm")
    if npm_plain:
        return npm_plain
    # Last resort: hope it's resolvable on PATH when subprocess runs it.
    return "npm.cmd"


def build_frontend() -> None:
    """Install deps (if needed) and build the frontend with npm/Vite."""
    print("Building frontend (first run only)...")
    npm = _find_npm()

    node_modules = FRONTEND_DIR / "node_modules"
    try:
        if not node_modules.exists():
            print("Installing frontend dependencies (npm install)...")
            subprocess.run([npm, "install"], cwd=str(FRONTEND_DIR), check=True)

        print("Running frontend build (npm run build)...")
        subprocess.run([npm, "run", "build"], cwd=str(FRONTEND_DIR), check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"ERROR: Frontend build failed: {exc}", file=sys.stderr)
        sys.exit(1)

    print("Frontend build complete.")


def ensure_frontend_built(force_rebuild: bool) -> None:
    """Build the frontend unless a build already exists and isn't forced."""
    dist_exists_and_populated = DIST_DIR.is_dir() and any(DIST_DIR.iterdir())

    if force_rebuild or not dist_exists_and_populated:
        build_frontend()
    else:
        print(f"Frontend build found at {DIST_DIR}, skipping build.")


def ensure_venv_python() -> None:
    """Confirm the backend venv's Python exists, or exit with guidance."""
    if not VENV_PY.exists():
        print(
            "ERROR: Backend virtual environment not found at "
            f"{VENV_PY}\n"
            "Create it and install dependencies first, e.g.:\n"
            "  cd backend\n"
            "  python -m venv .venv\n"
            "  .venv\\Scripts\\python.exe -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        sys.exit(1)


def wait_for_health(port: int, timeout_seconds: float = 15.0) -> bool:
    """Poll /api/health until it responds successfully or timeout elapses."""
    url = HEALTH_URL_TEMPLATE.format(port=port)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(0.5)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the whole STOCKY app (frontend + backend) with one command."
    )
    parser.add_argument(
        "--rebuild-frontend",
        action="store_true",
        help="Force a fresh frontend build even if frontend/dist already exists.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for the backend/app server (default: 8000).",
    )
    args = parser.parse_args()

    ensure_frontend_built(force_rebuild=args.rebuild_frontend)
    ensure_venv_python()

    print(f"Starting backend with {VENV_PY} ...")
    process = subprocess.Popen(
        [
            str(VENV_PY),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.port),
        ],
        cwd=str(BACKEND_DIR),
        # Let stdout/stderr flow straight through to this console so backend
        # logs are visible live and Ctrl+C works normally.
    )

    try:
        print("Waiting for backend to become healthy...")
        if wait_for_health(args.port):
            app_url = APP_URL_TEMPLATE.format(port=args.port)
            print(f"Backend is up. Opening {app_url} in your browser...")
            webbrowser.open(app_url)
        else:
            print(
                "WARNING: Backend did not respond to health checks within the "
                "timeout. It may still be starting up - check the logs above. "
                f"You can try opening {APP_URL_TEMPLATE.format(port=args.port)} manually.",
                file=sys.stderr,
            )

        # Block here, letting the subprocess's own stdout/stderr stream to
        # the console, until the user stops it (Ctrl+C) or it exits on its
        # own.
        process.wait()
    except KeyboardInterrupt:
        print("\nStopping backend...")
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        print("Stopped.")


if __name__ == "__main__":
    main()
