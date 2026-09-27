# STOCKY

## Quick Start

**One-time setup** (skip anything you've already done):
- Create the backend virtual environment and install dependencies:
  ```
  cd backend
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  ```
- Make sure `backend/.env` exists with these variables set (see `backend/.env.example`):
  `LLM_PROVIDER`, `LLM_GATEWAY_URL`, `LLM_GATEWAY_API_KEY`, `LLM_MODEL`.

**Run everything with one command:**

```
python run.py
```

(from the `c:\stocky` folder). This builds the frontend on first run and starts the backend, which serves the whole app at http://localhost:8000 — your browser will open automatically.

Other flags:
- `python run.py --rebuild-frontend` — force a fresh frontend build (use this after making frontend changes).
- `python run.py --port 8080` — run on a different port.
- Press `Ctrl+C` to stop the server.
