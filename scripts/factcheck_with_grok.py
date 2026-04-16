#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.append(str(Path(__file__).resolve().parent.parent))
from storage_backend import StorageBackend, choose_mode


REAL_GROK_TEXT_MODELS = [
    "grok-4.20-0309-reasoning",
    "grok-4.20-0309-non-reasoning",
    "grok-4.20-multi-agent-0309",
    "grok-4-1-fast-reasoning",
    "grok-4-1-fast-non-reasoning",
]


def parse_args() -> argparse.Namespace:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Read PolitiFact claims from storage and ask Grok to fact-check each claim."
    )
    parser.add_argument("--bucket", default=os.getenv("S3_BUCKET"), help="S3 bucket name")
    parser.add_argument(
        "--prefix",
        default=os.getenv("S3_PREFIX", "politifact"),
        help="S3 key prefix containing raw and output folders",
    )
    parser.add_argument(
        "--region",
        default=os.getenv("AWS_REGION"),
        help="AWS region override",
    )
    parser.add_argument(
        "--backend",
        choices=["auto", "s3", "local"],
        default=os.getenv("STORAGE_BACKEND", "auto"),
        help="Storage backend. auto uses S3 when S3_BUCKET is set, else local.",
    )
    parser.add_argument(
        "--local-dir",
        default=os.getenv("LOCAL_DATA_DIR", "data"),
        help="Local base directory for fallback storage",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("XAI_MODEL", "grok-4-1-fast-reasoning"),
        choices=REAL_GROK_TEXT_MODELS,
        help="Grok text model name",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=0,
        help="Process only first N raw claims (0 = all)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing Grok responses",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.25,
        help="Delay between Grok API calls",
    )
    parser.add_argument(
        "--api-base-url",
        default=os.getenv("XAI_API_BASE_URL", "https://api.x.ai/v1"),
        help="xAI API base URL",
    )
    return parser.parse_args()


def ask_grok(api_key: str, api_base_url: str, model: str, claim: str) -> dict:
    prompt = (
        "Fact-check the claim below. Return ONLY valid JSON with this exact schema: "
        '{"verdict":"True|Mostly True|Half True|Mostly False|False|Unproven|Pants On Fire|Barely True",'
        '"confidence":0,"evidence_summary":"...","caveats":"..."}. '
        "Do not include markdown or extra text.\n\n"
        f"Claim: {claim}"
    )

    response = requests.post(
        f"{api_base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a meticulous political fact-checking assistant.",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            "temperature": 0.2,
        },
        timeout=90,
    )
    response.raise_for_status()
    payload = response.json()

    return {
        "raw_response": payload,
        "content": payload["choices"][0]["message"]["content"],
    }


def parse_structured_factcheck(content: str) -> dict | None:
    raw = (content or "").strip()
    if not raw:
        return None

    candidates = [raw]
    fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL | re.IGNORECASE)
    if fenced_match:
        candidates.insert(0, fenced_match.group(1).strip())

    brace_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if brace_match:
        candidates.append(brace_match.group(0).strip())

    parsed = None
    for candidate in candidates:
        try:
            maybe = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(maybe, dict):
            parsed = maybe
            break

    if not parsed:
        return None

    verdict = (parsed.get("verdict") or "").strip()
    confidence = parsed.get("confidence")
    evidence_summary = (parsed.get("evidence_summary") or "").strip()
    caveats = (parsed.get("caveats") or "").strip()

    if isinstance(confidence, str):
        numeric = re.search(r"\d+", confidence)
        confidence = int(numeric.group(0)) if numeric else None
    elif isinstance(confidence, (int, float)):
        confidence = int(confidence)
    else:
        confidence = None

    if confidence is not None:
        confidence = max(0, min(100, confidence))

    return {
        "verdict": verdict or None,
        "confidence": confidence,
        "evidence_summary": evidence_summary or None,
        "caveats": caveats or None,
    }


def main() -> None:
    args = parse_args()
    api_key = os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY")
    if not api_key:
        raise RuntimeError("Set XAI_API_KEY (or GROK_API_KEY) before running")

    resolved_mode = choose_mode(args.backend, args.bucket)
    if resolved_mode == "s3" and not args.bucket:
        raise RuntimeError("S3 mode requires --bucket or S3_BUCKET in environment")

    storage = StorageBackend(
        mode=resolved_mode,
        bucket=args.bucket,
        region=args.region,
        local_dir=args.local_dir,
    )

    raw_keys = storage.list_json_keys(f"{args.prefix}/raw/")
    if args.max_items > 0:
        raw_keys = raw_keys[: args.max_items]

    print(f"Found {len(raw_keys)} raw fact-check files (backend={resolved_mode})")

    processed = 0
    skipped = 0
    failed = 0

    for idx, raw_key in enumerate(raw_keys, start=1):
        slug = raw_key.split("/")[-1]
        output_key = f"{args.prefix}/grok/{slug}"

        if not args.force and storage.exists(output_key):
            skipped += 1
            print(f"[{idx}/{len(raw_keys)}] Skip existing: {output_key}")
            continue

        try:
            source = storage.get_json(raw_key)
            claim = (source.get("claim") or source.get("title") or "").strip()
            if not claim:
                raise ValueError("Missing claim/title in source payload")

            grok_response = ask_grok(
                api_key=api_key,
                api_base_url=args.api_base_url,
                model=args.model,
                claim=claim,
            )

            output_payload = {
                "source_raw_key": raw_key,
                "source_url": source.get("url"),
                "claim": claim,
                "model": args.model,
                "grok_fact_check": grok_response["content"],
                "grok_structured": parse_structured_factcheck(grok_response["content"]),
                "grok_raw_response": grok_response["raw_response"],
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }

            storage.put_json(output_key, output_payload)
            processed += 1
            print(f"[{idx}/{len(raw_keys)}] Stored: {output_key}")
        except Exception as exc:
            failed += 1
            print(f"[{idx}/{len(raw_keys)}] Failed {raw_key}: {exc}")

        time.sleep(max(args.sleep_seconds, 0))

    print(
        "Done. "
        f"processed={processed}, skipped={skipped}, failed={failed}, total={len(raw_keys)}"
    )


if __name__ == "__main__":
    main()
