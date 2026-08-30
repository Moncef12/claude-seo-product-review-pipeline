import hashlib
import json
from datetime import datetime, timezone

from review_pipeline.config import (
    ARTICLE_MAX_WORDS,
    ARTICLE_MIN_WORDS,
    OUTPUT_DIR,
    PRODUCT,
    REQUIRED_HEADINGS,
    REVIEW_SOURCES,
    VALIDATION_PATH,
    ensure_data_directories,
)
from review_pipeline.validation import validate_review


DRAFT_PATH = OUTPUT_DIR / "draft.md"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_article(article: str) -> dict:
    return validate_review(
        article,
        product=PRODUCT,
        required_headings=REQUIRED_HEADINGS,
        expected_sources=REVIEW_SOURCES,
        methodology_heading="How We Researched This Review",
        min_words=ARTICLE_MIN_WORDS,
        max_words=ARTICLE_MAX_WORDS,
    )


def save_initial_report(article: str, report: dict) -> dict:
    output = {
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_sha256": content_hash(article),
        "repair_called": False,
        "initial": report,
        "final": report,
    }
    VALIDATION_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")
    return output


def main() -> None:
    ensure_data_directories()
    article = DRAFT_PATH.read_text(encoding="utf-8").strip()
    report = validate_article(article)
    save_initial_report(article, report)
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
