import hashlib
import json
from datetime import datetime, timezone

from review_pipeline.config import (
    ARTICLE_MAX_WORDS,
    ARTICLE_MIN_WORDS,
    OUTPUT_DIR,
    PLAN_PATH,
    PRODUCT,
    REQUIRED_HEADINGS,
    REVIEW_MANIFEST_PATH,
    SELECTED_REVIEW_COUNT,
    NORMALIZED_EVIDENCE_PATH,
    VALIDATION_PATH,
    ensure_data_directories,
)
from review_pipeline.validation import validate_review


DRAFT_PATH = OUTPUT_DIR / "draft.md"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_entries(value) -> list[dict]:
    if isinstance(value, list):
        entries = value
    elif isinstance(value, dict):
        entries = value.get("sources") or []
    else:
        entries = []
    if len(entries) != SELECTED_REVIEW_COUNT:
        return []
    output = []
    seen = set()
    seen_urls = set()
    for item in entries:
        if not isinstance(item, dict):
            return []
        publisher = str(item.get("publisher") or "").strip()
        url = str(item.get("url") or "").strip()
        key = (publisher.casefold(), url.casefold().rstrip("/"))
        if not publisher or not url or key in seen or key[1] in seen_urls:
            return []
        output.append({"publisher": publisher, "url": url})
        seen.add(key)
        seen_urls.add(key[1])
    return output


def dynamic_expected_sources(manifest: object, evidence: object) -> list[dict]:
    """Return exactly five valid dynamic sources or fail closed."""

    for value in (manifest, evidence):
        sources = _source_entries(value)
        if len(sources) == SELECTED_REVIEW_COUNT:
            return sources
    raise ValueError(
        f"Dynamic validation requires exactly {SELECTED_REVIEW_COUNT} valid expected sources"
    )


def _expected_sources() -> list[dict]:
    """Read and validate the dynamic five-source manifest/evidence set."""

    try:
        manifest = json.loads(REVIEW_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        manifest = None
    try:
        evidence = json.loads(NORMALIZED_EVIDENCE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        evidence = None
    return dynamic_expected_sources(manifest, evidence)


def _load_plan() -> dict | None:
    try:
        record = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if isinstance(record, dict) and isinstance(record.get("plan"), dict):
        return record["plan"]
    return record if isinstance(record, dict) else None


def validate_article(
    article: str,
    editorial_plan: dict | None = None,
    expected_sources: list[dict] | None = None,
) -> dict:
    editorial_plan = _load_plan() if editorial_plan is None else editorial_plan
    return validate_review(
        article,
        product=PRODUCT,
        required_headings=REQUIRED_HEADINGS,
        expected_sources=_expected_sources() if expected_sources is None else expected_sources,
        methodology_heading="How We Researched This Review",
        min_words=ARTICLE_MIN_WORDS,
        max_words=ARTICLE_MAX_WORDS,
        editorial_plan=editorial_plan,
    )


def save_initial_report(article: str, report: dict, editorial_plan: dict | None = None) -> dict:
    output = {
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_sha256": content_hash(article),
        "repair_called": False,
        "plan_sha256": hashlib.sha256(json.dumps(editorial_plan or {}, sort_keys=True).encode("utf-8")).hexdigest(),
        "initial": report,
        "final": report,
    }
    VALIDATION_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")
    return output


def main() -> None:
    ensure_data_directories()
    article = DRAFT_PATH.read_text(encoding="utf-8").strip()
    report = validate_article(article)
    save_initial_report(article, report, _load_plan())
    status = "PASSED" if report["passed"] else "FAILED"
    print(
        f"{status} deterministic validation: {report['word_count']} words, "
        f"{len(report['issues'])} issues"
    )
    for issue in report["issues"]:
        print(f"- {issue['code']}: {issue['message']}")
    print(f"Saved validation report to {VALIDATION_PATH}")


if __name__ == "__main__":
    main()


__all__ = ["content_hash", "dynamic_expected_sources", "save_initial_report", "validate_article"]
