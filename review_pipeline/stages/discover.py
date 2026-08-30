import argparse
import json
import os
from datetime import date
from urllib.parse import urlsplit, urlunsplit

import certifi
import requests
from dotenv import load_dotenv

from review_pipeline.config import (
    DATAFORSEO_ENDPOINT,
    PRODUCT,
    PROJECT_ROOT,
    SEARCH_QUERIES,
    SERP_RAW_PATH,
    SOURCES_PATH,
    ensure_data_directories,
)


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def classify_source(domain: str, url: str) -> str:
    value = f"{domain} {url}".lower()
    if "arzopa.com" in value:
        return "official"
    if any(name in value for name in ("amazon.", "walmart.", "bestbuy.", "ebay.")):
        return "retailer"
    if "youtube.com" in value:
        return "video"
    if "reddit.com" in value or "forum" in value:
        return "community"
    return "independent_candidate"


def load_auth() -> str:
    load_dotenv(PROJECT_ROOT / ".env")
    auth = os.getenv("DATAFORSEO_AUTH_BASE64")
    if not auth:
        raise SystemExit("Missing DATAFORSEO_AUTH_BASE64 in .env")
    return auth


def search_task(query: str) -> dict:
    return {
        "keyword": query,
        "location_code": 2840,
        "language_code": "en",
        "device": "desktop",
        "depth": 10,
    }


def load_cached_tasks() -> dict[str, dict]:
    if not SERP_RAW_PATH.exists():
        return {}
    data = json.loads(SERP_RAW_PATH.read_text(encoding="utf-8"))
    return {
        task["data"]["keyword"]: task
        for task in data.get("tasks", [])
        if task.get("status_code") == 20000 and task.get("data", {}).get("keyword")
    }


def fetch_live_task(query: str, auth: str) -> dict:
    response = requests.post(
        DATAFORSEO_ENDPOINT,
        json=[search_task(query)],
        headers={"Authorization": f"Basic {auth}"},
        timeout=90,
        verify=certifi.where(),
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status_code") != 20000:
        raise RuntimeError(payload.get("status_message", "Unknown DataForSEO error"))
    task = (payload.get("tasks") or [{}])[0]
    if task.get("status_code") != 20000:
        raise RuntimeError(task.get("status_message", f"Search failed for {query}"))
    return task


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover Z3FC sources with live Google SERPs")
    parser.add_argument("--refresh", action="store_true")
    return parser.parse_args()


def collect_tasks(auth: str, refresh: bool) -> tuple[list[dict], float]:
    cached = {} if refresh else load_cached_tasks()
    tasks = []
    new_cost = 0.0
    for query in SEARCH_QUERIES:
        task = cached.get(query) or fetch_live_task(query, auth)
        tasks.append(task)
        if query not in cached:
            new_cost += task.get("cost") or 0
    return tasks, new_cost


def organic_items(task: dict):
    for result in task.get("result") or []:
        for item in result.get("items") or []:
            if item.get("type") == "organic" and item.get("url"):
                yield item


def add_source(sources: dict, query: str, item: dict) -> None:
    url = canonical_url(item["url"])
    source = sources.setdefault(
        url,
        {
            "url": url,
            "domain": item.get("domain"),
            "title": item.get("title"),
            "description": item.get("description"),
            "source_type": classify_source(item.get("domain") or "", url),
            "queries": [],
            "best_rank": item.get("rank_group"),
        },
    )
    if query not in source["queries"]:
        source["queries"].append(query)
    rank = item.get("rank_group")
    if rank and (not source["best_rank"] or rank < source["best_rank"]):
        source["best_rank"] = rank


def normalized_sources(tasks: list[dict]) -> list[dict]:
    sources = {}
    for task in tasks:
        query = task["data"]["keyword"]
        for item in organic_items(task):
            add_source(sources, query, item)
    return sorted(
        sources.values(),
        key=lambda source: (
            0 if source["source_type"] == "official" else 1,
            source["best_rank"] or 999,
        ),
    )


def task_summary(task: dict) -> dict:
    return {
        "query": task.get("data", {}).get("keyword"),
        "status_code": task.get("status_code"),
        "status_message": task.get("status_message"),
        "cost": task.get("cost"),
    }


def save_results(tasks: list[dict], sources: list[dict]) -> None:
    SERP_RAW_PATH.write_text(
        json.dumps({"status_code": 20000, "tasks": tasks}, indent=2),
        encoding="utf-8",
    )
    output = {
        "product": PRODUCT,
        "collection": {
            "provider": "DataForSEO Google Organic Live Advanced",
            "location": "United States",
            "language": "English",
            "collected_at": date.today().isoformat(),
            "queries": SEARCH_QUERIES,
            "tasks": [task_summary(task) for task in tasks],
        },
        "sources": sources,
    }
    SOURCES_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")


def main() -> None:
    args = parse_args()
    ensure_data_directories()
    try:
        tasks, new_cost = collect_tasks(load_auth(), args.refresh)
    except requests.RequestException as error:
        raise SystemExit(f"DataForSEO request failed: {error}") from error
    except RuntimeError as error:
        raise SystemExit(f"DataForSEO error: {error}") from error
    sources = normalized_sources(tasks)
    save_results(tasks, sources)
    total_cost = sum(task.get("cost") or 0 for task in tasks)
    print(f"Saved {len(sources)} unique sources to {SOURCES_PATH}")
    print(f"New DataForSEO cost: ${new_cost:.4f}; saved-result cost: ${total_cost:.4f}")


if __name__ == "__main__":
    main()
