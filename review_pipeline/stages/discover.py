"""Discover review candidates and SERP intent signals.

Discovery is deliberately the only stage that knows about DataForSEO. It keeps
provider responses intact, writes a human-auditable source index, and performs
no publisher selection. Scraping and qualification consume this index later.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

import certifi
import requests
from dotenv import load_dotenv

from review_pipeline.config import (
    AUTHORITY_CACHE_TTL_DAYS,
    AUTHORITY_DB_PATH,
    AUTHORITY_RAW_PATH,
    DATAFORSEO_BULK_RANKS_ENDPOINT,
    DATAFORSEO_ENDPOINT,
    DISCOVERY_DEPTH,
    FALLBACK_SERP_QUERY,
    MIN_INDEPENDENT_CANDIDATES,
    PRIMARY_SERP_QUERY,
    PRODUCT,
    PROJECT_ROOT,
    SERP_RAW_PATH,
    SOURCES_PATH,
    ensure_data_directories,
)


SERP_FEATURE_TYPES = {
    "commercial": {
        "shopping",
        "product",
        "product_info",
        "reviews",
        "top_stories",
        "local_pack",
        "paid",
        "ads",
    },
    "paa": {"people_also_ask", "people_also_ask_question"},
    "related": {"related_searches", "related_search"},
}


def canonical_url(url: str) -> str:
    """Return a stable URL suitable for candidate de-duplication."""

    parts = urlsplit(str(url or "").strip())
    if not parts.scheme or not parts.netloc:
        return str(url or "").strip()
    path = parts.path.rstrip("/") or "/"
    query_parts = []
    for part in parts.query.split("&") if parts.query else []:
        key = part.split("=", 1)[0].casefold()
        if key.startswith(("utm_", "gclid", "fbclid", "msclkid")):
            continue
        query_parts.append(part)
    return urlunsplit(
        (parts.scheme.casefold(), parts.netloc.casefold(), path, "&".join(query_parts), "")
    )


def root_domain(value: str) -> str:
    """Return a practical registrable-domain approximation without a dependency."""

    host = urlsplit(value if "://" in value else f"https://{value}").hostname or value
    host = host.casefold().strip(".")
    if host.startswith("www."):
        host = host[4:]
    labels = [label for label in host.split(".") if label]
    if len(labels) <= 2:
        return ".".join(labels)
    if labels[-1] in {"uk", "au", "nz", "za"} and labels[-2] in {"co", "com", "org", "net"}:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def classify_source(domain: str, url: str) -> str:
    """Classify a page before fetching it.

    Facebook, Reddit, forums, and social/video pages can be useful context but
    never count as independent editorial reviews.
    """

    value = f"{domain} {url}".casefold()
    host = root_domain(domain or url)
    if host == "arzopa.com":
        return "official"
    if any(name in value for name in ("amazon.", "walmart.", "bestbuy.", "ebay.", "newegg.", "target.", "aliexpress.")):
        return "retailer"
    if any(name in value for name in ("youtube.", "youtu.be", "tiktok.", "instagram.", "vimeo.")):
        return "video"
    if any(name in value for name in ("reddit.", "facebook.", "groups.", "forum", "forums", "quora.", "discord.", "community.", "social.")):
        return "community"
    return "independent_candidate"


def is_independent_candidate(source: dict[str, Any] | str) -> bool:
    source_type = source.get("source_type") if isinstance(source, dict) else source
    return source_type == "independent_candidate"


def load_auth() -> str:
    load_dotenv(PROJECT_ROOT / ".env")
    auth = os.getenv("DATAFORSEO_AUTH_BASE64")
    if not auth:
        raise SystemExit("Missing DATAFORSEO_AUTH_BASE64 in .env")
    return auth


def search_task(query: str) -> dict[str, Any]:
    return {
        "keyword": query,
        "location_code": 2840,
        "language_code": "en",
        "device": "desktop",
        "depth": DISCOVERY_DEPTH,
    }


def load_cached_tasks() -> dict[str, dict]:
    if not SERP_RAW_PATH.exists():
        return {}
    try:
        data = json.loads(SERP_RAW_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    output: dict[str, dict] = {}
    for task in data.get("tasks", []) if isinstance(data, dict) else []:
        query = task.get("data", {}).get("keyword")
        if task.get("status_code") == 20000 and query:
            cached = dict(task)
            cached["_cached"] = True
            output[str(query)] = cached
    return output


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


def _items(task: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for result in task.get("result") or []:
        for item in result.get("items") or []:
            if isinstance(item, dict):
                yield item


def _signal_text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        for key in ("question", "keyword", "title", "text", "name"):
            if value.get(key):
                return _signal_text(value[key])
        return []
    if isinstance(value, list):
        output: list[str] = []
        for item in value:
            output.extend(_signal_text(item))
        return output
    return []


def serp_signals(tasks: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Extract intent signals while retaining the exact response separately."""

    by_query: dict[str, dict[str, Any]] = {}
    for task in tasks:
        query = str(task.get("data", {}).get("keyword") or "")
        if not query:
            continue
        organic: list[dict[str, Any]] = []
        paa: list[str] = []
        related: list[str] = []
        commercial: list[str] = []
        ai_overview = False
        for item in _items(task):
            item_type = str(item.get("type") or "").casefold()
            if item_type == "organic" and item.get("url"):
                organic.append(
                    {
                        "rank": item.get("rank_group") or item.get("rank_absolute"),
                        "title": item.get("title"),
                        "description": item.get("description"),
                        "url": canonical_url(item.get("url")),
                        "domain": item.get("domain"),
                    }
                )
            if "ai_overview" in item_type or item_type in {"ai_overview", "answer_box"}:
                ai_overview = True
            if item_type in SERP_FEATURE_TYPES["paa"] or "people_also_ask" in item_type:
                paa.extend(_signal_text(item.get("items") or item.get("questions") or item.get("question")))
            if item_type in SERP_FEATURE_TYPES["related"] or "related_search" in item_type:
                related.extend(_signal_text(item.get("items") or item.get("queries") or item.get("keyword")))
            if item_type in SERP_FEATURE_TYPES["commercial"] or any(token in item_type for token in ("shopping", "product", "commercial", "merchant")):
                commercial.append(item_type)
        for result in task.get("result") or []:
            if not isinstance(result, dict):
                continue
            paa.extend(_signal_text(result.get("people_also_ask")))
            related.extend(_signal_text(result.get("related_searches")))
            if result.get("ai_overview") or result.get("ai_overview_items"):
                ai_overview = True
            features = result.get("features") or result.get("serp_features") or []
            if isinstance(features, list):
                commercial.extend(
                    str(feature.get("type") if isinstance(feature, dict) else feature)
                    for feature in features
                    if any(token in str(feature).casefold() for token in ("shopping", "product", "review", "merchant"))
                )
        by_query[query] = {
            "organic": organic,
            "people_also_ask": sorted(set(filter(None, paa))),
            "related_searches": sorted(set(filter(None, related))),
            "ai_overview_present": bool(ai_overview),
            "commercial_serp_features": sorted(set(filter(None, commercial))),
        }
    return by_query


def _independent_count(tasks: Iterable[dict[str, Any]]) -> int:
    urls: set[str] = set()
    for task in tasks:
        for item in _items(task):
            if item.get("type") != "organic" or not item.get("url"):
                continue
            url = canonical_url(item["url"])
            if classify_source(item.get("domain") or "", url) == "independent_candidate":
                urls.add(url)
    return len(urls)


def should_run_fallback(
    primary_tasks: Iterable[dict[str, Any]],
    signals: dict[str, Any] | None = None,
    minimum_candidates: int = MIN_INDEPENDENT_CANDIDATES,
) -> bool:
    """Return whether pros/cons intent discovery is needed."""

    tasks = list(primary_tasks)
    if _independent_count(tasks) < minimum_candidates:
        return True
    signals = signals if signals is not None else serp_signals(tasks)
    if any(key in signals for key in ("people_also_ask", "related_searches")):
        return not bool(signals.get("people_also_ask") or signals.get("related_searches"))
    return not any(
        signal.get("people_also_ask") or signal.get("related_searches")
        for signal in signals.values()
    )


fallback_needed = should_run_fallback
needs_fallback_query = should_run_fallback


def collect_tasks(auth: str, refresh: bool) -> tuple[list[dict], float]:
    """Collect the primary query and conditionally the fallback query."""

    cached = {} if refresh else load_cached_tasks()
    primary = cached.get(PRIMARY_SERP_QUERY)
    primary_was_cached = primary is not None
    primary = dict(primary or fetch_live_task(PRIMARY_SERP_QUERY, auth))
    primary["_cached"] = primary_was_cached
    tasks = [primary]
    new_cost = 0.0 if primary_was_cached else float(primary.get("cost") or 0)
    if should_run_fallback(tasks):
        fallback = cached.get(FALLBACK_SERP_QUERY)
        fallback_was_cached = fallback is not None
        fallback = dict(fallback or fetch_live_task(FALLBACK_SERP_QUERY, auth))
        fallback["_cached"] = fallback_was_cached
        tasks.append(fallback)
        if not fallback_was_cached:
            new_cost += float(fallback.get("cost") or 0)
    return tasks, new_cost


def add_source(sources: dict[str, dict], query: str, item: dict) -> None:
    url = canonical_url(item.get("url", ""))
    if not url:
        return
    source = sources.setdefault(
        url,
        {
            "url": url,
            "domain": item.get("domain") or urlsplit(url).netloc,
            "root_domain": root_domain(item.get("domain") or url),
            "title": item.get("title") or "",
            "description": item.get("description") or "",
            "source_type": classify_source(item.get("domain") or "", url),
            "queries": [],
            "best_rank": item.get("rank_group") or item.get("rank_absolute"),
        },
    )
    if query not in source["queries"]:
        source["queries"].append(query)
    rank = item.get("rank_group") or item.get("rank_absolute")
    if rank and (not source.get("best_rank") or rank < source["best_rank"]):
        source["best_rank"] = rank
    source["title"] = source.get("title") or item.get("title") or ""
    source["description"] = source.get("description") or item.get("description") or ""


def normalized_sources(tasks: list[dict], authority: dict[str, float] | None = None) -> list[dict]:
    sources: dict[str, dict] = {}
    for task in tasks:
        query = task.get("data", {}).get("keyword")
        if not query:
            continue
        for item in _items(task):
            if item.get("type") == "organic" and item.get("url"):
                add_source(sources, str(query), item)
    output = list(sources.values())
    for source in output:
        source["authority_rank"] = (authority or {}).get(source.get("root_domain"))
    return sorted(
        output,
        key=lambda source: (
            0 if source["source_type"] == "official" else 1,
            source.get("best_rank") or 999,
            source.get("root_domain") or "",
            source["url"],
        ),
    )


def task_summary(task: dict) -> dict:
    return {
        "query": task.get("data", {}).get("keyword"),
        "status_code": task.get("status_code"),
        "status_message": task.get("status_message"),
        "cost": task.get("cost"),
        "cached": bool(task.get("_cached")),
    }


def authority_targets(sources: Iterable[dict]) -> list[str]:
    targets = set()
    for source in sources:
        value = str(source.get("root_domain") or source.get("domain") or source.get("url") or "")
        domain = root_domain(value)
        if domain:
            targets.add(domain)
    return sorted(targets)


def _empty_authority_database() -> dict[str, Any]:
    return {
        "version": 1,
        "ttl_days": AUTHORITY_CACHE_TTL_DAYS,
        "entries": {},
    }


def _entry_score(entry: Any) -> float | None:
    if not isinstance(entry, dict):
        return None
    value = entry.get("score")
    if value is None:
        value = entry.get("authority_rank")
    if value is None:
        value = entry.get("authority")
    score = _first_number(value)
    if score is None:
        return None
    return round(_authority_score(score), 2)


def _normalise_authority_database(payload: Any) -> dict[str, Any]:
    """Return a safe, compact database shape and discard malformed entries."""

    database = _empty_authority_database()
    if not isinstance(payload, dict):
        return database
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, dict):
        raw_entries = payload.get("domains")
    if not isinstance(raw_entries, dict):
        # Accept a simple domain-to-entry mapping for forward/backward
        # compatibility, while ignoring database metadata keys.
        raw_entries = {
            key: value
            for key, value in payload.items()
            if isinstance(value, dict) and key not in {"provenance", "metadata"}
        }
    for raw_domain, raw_entry in raw_entries.items():
        domain = root_domain(str(raw_domain))
        if not domain or not isinstance(raw_entry, dict):
            continue
        score = _entry_score(raw_entry)
        fetched_at = str(raw_entry.get("fetched_at") or "").strip()
        if score is None or not fetched_at:
            continue
        endpoint = str(raw_entry.get("endpoint") or DATAFORSEO_BULK_RANKS_ENDPOINT)
        rank_scale = str(raw_entry.get("rank_scale") or "one_hundred")
        raw_provenance = raw_entry.get("provenance")
        provenance = {
            key: raw_provenance[key]
            for key in ("provider", "endpoint", "rank_scale", "target", "method")
            if isinstance(raw_provenance, dict) and raw_provenance.get(key) is not None
        }
        provenance.setdefault("provider", "DataForSEO")
        provenance.setdefault("endpoint", endpoint)
        provenance.setdefault("rank_scale", rank_scale)
        provenance.setdefault("target", domain)
        database["entries"][domain] = {
            "score": score,
            "authority_rank": score,
            "fetched_at": fetched_at,
            "endpoint": endpoint,
            "rank_scale": rank_scale,
            "provenance": provenance,
        }
    return database


def _read_authority_database(path: Path | None = None) -> tuple[dict[str, Any], str]:
    database_path = Path(path or AUTHORITY_DB_PATH)
    if not database_path.exists():
        return _empty_authority_database(), "missing"
    try:
        payload = json.loads(database_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _empty_authority_database(), "malformed"
    if not isinstance(payload, dict):
        return _empty_authority_database(), "malformed"
    database = _normalise_authority_database(payload)
    return database, "loaded"


def load_authority_database(path: Path | None = None) -> dict[str, Any]:
    """Load the shared authority database, ignoring malformed content safely."""

    return _read_authority_database(path)[0]


load_authority_db = load_authority_database
load_authority_cache = load_authority_database


def save_authority_database(database: dict[str, Any], path: Path | None = None) -> None:
    database_path = Path(path or AUTHORITY_DB_PATH)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database_path.write_text(
        json.dumps(_normalise_authority_database(database), indent=2),
        encoding="utf-8",
    )


save_authority_cache = save_authority_database


def authority_entry_fresh(
    entry: dict[str, Any],
    now: datetime | None = None,
    ttl_days: int = AUTHORITY_CACHE_TTL_DAYS,
) -> bool:
    """Return whether one UTC-dated entry is within the default TTL."""

    fetched_at = str(entry.get("fetched_at") or "").strip()
    if not fetched_at or _entry_score(entry) is None:
        return False
    try:
        parsed = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    age = current - parsed.astimezone(timezone.utc)
    return timedelta(0) <= age <= timedelta(days=max(0, int(ttl_days)))


authority_cache_entry_fresh = authority_entry_fresh


def authority_cache_key(targets: Iterable[str]) -> str:
    normalized = sorted({root_domain(str(target)) for target in targets if str(target).strip()})
    return hashlib.sha256(json.dumps(normalized, separators=(",", ":")).encode("utf-8")).hexdigest()


def load_cached_authority(targets: Iterable[str]) -> dict | None:
    if not AUTHORITY_RAW_PATH.exists():
        return None
    try:
        cached = json.loads(AUTHORITY_RAW_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return cached if isinstance(cached, dict) and cached.get("target_set_sha256") == authority_cache_key(targets) else None


def fetch_authority(targets: list[str], auth: str) -> dict:
    """Make one DataForSEO bulk-ranks call for the complete target set."""

    response = requests.post(
        DATAFORSEO_BULK_RANKS_ENDPOINT,
        json=[{"targets": targets, "rank_scale": "one_hundred"}],
        headers={"Authorization": f"Basic {auth}"},
        timeout=90,
        verify=certifi.where(),
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status_code") != 20000:
        raise RuntimeError(payload.get("status_message", "Unknown DataForSEO authority error"))
    return payload


def _first_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number


def _provider_cost(payload: dict[str, Any] | None) -> float:
    if not isinstance(payload, dict):
        return 0.0
    task_cost = sum(
        float(task.get("cost") or 0)
        for task in payload.get("tasks") or []
        if isinstance(task, dict)
    )
    try:
        return float(payload.get("cost") or task_cost)
    except (TypeError, ValueError):
        return float(task_cost)


def _authority_score(value: float) -> float:
    """Normalize a provider rank to the configured 0-100 scale.

    ``rank_scale=one_hundred`` is sent explicitly.  The divide-by-ten fallback
    keeps an accidentally returned legacy 0-1000 rank from becoming a falsely
    perfect score while still preserving normal 0-100 values exactly.
    """

    if value > 100.0 and value <= 1000.0:
        value /= 10.0
    return max(0.0, min(100.0, value))


def authority_rank_map(payload: dict | None) -> dict[str, float]:
    """Read common bulk-ranks result shapes into a 0-100 authority scale."""

    output: dict[str, float] = {}

    def visit(value: Any, inherited_target: str | None = None) -> None:
        if isinstance(value, list):
            for child in value:
                visit(child, inherited_target)
            return
        if not isinstance(value, dict):
            return
        target_value = value.get("target") or value.get("domain") or value.get("url") or inherited_target
        target = root_domain(str(target_value)) if target_value else inherited_target
        rank = None
        for key in ("authority_rank", "authority", "rank", "domain_rank", "backlinks_rank", "rank_absolute"):
            rank = _first_number(value.get(key))
            if rank is not None:
                break
        if target and rank is not None:
            output[target] = _authority_score(rank)
        ignored = {"target", "domain", "url", "authority_rank", "authority", "rank", "domain_rank", "backlinks_rank", "rank_absolute"}
        for key, child in value.items():
            if key not in ignored:
                visit(child, target)

    visit(payload)
    return output


def seed_authority_database_from_artifact(
    database: dict[str, Any],
    database_status: str,
) -> tuple[dict[str, Any], str]:
    """Migrate a prior product authority artifact without another paid call."""

    if database.get("entries") or not AUTHORITY_RAW_PATH.exists():
        return database, database_status
    try:
        artifact = json.loads(AUTHORITY_RAW_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return database, database_status
    if not isinstance(artifact, dict):
        return database, database_status
    scores = dict(artifact.get("effective_scores") or artifact.get("scores") or {})
    if not scores:
        scores = authority_rank_map(artifact.get("raw_response"))
    if not scores:
        return database, database_status
    fetched_at = str(
        artifact.get("fetched_at")
        or artifact.get("collected_at")
        or datetime.now(timezone.utc).isoformat()
    ).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", fetched_at):
        fetched_at = f"{fetched_at}T00:00:00+00:00"
    entries = database.setdefault("entries", {})
    for raw_domain, raw_score in scores.items():
        domain = root_domain(str(raw_domain))
        score = _first_number(raw_score)
        if not domain or score is None:
            continue
        normalized = round(_authority_score(score), 2)
        entries[domain] = {
            "score": normalized,
            "authority_rank": normalized,
            "fetched_at": fetched_at,
            "endpoint": DATAFORSEO_BULK_RANKS_ENDPOINT,
            "rank_scale": "one_hundred",
            "provenance": {
                "provider": "DataForSEO",
                "endpoint": DATAFORSEO_BULK_RANKS_ENDPOINT,
                "rank_scale": "one_hundred",
                "target": domain,
                "method": "migrated_product_authority_artifact",
            },
        }
    if entries:
        save_authority_database(database)
        return database, "seeded_from_product_artifact"
    return database, database_status


def collect_authority(targets: list[str], auth: str, refresh: bool = False) -> tuple[dict, bool]:
    """Resolve scores from the shared database with one bounded provider call.

    ``refresh`` remains part of the public API for the CLI, but intentionally
    does not invalidate fresh shared authority entries.  Only missing or stale
    normalized root domains are sent to Bulk Ranks, at most once per run.
    """

    normalized_targets = sorted(
        {root_domain(str(target)) for target in targets if str(target).strip()}
    )
    database, database_status = _read_authority_database()
    database, database_status = seed_authority_database_from_artifact(
        database,
        database_status,
    )
    entries = database["entries"]
    now = datetime.now(timezone.utc)
    cache_hits: list[str] = []
    missing_or_stale: list[str] = []
    effective_scores: dict[str, float] = {}
    for domain in normalized_targets:
        entry = entries.get(domain)
        score = _entry_score(entry)
        if isinstance(entry, dict) and score is not None and authority_entry_fresh(entry, now):
            cache_hits.append(domain)
            effective_scores[domain] = score
        else:
            missing_or_stale.append(domain)

    payload: dict[str, Any] | None = None
    provider_cost = 0.0
    call_made = bool(missing_or_stale)
    if call_made:
        # This is the sole authority request for the run and contains only
        # domains absent from, or expired in, the shared database.
        payload = fetch_authority(missing_or_stale, auth)
        provider_cost = _provider_cost(payload)
        fetched_at = now.isoformat()
        scores = authority_rank_map(payload)
        for domain in missing_or_stale:
            score = scores.get(domain)
            if score is None:
                # Do not create a fabricated score or refresh a stale entry.
                continue
            score = round(_authority_score(float(score)), 2)
            entries[domain] = {
                "score": score,
                "authority_rank": score,
                "fetched_at": fetched_at,
                "endpoint": DATAFORSEO_BULK_RANKS_ENDPOINT,
                "rank_scale": "one_hundred",
                "provenance": {
                    "provider": "DataForSEO",
                    "endpoint": DATAFORSEO_BULK_RANKS_ENDPOINT,
                    "rank_scale": "one_hundred",
                    "target": domain,
                    "method": "backlinks_bulk_ranks_live",
                },
            }
            effective_scores[domain] = score
        # Persist the merged database even when the response omitted one or
        # more requested scores; omitted domains remain absent/stale.
        save_authority_database(database)

    effective_scores = {
        domain: round(float(effective_scores.get(domain, 0.0)), 2)
        for domain in normalized_targets
    }
    record = {
        "endpoint": DATAFORSEO_BULK_RANKS_ENDPOINT,
        "rank_scale": "one_hundred",
        "targets": normalized_targets,
        "all_requested_domains": normalized_targets,
        "requested_domains": missing_or_stale,
        "provider_requested_domains": missing_or_stale,
        "cache_hit_domains": cache_hits,
        "missing_or_stale_domains": missing_or_stale,
        "target_set_sha256": authority_cache_key(normalized_targets),
        "collected_at": date.today().isoformat(),
        "call_made": call_made,
        "called": call_made,
        "call_count": 1 if call_made else 0,
        "cost": provider_cost,
        "provider_cost": provider_cost,
        "recorded_cost": provider_cost,
        "cached": not call_made,
        "last_run_cache_hit": not call_made,
        "refresh_requested": bool(refresh),
        "refresh_does_not_invalidate_fresh_entries": True,
        "database_path": str(AUTHORITY_DB_PATH),
        "database_status": database_status,
        "ttl_days": AUTHORITY_CACHE_TTL_DAYS,
        "effective_scores": effective_scores,
        "scores": effective_scores,
        "raw_response": payload,
    }
    AUTHORITY_RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTHORITY_RAW_PATH.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record, call_made


def save_results(
    tasks: list[dict],
    sources: list[dict],
    authority: dict | None = None,
    authority_called: bool = False,
) -> None:
    raw_tasks = [
        {key: value for key, value in task.items() if key != "_cached"}
        for task in tasks
    ]
    SERP_RAW_PATH.write_text(
        json.dumps({"status_code": 20000, "tasks": raw_tasks}, indent=2),
        encoding="utf-8",
    )
    signals = serp_signals(tasks)
    signal_summary = {
        "by_query": signals,
        "queries": signals,
        "organic_titles": [item.get("title") for value in signals.values() for item in value.get("organic", []) if item.get("title")],
        "organic_descriptions": [item.get("description") for value in signals.values() for item in value.get("organic", []) if item.get("description")],
        "people_also_ask": sorted({question for value in signals.values() for question in value.get("people_also_ask", [])}),
        "related_searches": sorted({query for value in signals.values() for query in value.get("related_searches", [])}),
        "ai_overview_present": any(value.get("ai_overview_present") for value in signals.values()),
        "commercial_serp_features": sorted({feature for value in signals.values() for feature in value.get("commercial_serp_features", [])}),
    }
    signal_summary.update(signals)
    task_summaries = [task_summary(task) for task in tasks]
    dataforseo_cost = sum(float(task.get("cost") or 0) for task in tasks)
    if authority:
        dataforseo_cost += float(
            authority.get("recorded_cost") or authority.get("cost") or 0
        )
    authority_payload = (authority or {}).get("raw_response") if authority else None
    authority_map = dict((authority or {}).get("effective_scores") or {})
    if not authority_map:
        authority_map = authority_rank_map(
            authority_payload if authority_payload is not None else authority
        )
    source_output = []
    for source in sources:
        value = dict(source)
        value["authority_rank"] = authority_map.get(root_domain(value.get("root_domain") or value.get("url") or ""))
        source_output.append(value)
    output = {
        "product": PRODUCT,
        "collection": {
            "provider": "DataForSEO Google Organic Live Advanced",
            "location": "United States",
            "language": "English",
            "queries": [task.get("data", {}).get("keyword") for task in tasks],
            "actual_queries": [task.get("data", {}).get("keyword") for task in tasks],
            "primary_query": PRIMARY_SERP_QUERY,
            "fallback_query": FALLBACK_SERP_QUERY,
            "fallback_used": any(task.get("data", {}).get("keyword") == FALLBACK_SERP_QUERY for task in tasks),
            "collected_at": date.today().isoformat(),
            "search_call_count": sum(not task.get("_cached") for task in tasks),
            "call_count": sum(not task.get("_cached") for task in tasks) + int(authority_called),
            "total_call_count": sum(not task.get("_cached") for task in tasks) + int(authority_called),
            "calls": task_summaries,
            "tasks": task_summaries,
            "per_call_costs": [
                *[{"stage": "discovery", "query": item.get("query"), "cost": item.get("cost"), "cached": item.get("cached")} for item in task_summaries],
                {
                    "stage": "authority",
                    "query": None,
                    "cost": (authority or {}).get("cost"),
                    "recorded_cost": (authority or {}).get("recorded_cost"),
                    "cached": not authority_called,
                },
            ],
            "total_dataforseo_cost": dataforseo_cost,
            "total_cost_includes_authority": True,
            "authority_call_count": int(authority_called),
        },
        "serp_signals": signal_summary,
        "authority": {
            "endpoint": DATAFORSEO_BULK_RANKS_ENDPOINT,
            "targets": (authority or {}).get("targets") or authority_targets(source_output),
            "raw_artifact": str(AUTHORITY_RAW_PATH),
            "called": authority_called,
        },
        "sources": source_output,
    }
    SOURCES_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover Z3FC sources with live Google SERPs")
    parser.add_argument("--refresh", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ensure_data_directories()
    try:
        auth = load_auth()
        tasks, new_cost = collect_tasks(auth, args.refresh)
        sources = normalized_sources(tasks)
        authority, authority_called = collect_authority(authority_targets(sources), auth, args.refresh)
        save_results(tasks, sources, authority, authority_called)
    except requests.RequestException as error:
        raise SystemExit(f"DataForSEO request failed: {error}") from error
    except RuntimeError as error:
        raise SystemExit(f"DataForSEO error: {error}") from error
    total_cost = sum(float(task.get("cost") or 0) for task in tasks) + float(authority.get("cost") or 0)
    print(f"Saved {len(sources)} unique sources to {SOURCES_PATH}")
    new_authority_cost = float(authority.get("cost") or 0) if authority_called else 0.0
    print(
        f"New DataForSEO cost: ${new_cost + new_authority_cost:.4f}; "
        f"saved-result cost: ${total_cost:.4f}"
    )


if __name__ == "__main__":
    main()


__all__ = [
    "add_source",
    "authority_cache_key",
    "authority_cache_entry_fresh",
    "authority_entry_fresh",
    "authority_rank_map",
    "authority_targets",
    "canonical_url",
    "classify_source",
    "collect_authority",
    "collect_tasks",
    "fallback_needed",
    "is_independent_candidate",
    "load_authority_database",
    "load_authority_db",
    "load_authority_cache",
    "normalized_sources",
    "root_domain",
    "search_task",
    "save_authority_database",
    "save_authority_cache",
    "seed_authority_database_from_artifact",
    "serp_signals",
    "should_run_fallback",
]
