import os
import sys

from scripts.ingest_politifact_raw import main


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
        "--limit",
        str(event.get("limit", 50)),
    ]

    feed_url = event.get("feed_url")
    if feed_url:
        args.extend(["--feed-url", str(feed_url)])

    if event.get("force"):
        args.append("--force")

    if event.get("region"):
        args.extend(["--region", str(event["region"])])

    if event.get("sleep_seconds") is not None:
        args.extend(["--sleep-seconds", str(event["sleep_seconds"])])

    sys.argv = args
    main()

    return {
        "ok": True,
        "action": "ingest_politifact_raw",
        "bucket": os.environ["S3_BUCKET"],
        "prefix": os.getenv("S3_PREFIX", "politifact"),
        "limit": int(event.get("limit", 50)),
    }
