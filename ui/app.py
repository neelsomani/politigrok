#!/usr/bin/env python3
import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

sys.path.append(str(Path(__file__).resolve().parent.parent))
from storage_backend import StorageBackend, choose_mode


def create_app(
    bucket: str,
    prefix: str,
    region: str = None,
    backend: str = "auto",
    local_dir: str = "data",
) -> Flask:
    app = Flask(__name__)
    resolved_mode = choose_mode(backend, bucket)
    if resolved_mode == "s3" and not bucket:
        raise RuntimeError("S3 mode requires --bucket or S3_BUCKET in environment")

    storage = StorageBackend(
        mode=resolved_mode,
        bucket=bucket,
        region=region,
        local_dir=local_dir,
    )

    def extract_grok_verdict(text: str | None) -> str | None:
        if not text:
            return None

        known_verdicts = [
            "pants on fire",
            "mostly false",
            "barely true",
            "half true",
            "mostly true",
            "unproven",
            "false",
            "true",
        ]

        def normalize(value: str) -> str | None:
            cleaned = value.strip().strip("*`_-: ")
            if not cleaned:
                return None

            lowered = cleaned.lower()
            for verdict in known_verdicts:
                if re.search(rf"\b{re.escape(verdict)}\b", lowered):
                    return verdict.title()

            return None

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            cleaned = line.lstrip("-*# ").strip()
            lowered = cleaned.lower()

            if lowered.startswith("verdict") or lowered.startswith("rating"):
                if ":" in cleaned:
                    value = cleaned.split(":", 1)[1].strip()
                elif lowered.startswith("rating"):
                    value = cleaned[len("rating") :].strip()
                else:
                    value = cleaned[len("verdict") :].strip()
                normalized = normalize(value)
                if normalized:
                    return normalized

                if index + 1 < len(lines):
                    next_line = lines[index + 1].lstrip("-*# ").strip()
                    normalized = normalize(next_line)
                    if normalized:
                        return normalized

            if re.match(r"^\d+[\).\-]\s*verdict", lowered):
                value = re.sub(r"^\d+[\).\-]\s*verdict\s*:?", "", cleaned, flags=re.IGNORECASE).strip()
                normalized = normalize(value)
                if normalized:
                    return normalized
                if index + 1 < len(lines):
                    normalized = normalize(lines[index + 1])
                    if normalized:
                        return normalized

        for line in lines[:10]:
            normalized = normalize(line)
            if normalized:
                return normalized

        return None

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/fact-checks")
    def fact_checks():
        page = max(int(request.args.get("page", 1)), 1)
        page_size = min(max(int(request.args.get("page_size", 20)), 1), 100)
        query = (request.args.get("q", "") or "").strip().lower()
        slug_filter = (request.args.get("slug", "") or "").strip()

        raw_keys = storage.list_json_keys(f"{prefix}/raw/")
        grok_keys = storage.list_json_keys(f"{prefix}/grok/")

        raw_by_slug = {key.split("/")[-1]: key for key in raw_keys}
        grok_by_slug = {key.split("/")[-1]: key for key in grok_keys}

        slugs = sorted(set(raw_by_slug.keys()) | set(grok_by_slug.keys()))
        results: list[dict[str, Any]] = []

        for slug in slugs:
            if slug_filter and slug != slug_filter:
                continue

            raw_payload = storage.get_json(raw_by_slug[slug]) if slug in raw_by_slug else {}
            grok_payload = storage.get_json(grok_by_slug[slug]) if slug in grok_by_slug else {}
            grok_fact_check = grok_payload.get("grok_fact_check")
            grok_structured = grok_payload.get("grok_structured") or {}
            grok_verdict = grok_structured.get("verdict") or extract_grok_verdict(grok_fact_check)
            grok_confidence = grok_structured.get("confidence")
            grok_evidence_summary = grok_structured.get("evidence_summary")
            grok_caveats = grok_structured.get("caveats")

            grok_display_parts = []
            if grok_evidence_summary:
                grok_display_parts.append(f"Evidence summary:\n{grok_evidence_summary}")
            if grok_caveats:
                grok_display_parts.append(f"Caveats:\n{grok_caveats}")

            if grok_display_parts:
                grok_display_text = "\n\n".join(grok_display_parts)
            else:
                grok_display_text = grok_fact_check

            results.append(
                {
                    "slug": slug,
                    "claim": raw_payload.get("claim") or grok_payload.get("claim"),
                    "title": raw_payload.get("title"),
                    "url": raw_payload.get("url") or grok_payload.get("source_url"),
                    "published": raw_payload.get("published"),
                    "politifact_verdict": raw_payload.get("politifact_verdict"),
                    "politifact_text": raw_payload.get("raw_fact_check_text"),
                    "grok_model": grok_payload.get("model"),
                    "grok_verdict": grok_verdict,
                    "grok_confidence": grok_confidence,
                    "grok_evidence_summary": grok_evidence_summary,
                    "grok_caveats": grok_caveats,
                    "grok_display_text": grok_display_text,
                    "grok_fact_check": grok_fact_check,
                    "grok_generated_at": grok_payload.get("generated_at"),
                }
            )

        if query:
            results = [
                item
                for item in results
                if query in (item.get("claim") or "").lower()
                or query in (item.get("title") or "").lower()
                or query in (item.get("politifact_text") or "").lower()
                or query in (item.get("grok_display_text") or "").lower()
                or query in (item.get("grok_fact_check") or "").lower()
                or query in (item.get("politifact_verdict") or "").lower()
                or query in (item.get("grok_verdict") or "").lower()
            ]

        results.sort(key=lambda item: item.get("published") or "", reverse=True)

        total = len(results)
        start = (page - 1) * page_size
        end = start + page_size
        page_items = results[start:end]

        return jsonify(
            {
                "items": page_items,
                "total": total,
                "page": page,
                "page_size": page_size,
                "has_more": end < total,
            }
        )

    return app


def parse_args() -> argparse.Namespace:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Run local side-by-side compare UI")
    parser.add_argument("--bucket", default=os.getenv("S3_BUCKET"), help="S3 bucket")
    parser.add_argument(
        "--prefix",
        default=os.getenv("S3_PREFIX", "politifact"),
        help="Storage key prefix",
    )
    parser.add_argument("--region", default=os.getenv("AWS_REGION"), help="AWS region")
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
    parser.add_argument("--host", default="127.0.0.1", help="Host")
    parser.add_argument("--port", default=5000, type=int, help="Port")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = create_app(
        bucket=args.bucket,
        prefix=args.prefix,
        region=args.region,
        backend=args.backend,
        local_dir=args.local_dir,
    )
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
