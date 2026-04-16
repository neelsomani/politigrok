import os
import sys

from scripts.factcheck_with_grok import main


def handler(event, context):
    event = event or {}

    args = [
        "prog",
        "--backend",
        "s3",
        "--bucket",
        os.environ["S3_BUCKET"],
        "--prefix",
        os.getenv("S3_PREFIX", "politifact"),
        "--model",
        os.getenv("XAI_MODEL", "grok-4-1-fast-reasoning"),
        "--max-items",
        str(event.get("max_items", 0)),
    ]

    if event.get("force"):
        args.append("--force")

    if event.get("region"):
        args.extend(["--region", str(event["region"])])

    if event.get("sleep_seconds") is not None:
        args.extend(["--sleep-seconds", str(event["sleep_seconds"])])

    if event.get("model"):
        args.extend(["--model", str(event["model"])])

    if event.get("api_base_url"):
        args.extend(["--api-base-url", str(event["api_base_url"])])

    sys.argv = args
    main()

    return {
        "ok": True,
        "action": "factcheck_with_grok",
        "bucket": os.environ["S3_BUCKET"],
        "prefix": os.getenv("S3_PREFIX", "politifact"),
        "model": event.get("model") or os.getenv("XAI_MODEL", "grok-4-1-fast-reasoning"),
    }
