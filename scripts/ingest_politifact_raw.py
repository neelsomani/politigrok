#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

sys.path.append(str(Path(__file__).resolve().parent.parent))
from storage_backend import StorageBackend, choose_mode


DEFAULT_FEED_URL = "https://www.politifact.com/factchecks/list/"


def parse_args() -> argparse.Namespace:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Fetch latest PolitiFact fact checks and store raw text in S3 or local files."
    )
    parser.add_argument("--bucket", default=os.getenv("S3_BUCKET"), help="S3 bucket name")
    parser.add_argument(
        "--prefix",
        default=os.getenv("S3_PREFIX", "politifact"),
        help="S3 key prefix (default: politifact)",
    )
    parser.add_argument(
        "--feed-url",
        default=DEFAULT_FEED_URL,
        help="PolitiFact RSS feed URL",
    )
    parser.add_argument(
        "--url",
        action="append",
        default=[],
        help="Specific PolitiFact article URL to ingest (can be used multiple times)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max number of latest articles to ingest",
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
        "--force",
        action="store_true",
        help="Overwrite existing stored objects",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.2,
        help="Delay between article fetches",
    )
    return parser.parse_args()


def get_rss_items(feed_url: str, limit: int) -> List[dict]:
    response = requests.get(feed_url, timeout=30)
    response.raise_for_status()

    root = ET.fromstring(response.content)
    items = []

    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        guid = (item.findtext("guid") or "").strip()

        if not link:
            continue

        items.append(
            {
                "title": title,
                "link": link,
                "pub_date": pub_date,
                "guid": guid,
            }
        )

        if len(items) >= limit:
            break

    return items


def get_list_page_items(list_url: str, limit: int) -> List[dict]:
    response = requests.get(list_url, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    seen = set()
    items = []
    factcheck_pattern = re.compile(r"/factchecks/\d{4}/")

    for anchor in soup.select("a[href*='/factchecks/']"):
        href = (anchor.get("href") or "").strip()
        if not href:
            continue

        full_url = urljoin(list_url, href)
        parsed = urlparse(full_url)
        path = parsed.path

        if "/factchecks/list/" in path:
            continue
        if not factcheck_pattern.search(path):
            continue
        if full_url in seen:
            continue

        seen.add(full_url)
        title = anchor.get_text(" ", strip=True)
        items.append(
            {
                "title": title,
                "link": full_url,
                "pub_date": "",
                "guid": full_url,
            }
        )

        if len(items) >= limit:
            break

    return items


def get_latest_items(source_url: str, limit: int) -> List[dict]:
    try:
        return get_rss_items(source_url, limit)
    except ET.ParseError:
        return get_list_page_items(source_url, limit)


def make_slug(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    slug = path.replace("/", "__") or "article"
    return slug


def get_article_text(soup: BeautifulSoup) -> str:
    json_ld_text = get_article_text_from_jsonld(soup)
    if json_ld_text:
        return json_ld_text

    body_node = soup.select_one(".m-textblock")
    if body_node:
        paragraph_nodes = body_node.select("p")
        list_nodes = body_node.select("li")

        text_chunks = [
            node.get_text(" ", strip=True)
            for node in [*paragraph_nodes, *list_nodes]
            if node.get_text(strip=True)
        ]
        text_chunks = filter_noise_chunks(text_chunks)
        text_chunks = dedupe_adjacent_chunks(text_chunks)
        if text_chunks:
            return "\n\n".join(text_chunks)

        fallback_text = body_node.get_text(" ", strip=True)
        if fallback_text:
            return fallback_text

    return ""


def filter_noise_chunks(chunks: List[str]) -> List[str]:
    lowered_noise_substrings = [
        "síguenos en whatsapp",
        "sigue nuestro canal en whatsapp",
        "mándanoslo por whatsapp",
        "if you’ve seen something",
        "if you see something",
    ]

    filtered = []
    for chunk in chunks:
        lowered = chunk.lower()
        if any(term in lowered for term in lowered_noise_substrings):
            continue
        filtered.append(chunk)

    return filtered


def get_article_text_from_jsonld(soup: BeautifulSoup) -> Optional[str]:
    for script in soup.select("script[type='application/ld+json']"):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue

        nodes = []
        if isinstance(payload, list):
            nodes.extend(payload)
        elif isinstance(payload, dict):
            if isinstance(payload.get("@graph"), list):
                nodes.extend(payload["@graph"])
            nodes.append(payload)

        for node in nodes:
            if not isinstance(node, dict):
                continue

            node_type = node.get("@type")
            node_types = node_type if isinstance(node_type, list) else [node_type]
            node_types = [value for value in node_types if isinstance(value, str)]

            if not any(value in {"Article", "NewsArticle", "ReportageNewsArticle"} for value in node_types):
                continue

            article_body = node.get("articleBody")
            if isinstance(article_body, str):
                text = article_body.strip()
                if text:
                    return text

    return None


def dedupe_adjacent_chunks(chunks: List[str]) -> List[str]:
    if not chunks:
        return chunks

    deduped = [chunks[0]]
    for chunk in chunks[1:]:
        if chunk != deduped[-1]:
            deduped.append(chunk)

    return deduped


def normalize_meter_slug(value: str) -> str:
    cleaned = value.replace("_", "-").strip("-/ ")
    if not cleaned:
        return ""

    aliases = {
        "pants-fire": "Pants On Fire",
    }
    if cleaned in aliases:
        return aliases[cleaned]

    return cleaned.replace("-", " ").title()


KNOWN_VERDICTS = {
    "True",
    "Mostly True",
    "Half True",
    "Mostly False",
    "False",
    "Pants On Fire",
    "Barely True",
}


def normalize_verdict_label(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    normalized = normalize_meter_slug(value)
    if normalized in KNOWN_VERDICTS:
        return normalized
    return None


def verdict_from_meter_src(src: str) -> Optional[str]:
    if not src:
        return None

    match = re.search(r"meter-([a-z-]+)(?:[/.]|$)", src, re.IGNORECASE)
    if not match:
        return None

    return normalize_verdict_label(match.group(1))


def extract_primary_claim_and_verdict(soup: BeautifulSoup) -> tuple[Optional[str], Optional[str]]:
    for statement in soup.select(".m-statement"):
        quote_node = statement.select_one(".m-statement__quote")
        quote_text = quote_node.get_text(" ", strip=True) if quote_node else ""
        if not quote_text:
            continue

        meter_images = statement.select(".m-statement__meter img[src*='meter-']")
        if not meter_images:
            continue

        preferred = []
        fallback = []
        for image in meter_images:
            src = (image.get("src") or "").strip()
            if "-th." in src:
                fallback.append(src)
            else:
                preferred.append(src)

        for src in [*preferred, *fallback]:
            verdict = verdict_from_meter_src(src)
            if verdict:
                return quote_text, verdict

        return quote_text, None

    return None, None


def extract_verdict_from_meter_image(soup: BeautifulSoup) -> Optional[str]:
    selector_order = [
        "article .m-statement__meter img[src*='meter-']",
        "article .m-statement__meta img[src*='meter-']",
        "article img[src*='meter-']",
    ]

    seen_sources = set()
    img_nodes = []
    for selector in selector_order:
        for img in soup.select(selector):
            src = (img.get("src") or "").strip()
            if src in seen_sources:
                continue
            seen_sources.add(src)
            img_nodes.append(img)

    for img in img_nodes:
        src = (img.get("src") or "").strip()
        alt = (img.get("alt") or "").strip()
        title_attr = (img.get("title") or "").strip()

        normalized = verdict_from_meter_src(src)
        if normalized:
            return normalized

        for candidate in (alt, title_attr):
            normalized = normalize_verdict_label(candidate)
            if normalized:
                return normalized

    return None


def first_text(soup: BeautifulSoup, selectors: List[str]) -> Optional[str]:
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            value = node.get_text(" ", strip=True)
            if value:
                return value
    return None


def fetch_article_payload(item: dict) -> dict:
    response = requests.get(item["link"], timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    claim, primary_verdict = extract_primary_claim_and_verdict(soup)
    if not claim:
        claim = first_text(
            soup,
            [
                ".m-statement__quote",
                "article blockquote",
                "h1",
            ],
        )
    verdict_text = first_text(
        soup,
        [
            "article .m-statement__meter .c-image__title",
            "article .m-statement__meta .m-statement__meter .c-image__title",
        ],
    )
    verdict = primary_verdict or normalize_verdict_label(verdict_text)
    if not verdict:
        verdict = extract_verdict_from_meter_image(soup)
    article_text = get_article_text(soup)

    return {
        "url": item["link"],
        "guid": item.get("guid"),
        "title": item.get("title"),
        "published": item.get("pub_date"),
        "claim": claim,
        "politifact_verdict": verdict,
        "raw_fact_check_text": article_text,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    args = parse_args()
    resolved_mode = choose_mode(args.backend, args.bucket)
    if resolved_mode == "s3" and not args.bucket:
        raise RuntimeError("S3 mode requires --bucket or S3_BUCKET in environment")

    storage = StorageBackend(
        mode=resolved_mode,
        bucket=args.bucket,
        region=args.region,
        local_dir=args.local_dir,
    )

    if args.url:
        items = [
            {
                "title": "",
                "link": url,
                "pub_date": "",
                "guid": url,
            }
            for url in args.url
        ]
    else:
        items = get_latest_items(args.feed_url, args.limit)
    print(f"Found {len(items)} feed items (backend={resolved_mode})")

    stored = 0
    skipped = 0
    failed = 0

    for idx, item in enumerate(items, start=1):
        slug = make_slug(item["link"])
        key = f"{args.prefix}/raw/{slug}.json"

        if not args.force and storage.exists(key):
            skipped += 1
            print(f"[{idx}/{len(items)}] Skip existing: {key}")
            continue

        try:
            payload = fetch_article_payload(item)
            if not payload["raw_fact_check_text"]:
                print(f"[{idx}/{len(items)}] Warning: empty text for {item['link']}")

            storage.put_json(key, payload)
            stored += 1
            print(f"[{idx}/{len(items)}] Stored: {key}")
        except Exception as exc:
            failed += 1
            print(f"[{idx}/{len(items)}] Failed {item['link']}: {exc}")

        time.sleep(max(args.sleep_seconds, 0))

    print(
        "Done. "
        f"stored={stored}, skipped={skipped}, failed={failed}, total={len(items)}"
    )


if __name__ == "__main__":
    main()
