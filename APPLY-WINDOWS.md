# FRIDAY complete update: backend audit + Bento browser UI (Windows PowerShell)

This package contains every changed and new file from both updates:

- **Backend and agent:** security, resumable sessions, hybrid search and memory, OCR and image understanding, coding-agent reliability, streaming and model fallback, health checks and logging, tests and docs.
- **Browser UI:** Bento dashboard, Spark logo, light and dark themes, chat working status, Memory page.

Your terminal console (`friday_agent/terminal.py`) is NOT in this package. Keep the one you have.

## 1. Back up

```powershell
cd C:\path\to\FRIDAY
git add -A; git commit -m "before FRIDAY complete update"   # or copy the folder
```

## 2. Copy the files and remove the two old ones

```powershell
Copy-Item "$HOME\Downloads\friday-update\*" . -Recurse -Force
Remove-Item src\app\workspace.css, src\app\thinking\ThinkingExample.tsx -ErrorAction SilentlyContinue
Remove-Item .\APPLY-WINDOWS.md -ErrorAction SilentlyContinue   # this guide, not part of the project
```

## 3. Install dependencies

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
pip install -r backend\requirements-agent-optional.txt   # optional: MCP / voice
pip install -r backend\requirements-ocr.txt              # optional: OCR + Excel
winget install UB-Mannheim.TesseractOCR                  # optional: OCR engine (reopen PowerShell afterwards)
npm install
```

## 4. Models (Ollama)

```powershell
ollama pull nomic-embed-text
ollama pull qwen2.5-coder:7b              # or keep $env:FRIDAY_MODEL = "llama3.2:latest"
ollama pull llava:7b                      # optional: image understanding
$env:FRIDAY_VISION_MODEL = "llava:7b"     # optional
```

## 5. Verify

```powershell
python -m pytest -q        # expect: 78 passed
npx tsc --noEmit
npm run lint
npm run build
```

## 6. Start

```powershell
friday start               # or: python -m uvicorn backend.app.main:app --port 8000   and   npm run dev
Invoke-RestMethod http://127.0.0.1:8000/api/health | ConvertTo-Json -Depth 5
```

Open http://localhost:3000. The app opens on the dashboard. The theme follows Windows until you
click the sun/moon button or choose one in Settings. Shortcuts: Ctrl+K search, Ctrl+Shift+O new chat.
Run `friday agent` for the terminal console (unchanged).

## Notes

- Your existing database upgrades itself on first start (new columns, full-text index, memories table).
  Documents you already have are indexed automatically. Images uploaded before this update have no
  text: click the reprocess button on the Documents page after setting up OCR or a vision model.
- Requests from this PC need no token. To use FRIDAY from another device, set `$env:FRIDAY_ALLOWED_HOSTS`
  and send the token from `data\api_token` as `Authorization: Bearer <token>`.
- All settings, environment variables and API routes: `docs/operations.md`. What changed and what is
  still open: `PROGRESS.md`.
