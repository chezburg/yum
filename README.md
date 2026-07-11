# Instagram Recipe Extractor

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
# 1. Configure
cp .env.example .env
#    Edit .env: set LLM_MODEL + LLM_API_KEY (or point at Ollama),
#    choose Whisper/OCR engines, pick export targets.

# 2. Provide Instagram cookies (required for reliable downloads/comments)
#    Export cookies from your logged-in browser in Netscape format
#    (e.g. "Get cookies.txt LOCALLY" extension) and save as:
mkdir -p config
#    config/instagram_cookies.txt

# 3. Run
docker compose up -d --build
```

To bake local models (faster-whisper, PaddleOCR) into the image, set
`INSTALL_LOCAL_MODELS: "true"` in `docker-compose.yml` before building.

## Usage

Share a Reel from the Instagram app (or paste any text containing the URL):

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d '{"text": "Check this out! https://www.instagram.com/reel/ABC123/?igsh=xyz"}'
# -> {"job_id": "…", "url": "https://www.instagram.com/reel/ABC123/", "status": "pending"}
```

On mobile, use an app like **HTTP Shortcuts** (Android) or **Shortcuts** (iOS)
to POST the share-sheet text directly to this endpoint.

Check progress and results:

```bash
curl http://localhost:8000/api/v1/jobs                 # list jobs
curl http://localhost:8000/api/v1/jobs/<id>            # status + structured recipe JSON
curl http://localhost:8000/api/v1/jobs/<id>/markdown   # Obsidian-ready markdown
curl http://localhost:8000/health                      # config summary
```

If `AUTO_EXPORT_ON_SUCCESS=true`, completed recipes are automatically pushed to
every target in `EXPORT_TARGETS` (comma-separated: `mealie`, `tandoor`,
`markdown`, `json`).

## Configuration

All configuration lives in `.env` (see `.env.example` for the full documented
template). Every AI engine is independently configurable between **local
models** and **cloud APIs**:

| Stage          | Local option            | API option                 |
|----------------|-------------------------|----------------------------|
| Speech-to-text | `WHISPER_ENGINE=local` (faster-whisper) | `openai` / `groq` |
| OCR            | `OCR_ENGINE=paddleocr`  | `tesseract` (bundled)      |
| Reconstruction | `LLM_MODEL=ollama/...`  | `gemini/...`, `gpt-...`, `anthropic/...` |
| Vision (opt.)  | `VISION_MODEL=ollama/qwen2.5vl` | any LiteLLM VLM    |

## Pipeline

```
Share-sheet text → URL parse → Download (yt-dlp, cookies)
  → Comments (Instaloader) → Audio → Whisper → Keyframes → OCR → Vision
  → Evidence collation (priority: creator comments > caption > OCR > transcript > vision)
  → LLM reconstruction (strict JSON schema + per-fact confidence)
  → Validation (missing amounts, duplicates, unreferenced ingredients)
  → SQLite storage (structured JSON + rendered Markdown)
  → Export (Mealie / Tandoor / Markdown / JSON)
```

## Development

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt      # + requirements-local.txt for local models
.venv/bin/pytest                                # run test suite
.venv/bin/uvicorn src.main:app --reload         # run dev server
```

## Notes & limitations

- **Instagram cookies**: unauthenticated scraping is heavily rate-limited by
  Instagram. A logged-in session cookie file is the assumed default. Keep the
  cookie file out of version control (it is gitignored).
- Comment fetching is best-effort - if Instagram blocks it, the pipeline
  continues with the remaining evidence sources.
- Processing is intentionally serialized (one job at a time) since Whisper/OCR
  are resource-heavy; adjust `max_workers` in `src/main.py` if you have the
  hardware.
