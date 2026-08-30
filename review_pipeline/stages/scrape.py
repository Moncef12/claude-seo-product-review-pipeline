import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import certifi
import requests
from bs4 import BeautifulSoup

from review_pipeline.config import (
    REVIEW_MANIFEST_PATH,
    REVIEW_SOURCES,
    SCRAPED_REVIEWS_DIR,
    ensure_data_directories,
)


REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape and cache five Z3FC reviews")
    parser.add_argument("--refresh", action="store_true")
    return parser.parse_args()


def review_cache_path(publisher: str, url: str) -> Path:
    name = re.sub(r"[^a-z0-9]+", "-", publisher.lower()).strip("-")
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return SCRAPED_REVIEWS_DIR / f"{name}-{digest}.json"


def fetch_page(url: str) -> requests.Response:
    response = requests.get(
        url,
        headers=REQUEST_HEADERS,
        timeout=45,
        verify=certifi.where(),
    )
    response.raise_for_status()
    return response


def clean_article(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    remove_page_chrome(soup)
    container = soup.find("article") or soup.find("main") or soup.body
    return title, normalize_text(container)


def remove_page_chrome(soup: BeautifulSoup) -> None:
    selector = "script, style, noscript, svg, nav, header, footer, form, aside, iframe"
    for tag in soup.select(selector):
        tag.decompose()


def normalize_text(container) -> str:
    if not container:
        return ""
    lines = []
    for raw_line in container.get_text("\n", strip=True).splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n".join(lines)


def build_record(source: dict, response: requests.Response) -> dict:
    title, text = clean_article(response.text)
    if len(text) < 800:
        raise ValueError(f"extracted only {len(text)} characters")
    return {
        "publisher": source["publisher"],
        "url": source["url"],
        "final_url": response.url,
        "title": title,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "word_count": len(text.split()),
        "contains_exact_model": "z3fc" in text.lower(),
        "text": text,
    }


def load_or_fetch(source: dict, refresh: bool) -> dict | None:
    path = review_cache_path(source["publisher"], source["url"])
    if path.exists() and not refresh:
        record = json.loads(path.read_text(encoding="utf-8"))
        print(f"CACHED {source['publisher']}: {record['word_count']} words")
        return record
    try:
        record = build_record(source, fetch_page(source["url"]))
    except (requests.RequestException, ValueError) as error:
        print(f"FAILED {source['publisher']}: {error}")
        return None
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"FETCHED {source['publisher']}: {record['word_count']} words")
    return record


def manifest_entry(record: dict) -> dict:
    return {key: value for key, value in record.items() if key != "text"}


def main() -> None:
    args = parse_args()
    ensure_data_directories()
    records = [load_or_fetch(source, args.refresh) for source in REVIEW_SOURCES]
    manifest = [manifest_entry(record) for record in records if record]
    REVIEW_MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Saved {len(manifest)} cached reviews to {REVIEW_MANIFEST_PATH}")


if __name__ == "__main__":
    main()
