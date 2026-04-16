# Lambda Deploy (No AWS CLI)

This repo includes two Lambda handlers:

- `lambda_ingest.handler`
- `lambda_factcheck.handler`

## 1) Build zip files locally

From repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Build ingest package:

```bash
mkdir -p build_ingest
pip install -r requirements.txt -t build_ingest
cp -r scripts storage_backend.py lambda_ingest.py build_ingest/
cd build_ingest && zip -r ../ingest.zip . && cd ..
```

Build factcheck package:

```bash
mkdir -p build_factcheck
pip install -r requirements.txt -t build_factcheck
cp -r scripts storage_backend.py lambda_factcheck.py build_factcheck/
cd build_factcheck && zip -r ../factcheck.zip . && cd ..
```

## 2) Create Lambda functions in AWS Console

For each function:

- Runtime: `Python 3.12`
- Upload: `.zip file`
- Handler:
  - ingest function: `lambda_ingest.handler`
  - factcheck function: `lambda_factcheck.handler`
- Timeout: `15 minutes`
- Memory: start with `1024 MB`
- Execution role: role with S3 access + CloudWatch logs

## 3) Environment variables

### Ingest Lambda

- `S3_BUCKET=<your-bucket>`
- `S3_PREFIX=politifact` (or your prefix)

### Factcheck Lambda

- `S3_BUCKET=<your-bucket>`
- `S3_PREFIX=politifact` (or your prefix)
- `XAI_API_KEY=<your-key>`
- `XAI_MODEL=grok-4-1-fast-reasoning`
- `XAI_API_BASE_URL=https://api.x.ai/v1` (optional)

## 4) Test payloads

Ingest test payload:

```json
{
  "limit": 50
}
```

Factcheck test payload:

```json
{
  "max_items": 0
}
```

Optional payload keys for either handler:

- `force` (boolean)
- `region` (string)
- `sleep_seconds` (number)

Extra ingest payload key:

- `feed_url` (string)

Extra factcheck payload keys:

- `model` (string)
- `api_base_url` (string)

## 5) Schedule

Use EventBridge Scheduler:

1. Run ingest daily (or hourly).
2. Run factcheck after ingest (for example +10 minutes).

