"""Fetch discovered candidates and deterministically qualify five reviews."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlsplit

import certifi
import requests
from bs4 import BeautifulSoup

from review_pipeline.config import (
    PRELIMINARY_CANDIDATE_LIMIT,
    PRODUCT,
    QUALIFICATION_PATH,
    REVIEW_MANIFEST_PATH,
    SCRAPED_REVIEWS_DIR,
    SELECTED_REVIEW_COUNT,
    SOURCES_PATH,
    ensure_data_directories,
)
from review_pipeline.stages.discover import root_domain


REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

QUALIFICATION_WEIGHTS = {
    "exact_model_relevance": 25,
    "original_testing_evidence": 20,
    "editorial_independence": 15,
    "publisher_site_authority": 10,
    "author_credibility_transparency": 10,
    "article_depth": 10,
    "recency": 5,
    "accessibility_attribution": 5,
}


def source_type(record: dict[str, Any]) -> str:
    return str(record.get("source_type") or record.get("classification") or record.get("type") or "")

TESTING_PATTERNS = (
    r"\b(?:tested|testing|test results|benchmark(?:ed|ing)?|measured|measurement)\b",
    r"\b(?:colorimeter|calibrat(?:ed|ion)|latency|response time|sRGB|nits|brightness)\b",
    r"\b(?:frame rate|fps|refresh rate).{0,35}\b(?:test|measure|benchmark|observ)\w*\b",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape and qualify discovered Z3FC review candidates")
    parser.add_argument("--refresh", action="store_true")
    return parser.parse_args()


def review_cache_path(publisher: str, url: str) -> Path:
    name = re.sub(r"[^a-z0-9]+", "-", str(publisher or "source").lower()).strip("-") or "source"
    digest = hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:12]
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


def remove_page_chrome(soup: BeautifulSoup) -> None:
    selector = "script, style, noscript, svg, nav, header, footer, form, aside, iframe"
    for tag in soup.select(selector):
        tag.decompose()


def normalize_text(container: Any) -> str:
    if not container:
        return ""
    lines: list[str] = []
    for raw_line in container.get_text("\n", strip=True).splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n".join(lines)


def _meta(soup: BeautifulSoup, *names: str) -> str:
    wanted = {name.casefold() for name in names}
    for tag in soup.find_all("meta"):
        key = str(tag.get("name") or tag.get("property") or tag.get("itemprop") or "").casefold()
        if key in wanted and tag.get("content"):
            return str(tag["content"]).strip()
    return ""


def _jsonld_objects(soup: BeautifulSoup) -> Iterable[dict[str, Any]]:
    for script in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = script.string or script.get_text()
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, dict):
                yield item
                graph = item.get("@graph")
                if isinstance(graph, list):
                    yield from (child for child in graph if isinstance(child, dict))


def _jsonld_article(soup: BeautifulSoup) -> dict[str, Any]:
    for item in _jsonld_objects(soup):
        types = item.get("@type")
        types = types if isinstance(types, list) else [types]
        if any(str(value).casefold() in {"article", "review", "newsarticle", "techarticle"} for value in types):
            return item
    return {}


def _person_fields(value: Any) -> tuple[str, str]:
    if isinstance(value, list):
        value = next((item for item in value if item), None)
    if isinstance(value, dict):
        name = str(value.get("name") or value.get("givenName") or "").strip()
        url = str(value.get("url") or value.get("sameAs") or "").strip()
        return name, url
    return str(value or "").strip(), ""


def extract_page_metadata(html: str, source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extract standard page metadata before page-chrome removal."""

    soup = BeautifulSoup(html, "lxml")
    article_json = _jsonld_article(soup)
    title = (
        _meta(soup, "og:title", "twitter:title")
        or str(article_json.get("headline") or "").strip()
        or (soup.title.get_text(" ", strip=True) if soup.title else "")
    )
    publisher = (
        _meta(soup, "og:site_name", "application-name", "publisher")
        or _person_fields(article_json.get("publisher"))[0]
        or str((source or {}).get("publisher") or "").strip()
        or str((source or {}).get("domain") or "").strip()
        or root_domain(str((source or {}).get("url") or ""))
    )
    author, author_url = _person_fields(article_json.get("author"))
    author = _meta(soup, "author", "byline", "article:author") or author
    author_url = _meta(soup, "author:url", "author_url") or author_url
    published = (
        _meta(soup, "article:published_time", "date", "datepublished", "datePublished", "pubdate")
        or str(article_json.get("datePublished") or article_json.get("dateCreated") or "").strip()
    )
    if not published:
        time_tag = soup.find("time", attrs={"datetime": True})
        published = str(time_tag.get("datetime") or time_tag.get_text(" ", strip=True) or "") if time_tag else ""
    author_bio = _meta(soup, "author:description", "author_bio", "bio")
    if author and not author_url:
        author_tag = soup.find(attrs={"rel": lambda value: value and "author" in value})
        if author_tag and author_tag.get("href"):
            author_url = urljoin(str((source or {}).get("url") or ""), author_tag["href"])
    headings = [tag.get_text(" ", strip=True) for tag in soup.find_all(["h1", "h2", "h3"]) if tag.get_text(" ", strip=True)]
    remove_page_chrome(soup)
    container = soup.find("article") or soup.find("main") or soup.body
    text = normalize_text(container)
    return {
        "publisher": publisher,
        "site_name": publisher,
        "author": author,
        "author_name": author,
        "author_url": author_url,
        "author_profile_url": author_url,
        "author_bio": author_bio,
        "publication_date": published,
        "published_at": published,
        "headings": headings,
        "title": title,
        "text": text,
    }


def clean_article(html: str) -> tuple[str, str]:
    """Backward-compatible title/body helper."""

    metadata = extract_page_metadata(html)
    return metadata["title"], metadata["text"]


def useful_word_count(text: str) -> int:
    return len(re.findall(r"\b[\w]+(?:['’][\w]+)?\b", str(text or ""), re.UNICODE))


def _source_publisher(source: dict[str, Any]) -> str:
    return str(source.get("publisher") or source.get("site_name") or source.get("domain") or root_domain(source.get("url", "")) or "source")


def exact_model_present(record: dict[str, Any]) -> bool:
    if "contains_exact_model" in record:
        return bool(record.get("contains_exact_model"))
    return bool(re.search(r"\barzopa\s+z3fc\b|\bz3fc\b", f"{record.get('title', '')}\n{record.get('text', '')}", re.I))


def build_record(source: dict, response: requests.Response) -> dict:
    metadata = extract_page_metadata(response.text, source)
    text = metadata["text"]
    words = useful_word_count(text)
    if words < 500:
        raise ValueError(f"extracted only {words} useful words")
    record = {
        **{key: value for key, value in source.items() if key in {"url", "domain", "root_domain", "source_type", "best_rank", "authority_rank", "queries", "title", "description"}},
        **{key: value for key, value in metadata.items() if key != "text"},
        "publisher": metadata["publisher"] or _source_publisher(source),
        "url": source.get("url"),
        "final_url": getattr(response, "url", None) or source.get("url"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "word_count": words,
        "useful_word_count": words,
        "contains_exact_model": bool(re.search(r"\barzopa\s+z3fc\b|\bz3fc\b", f"{metadata['title']}\n{text}", re.I)),
        "accessibility": "fetched",
        "text": text,
    }
    return record


def _candidate_label(source: dict[str, Any]) -> str:
    return _source_publisher(source)


def preliminary_pool(sources: Iterable[dict[str, Any]], limit: int = PRELIMINARY_CANDIDATE_LIMIT) -> list[dict[str, Any]]:
    """Choose a deterministic, bounded set of likely independent pages."""

    candidates = [
        source for source in sources
        if source_type(source) == "independent_candidate" and source.get("url")
    ]
    candidates.sort(
        key=lambda source: (
            source.get("best_rank") if isinstance(source.get("best_rank"), (int, float)) else 999,
            -(float(source.get("authority_rank") or 0)),
            root_domain(str(source.get("url") or "")),
            str(source.get("url")),
        )
    )
    return [dict(candidate) for candidate in candidates[: max(0, int(limit))]]


def _testing_points(record: dict[str, Any]) -> tuple[float, list[str]]:
    text = str(record.get("text") or "")
    hits = sum(bool(re.search(pattern, text, re.I)) for pattern in TESTING_PATTERNS)
    points = min(20.0, hits * 6.0 + (2.0 if record.get("headings") else 0.0))
    reasons = []
    if hits:
        reasons.append(f"observable original-testing/evidence signals: {hits} pattern groups")
    else:
        reasons.append("no observable original-testing/evidence signal")
    return points, reasons


def _author_points(record: dict[str, Any]) -> tuple[float, list[str]]:
    author = str(record.get("author_name") or record.get("author") or "").strip()
    profile = str(record.get("author_profile_url") or record.get("author_url") or "").strip()
    bio = str(record.get("author_bio") or "").strip()
    points = (5.0 if author else 0.0) + (3.0 if profile else 0.0) + (2.0 if bio else 0.0)
    reasons = ["named author is visible" if author else "no named author found"]
    if profile:
        reasons.append("author/profile URL is visible")
    if bio:
        reasons.append("author bio/transparency text is visible")
    return points, reasons


def _recency_points(record: dict[str, Any]) -> tuple[float, list[str]]:
    value = str(record.get("publication_date") or record.get("published_at") or "")
    match = re.search(r"(?:19|20)\d{2}", value)
    if not match:
        return 0.0, ["publication date was not observable"]
    year = int(match.group(0))
    current = datetime.now(timezone.utc).year
    age = max(0, current - year)
    points = 5.0 if age <= 2 else 3.0 if age <= 5 else 1.0 if age <= 8 else 0.0
    return points, [f"publication year {year} yields {points:g}/5 recency points"]


def hard_rejection_reasons(record: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if record.get("duplicate_of") or record.get("is_duplicate") or record.get("syndicated"):
        reasons.append("duplicate/syndicated candidate was identified")
    if source_type(record) != "independent_candidate":
        reasons.append(f"source type {source_type(record) or 'unknown'} is not an independent candidate")
    if not exact_model_present(record):
        reasons.append("wrong model or exact Arzopa Z3FC presence was not observed")
    words = int(record.get("useful_word_count") or record.get("word_count") or useful_word_count(record.get("text", "")))
    if words < 500:
        reasons.append(f"only {words} useful words, below the 500-word minimum")
    accessibility = str(record.get("accessibility") or record.get("fetch_status") or "fetched").casefold()
    if accessibility not in {"fetched", "accessible", "ok", "success"}:
        reasons.append("page was inaccessible")
    if record.get("fetch_error"):
        reasons.append(f"page fetch failed: {record['fetch_error']}")
    if record.get("qualification_error"):
        reasons.append(f"qualification failed: {record['qualification_error']}")
    return reasons


def _record_content_hash(record: dict[str, Any]) -> str:
    digest = str(record.get("content_sha256") or "").strip()
    if digest:
        return digest
    text = re.sub(r"\s+", " ", str(record.get("text") or "")).strip().casefold()
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""


def score_candidate(record: dict[str, Any], authority_rank: float | None = None) -> dict[str, Any]:
    """Score observable credibility signals; this is not proof of expertise."""

    authority = authority_rank if authority_rank is not None else record.get("authority_rank")
    try:
        authority = max(0.0, min(100.0, float(authority or 0)))
    except (TypeError, ValueError):
        authority = 0.0
    testing, testing_reasons = _testing_points(record)
    author, author_reasons = _author_points(record)
    recency, recency_reasons = _recency_points(record)
    words = int(record.get("useful_word_count") or record.get("word_count") or useful_word_count(record.get("text", "")))
    breakdown = {
        "exact_model_relevance": {"points": 25.0 if exact_model_present(record) else 0.0, "max": 25, "reason": "exact Arzopa Z3FC presence observed" if exact_model_present(record) else "exact model presence not observed"},
        "original_testing_evidence": {"points": testing, "max": 20, "reason": "; ".join(testing_reasons)},
        "editorial_independence": {"points": 15.0 if source_type(record) == "independent_candidate" else 0.0, "max": 15, "reason": "classified as an independent editorial candidate" if source_type(record) == "independent_candidate" else "not classified as independent editorial coverage"},
        "publisher_site_authority": {"points": round(authority / 10.0, 2), "max": 10, "reason": f"DataForSEO authority signal {authority:g}/100; SERP rank is not used as authority"},
        "author_credibility_transparency": {"points": author, "max": 10, "reason": "; ".join(author_reasons) + "; signals are transparency indicators, not proof of expertise"},
        "article_depth": {"points": round(min(10.0, words / 1500.0 * 10.0), 2), "max": 10, "reason": f"{words} useful words"},
        "recency": {"points": recency, "max": 5, "reason": "; ".join(recency_reasons)},
        "accessibility_attribution": {"points": 5.0 if record.get("publisher") and record.get("url") and str(record.get("accessibility", "fetched")).casefold() in {"fetched", "accessible", "ok", "success"} else 0.0, "max": 5, "reason": "page fetched and publisher/URL attribution is available" if record.get("publisher") and record.get("url") else "publisher or URL attribution is incomplete"},
    }
    total = round(sum(float(item["points"]) for item in breakdown.values()), 2)
    reasons = [item["reason"] for item in breakdown.values()]
    return {"breakdown": breakdown, "total": total, "score": total, "reasons": reasons}


def score_source(record: dict[str, Any], authority_rank: float | None = None) -> float:
    return float(score_candidate(record, authority_rank)["total"])


source_score = score_source


def qualify_candidate(record: dict[str, Any], authority_rank: float | None = None) -> dict[str, Any]:
    hard = hard_rejection_reasons(record)
    score = score_candidate(record, authority_rank)
    return {
        "url": record.get("url"),
        "final_url": record.get("final_url"),
        "root_domain": record.get("root_domain") or root_domain(str(record.get("url") or "")),
        "publisher": record.get("publisher") or _candidate_label(record),
        "source_type": source_type(record),
        "cache_file": record.get("cache_file"),
        "content_sha256": record.get("content_sha256"),
        "authority_rank": record.get("authority_rank") if authority_rank is None else authority_rank,
        "score_breakdown": score["breakdown"],
        "total_score": score["total"],
        "reasons": score["reasons"] + hard,
        "hard_rejection_reasons": hard,
        "eligible": not hard,
        "record": record,
    }


def qualify_and_select(
    records: Iterable[dict[str, Any]],
    authority_scores: dict[str, float] | None = None,
    selected_count: int = SELECTED_REVIEW_COUNT,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return every considered row and the highest-scoring unique-domain rows."""

    authority_scores = authority_scores or {}
    considered = []
    for record in records:
        domain = root_domain(str(record.get("root_domain") or record.get("url") or ""))
        considered.append(qualify_candidate(record, authority_scores.get(domain)))
    considered.sort(key=lambda row: (-float(row["total_score"]), str(row.get("root_domain") or ""), str(row.get("url") or "")))
    seen_hashes: set[str] = set()
    seen_urls: set[str] = set()
    for row in considered:
        record = row["record"]
        digest = _record_content_hash(record)
        url = str(record.get("url") or "").strip().casefold().rstrip("/")
        if digest and digest in seen_hashes:
            row["eligible"] = False
            row["hard_rejection_reasons"].append("duplicate/syndicated content hash")
            row["reasons"].append("duplicate/syndicated content hash")
        elif url and url in seen_urls:
            row["eligible"] = False
            row["hard_rejection_reasons"].append("duplicate canonical URL")
            row["reasons"].append("duplicate canonical URL")
        elif digest:
            seen_hashes.add(digest)
        if url:
            seen_urls.add(url)
    selected: list[dict[str, Any]] = []
    selected_domains: set[str] = set()
    for row in considered:
        domain = str(row.get("root_domain") or "")
        if not row["eligible"]:
            row["result"] = "rejected"
            continue
        if domain in selected_domains:
            row["eligible"] = False
            row["hard_rejection_reasons"].append("root domain already represented by a higher-scoring selected page")
            row["reasons"].append("root domain already represented by a higher-scoring selected page")
            row["result"] = "rejected"
            continue
        if len(selected) < selected_count:
            row["result"] = "selected"
            row["selection_rank"] = len(selected) + 1
            selected.append(row)
            selected_domains.add(domain)
        else:
            row["result"] = "rejected"
            row["hard_rejection_reasons"].append("outside the top selected source count")
            row["reasons"].append("outside the top selected source count")
    selected.sort(key=lambda row: row.get("selection_rank", 999))
    return considered, selected


select_sources = qualify_and_select
select_five = qualify_and_select


def manifest_entry(record: dict) -> dict:
    cache_file = record.get("cache_file") or record.get("cache_path")
    if not cache_file:
        cache_file = review_cache_path(record.get("publisher", "source"), record.get("url", "")).name
    filename = Path(str(cache_file)).name
    return {
        key: value for key, value in record.items()
        if key != "text" and key != "record"
    } | {
        "cache_file": filename,
        "cache_filename": filename,
        "cache_path": filename,
        "cache_reference": filename,
    }


def _load_discovered_sources() -> tuple[list[dict], dict[str, float]]:
    payload = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload, {}
    sources = payload.get("sources") or []
    authority = {
        root_domain(str(source.get("root_domain") or source.get("domain") or source.get("url") or "")): float(source.get("authority_rank") or 0)
        for source in sources
        if source.get("authority_rank") is not None
    }
    return sources, authority


def load_or_fetch(source: dict, refresh: bool) -> dict | None:
    publisher = _source_publisher(source)
    path = review_cache_path(publisher, source["url"])
    if path.exists() and not refresh:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            record.setdefault("cache_file", path.name)
            record.setdefault("cache_path", path.name)
            record["cache_hit"] = True
            print(f"CACHED {publisher}: {record.get('word_count', 0)} words")
            return record
        except (OSError, json.JSONDecodeError):
            pass
    try:
        record = build_record(source, fetch_page(source["url"]))
    except ValueError as error:
        print(f"FAILED {publisher}: {error}")
        return {
            **source,
            "publisher": publisher,
            "url": source.get("url"),
            "source_type": source.get("source_type"),
            "accessibility": "fetched",
            "qualification_error": str(error),
            "cache_hit": False,
            "text": "",
            "useful_word_count": 0,
            "word_count": 0,
            "contains_exact_model": False,
        }
    except requests.RequestException as error:
        print(f"FAILED {publisher}: {error}")
        return {
            **source,
            "publisher": publisher,
            "url": source.get("url"),
            "source_type": source.get("source_type"),
            "accessibility": "inaccessible",
            "fetch_error": str(error),
            "cache_hit": False,
            "text": "",
            "useful_word_count": 0,
            "word_count": 0,
            "contains_exact_model": False,
        }
    record["cache_file"] = path.name
    record["cache_path"] = path.name
    record["cache_hit"] = False
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"FETCHED {publisher}: {record['word_count']} words")
    return record


def save_qualification(considered: list[dict], selected: list[dict], pool: list[dict]) -> dict:
    def public(row: dict) -> dict:
        value = {key: item for key, item in row.items() if key != "record"}
        record = row.get("record") or {}
        value.setdefault("metadata", {key: record.get(key) for key in ("title", "author", "author_profile_url", "publication_date", "headings", "content_sha256", "useful_word_count")})
        return value

    output = {
        "product": PRODUCT,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "criteria": {
            "minimum_useful_words": 500,
            "selected_count": SELECTED_REVIEW_COUNT,
            "one_selected_page_per_root_domain": True,
            "rejections": ["wrong model", "thin page", "non-independent type", "duplicate/syndication", "inaccessible"],
            "signals_are_observable_not_proof": True,
        },
        "weights": QUALIFICATION_WEIGHTS,
        "preliminary_pool": pool,
        "considered": [public(row) for row in considered],
        "selected": [public(row) for row in selected],
        "selected_count": len(selected),
        "fetch_count": sum(not bool((row.get("record") or {}).get("cache_hit")) for row in considered),
        "cached_count": sum(bool((row.get("record") or {}).get("cache_hit")) for row in considered),
    }
    QUALIFICATION_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")
    return output


def main() -> None:
    args = parse_args()
    ensure_data_directories()
    try:
        discovered, authority = _load_discovered_sources()
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Discovery sources are unavailable: {error}") from error
    pool = preliminary_pool(discovered)
    records = [record for source in pool if (record := load_or_fetch(source, args.refresh))]
    considered, selected = qualify_and_select(records, authority)
    if len(selected) != SELECTED_REVIEW_COUNT:
        save_qualification(considered, selected, pool)
        raise SystemExit(f"Only {len(selected)} eligible unique-domain reviews were found; need {SELECTED_REVIEW_COUNT}")
    for row in selected:
        row["record"]["cache_file"] = row["record"].get("cache_file") or review_cache_path(row["publisher"], row["url"]).name
    manifest = [manifest_entry(row["record"]) for row in selected]
    REVIEW_MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    save_qualification(considered, selected, pool)
    print(f"Saved qualification artifact to {QUALIFICATION_PATH}")
    print(f"Saved exactly {len(manifest)} selected reviews to {REVIEW_MANIFEST_PATH}")


if __name__ == "__main__":
    main()


__all__ = [
    "QUALIFICATION_WEIGHTS",
    "build_record",
    "clean_article",
    "extract_page_metadata",
    "exact_model_present",
    "hard_rejection_reasons",
    "manifest_entry",
    "preliminary_pool",
    "qualify_and_select",
    "qualify_candidate",
    "review_cache_path",
    "score_candidate",
    "score_source",
    "source_score",
    "select_five",
    "select_sources",
    "useful_word_count",
]
