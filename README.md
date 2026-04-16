# PolitiFact → S3 → Grok Compare

This project includes:

1. A script to fetch the latest PolitiFact fact checks and store raw text in S3 or local files.
2. A script to send each claim to Grok for an independent fact check.
3. A local web UI to compare PolitiFact vs Grok side by side.

Configuration is `.env`-first. CLI flags are optional overrides.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `.env` from example:

```bash
cp .env.example .env
```

If using S3, make sure AWS credentials are configured (for example via `AWS_PROFILE` or env vars):

```bash
aws sts get-caller-identity
```

Set your Grok/xAI API key (in `.env` or shell):

```bash
export XAI_API_KEY=...
```

## 1) Fetch latest 50 PolitiFact raw fact checks into storage

By default this uses `https://www.politifact.com/factchecks/list/`.

You can also pass a source explicitly with `--feed-url` (RSS or list page URL).

```bash
python scripts/ingest_politifact_raw.py --limit 50
```

Idempotency behavior:

- Each article is stored as `<prefix>/raw/<slug>.json` in selected backend
- If that key already exists, it is skipped.
- Use `--force` to overwrite existing keys.

Storage behavior:

- If `S3_BUCKET` is set, backend is S3 (default `STORAGE_BACKEND=auto`).
- If `S3_BUCKET` is not set, it falls back to local files in `LOCAL_DATA_DIR`.

## 2) Ask Grok to fact-check each claim

```bash
python scripts/factcheck_with_grok.py
```

Supported text models in script:

- `grok-4.20-0309-reasoning`
- `grok-4.20-0309-non-reasoning`
- `grok-4.20-multi-agent-0309`
- `grok-4-1-fast-reasoning`
- `grok-4-1-fast-non-reasoning`

The Grok script requests a structured JSON response and stores it in `grok_structured` with:

- `verdict`
- `confidence` (0-100)
- `evidence_summary`
- `caveats`

Idempotency behavior:

- Grok output is stored as `<prefix>/grok/<slug>.json` in selected backend
- Existing output keys are skipped unless `--force` is set.

## 3) Run side-by-side compare UI

```bash
python ui/app.py --port 5000
```

Open:

```text
http://127.0.0.1:5000
```

The UI loads paired `raw/` and `grok/` records and renders:

- claim/title
- PolitiFact raw fact-check text and verdict
- Grok fact-check response
- source link and timestamps

## Deploy on Render

Use these settings for a Python web service:

- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn ui.wsgi:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120`

Required env vars on Render (S3 mode):

- `STORAGE_BACKEND=s3`
- `S3_BUCKET=<your-bucket>`
- `S3_PREFIX=politifact`
- `AWS_REGION=<your-region>`
- `AWS_ACCESS_KEY_ID=<key>`
- `AWS_SECRET_ACCESS_KEY=<secret>`
