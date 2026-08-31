"""Write the deterministic production-summary artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from review_pipeline.config import (
    AUTHORITY_RAW_PATH,
    EXTRACTIONS_PATH,
    FACTUAL_AUDIT_PATH,
    GENERATION_PATH,
    NORMALIZED_EVIDENCE_PATH,
    OUTPUT_DIR,
    PLAN_PATH,
    PRODUCTION_SUMMARY_PATH,
    QUALIFICATION_PATH,
    REVIEW_MANIFEST_PATH,
    SCRAPED_REVIEWS_DIR,
    SOURCES_PATH,
    VALIDATION_PATH,
    REPAIR_PATH,
    ensure_data_directories,
)
from review_pipeline.production_summary import build_production_summary, write_summary


def _read(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return default


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Write production API/cost/validation summary")
    # Summary is deterministic. The flag is accepted for CLI symmetry but does
    # not trigger any provider work or alter the summary identity.
    parser.add_argument("--refresh", action="store_true", help="Accepted for CLI symmetry; summary is always rebuilt deterministically")
    parser.parse_args()
    ensure_data_directories()
    manifest = _read(REVIEW_MANIFEST_PATH, [])
    summary = build_production_summary(
        discovery=_read(SOURCES_PATH, {}),
        authority=_read(AUTHORITY_RAW_PATH, {}),
        scrape=_read(QUALIFICATION_PATH, {}),
        manifest=manifest,
        extraction=_read(EXTRACTIONS_PATH, {}),
        plan=_read(PLAN_PATH, {}),
        generation=_read(GENERATION_PATH, {}),
        validation=_read(VALIDATION_PATH, {}),
        factual_audit=_read(FACTUAL_AUDIT_PATH, {}),
        repair=_read(REPAIR_PATH, {}),
        final_article=_text(OUTPUT_DIR / "polished.md") or _text(OUTPUT_DIR / "review.md"),
    )
    write_summary(PRODUCTION_SUMMARY_PATH, summary)
    print(
        f"Saved production summary: {summary['total_calls']} external calls, "
        f"{summary['tokens']['total']} Claude tokens, "
        f"${summary['estimated_total_usd']:.6f} estimated total"
    )


if __name__ == "__main__":
    main()


__all__ = ["build_production_summary"]
