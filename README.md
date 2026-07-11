# yum 🍜

Self-hosted system that converts Instagram cooking Reels/Posts into structured
recipes with high accuracy - and exports them to Mealie, Tandoor, Obsidian
(Markdown), or plain JSON.

Instagram posts are treated as **a collection of information sources**, not just
videos. The pipeline merges evidence from:

1. Audio narration (Whisper transcription)
2. On-screen text overlays (OCR)
3. Caption & hashtags
4. Creator comments & replies (often the most accurate quantities!)
5. Top community comments
6. Visual analysis (equipment, un-narrated ingredients) via optional VLM

Every extracted fact carries its **source** and a **confidence score**, and
recipes are validated for missing quantities, duplicates, and unreferenced
ingredients before export.

## Quick start (Docker Compose)

```bash
# 1. Bootstrap config (only SECRET_KEY is required - everything else is
#    configured through the web UI)
cp .env.example .env
echo "SECRET_KEY=$(openssl rand -hex 32)" >> .env

# 2. Run
docker compose up -d --build

# 3. Open http://localhost:8000 and configure via the Settings page:
#    - Connect your Instagram account (guided login wizard, 2FA supported)
#    - Choose Whisper/OCR engines (local models or cloud APIs)
#    - Set your LLM provider + API key
#    - Pick export targets (Mealie / Tandoor / Markdown / JSON)
```

To bake local models (faster-whisper, PaddleOCR) into the image, set
`INSTALL_LOCAL_MODELS: "true"` in `docker-compose.yml` before building.

## Usage

### Web UI

Open `http://localhost:8000`:

- **Dashboard** - paste a Reel URL (or shared text) and watch extraction live
- **Recipes** - browse, search, view, and export extracted recipes; copy
  Obsidian-ready Markdown or download `.md`/JSON
- **Jobs** - per-job pipeline timeline with stage durations, warnings, and all
  raw evidence (transcript, OCR, comments, vision) for debugging
- **Settings** - all configuration, including the guided Instagram login

### API (mobile share sheet)

Share a Reel from the Instagram app (or paste any text containing the URL):

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d '{"text": "Check this out! https://www.instagram.com/reel/ABC123/?igsh=xyz"}'
# -> {"job_id": "…", "url": "https://www.instagram.com/reel/ABC123/", "status": "pending"}
```

On mobile, use an app like **HTTP Shortcuts** (Android) or **Shortcuts** (iOS)
to POST the share-sheet text directly to this endpoint.

Other endpoints:

```bash
GET  /api/v1/jobs                  # list jobs
GET  /api/v1/jobs/<id>             # status + structured recipe JSON
GET  /api/v1/jobs/<id>/events      # stage-by-stage event log
GET  /api/v1/jobs/<id>/markdown    # Obsidian-ready markdown
POST /api/v1/jobs/<id>/export      # on-demand export {"targets": ["mealie"]}
GET  /api/v1/settings              # settings (secrets masked)
PUT  /api/v1/settings              # update settings
GET  /health                       # config summary
```

## Configuration

All runtime configuration lives **in the database** and is managed through the
web UI. Secrets (API keys, Instagram session) are **encrypted at rest** with a
key derived from `SECRET_KEY` - the single required environment variable.

Every AI engine is independently configurable between **local models** and
**cloud APIs**:

| Stage          | Local option            | API option                 |
|----------------|-------------------------|----------------------------|
| Speech-to-text | faster-whisper          | OpenAI / Groq              |
| OCR            | PaddleOCR               | - (Tesseract bundled)      |
| Reconstruction | Ollama (`ollama/...`)   | Gemini / OpenAI / Anthropic|
| Vision (opt.)  | `ollama/qwen2.5vl`      | any LiteLLM VLM            |

### Instagram authentication

Instagram heavily rate-limits anonymous scraping. Use the **guided login
wizard** in Settings: enter your username/password (2FA supported), and yum
stores only the resulting session (encrypted) - never your password.
Consider using a secondary account.

## Pipeline

```
Share-sheet text → URL parse → Download (yt-dlp, session auth)
  → Comments (Instaloader) → Audio → Whisper → Keyframes → OCR → Vision
  → Evidence collation (priority: creator comments > caption > OCR > transcript > vision)
  → LLM reconstruction (strict JSON schema + per-fact confidence)
  → Validation (missing amounts, duplicates, unreferenced ingredients)
  → SQLite storage (structured JSON + rendered Markdown)
  → Export (Mealie / Tandoor / Markdown / JSON)
```

Every stage emits timed events to the job log, visible in the Jobs view.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt      # + requirements-local.txt for local models
export SECRET_KEY=$(openssl rand -hex 32)
.venv/bin/pytest                                # run test suite
.venv/bin/uvicorn src.main:app --reload         # run dev server
.venv/bin/alembic upgrade head                  # apply DB migrations manually
```

Database schema is managed with Alembic; migrations run automatically at
startup.

## Notes & limitations

- **Back up `./data`** - it contains the database with your config, encrypted
  secrets, and all extracted recipes.
- Changing `SECRET_KEY` makes previously stored secrets unreadable (you'll
  need to re-enter API keys and reconnect Instagram).
- Comment fetching is best-effort - if Instagram blocks it, the pipeline
  continues with the remaining evidence sources.
- Processing is intentionally serialized (one job at a time) since Whisper/OCR
  are resource-heavy; adjust `max_workers` in `src/main.py` if you have the
  hardware.
