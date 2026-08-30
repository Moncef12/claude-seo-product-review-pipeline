"""Run or reuse the initial Haiku factual-grounding audit."""

from __future__ import annotations

import argparse
import json

import anthropic
from dotenv import load_dotenv

from review_pipeline.config import (
    FACTUAL_AUDIT_PATH,
    NORMALIZED_EVIDENCE_PATH,
    OUTPUT_DIR,
    PROJECT_ROOT,
    ensure_data_directories,
)
from review_pipeline.factual_validation import (
    MODEL,
    PROMPT_VERSION,
    audit_article,
    cached_run_matches,
    stable_hash,
)


DRAFT_PATH = OUTPUT_DIR / "draft.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit the Sonnet candidate against normalized evidence with Haiku"
    )
    parser.add_argument("--refresh", action="store_true")
    return parser.parse_args()


def load_record() -> dict:
    if not FACTUAL_AUDIT_PATH.exists():
        return {}
    value = json.loads(FACTUAL_AUDIT_PATH.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def record_matches(record: dict, evidence: dict) -> bool:
    return (
        record.get("model") == MODEL
        and record.get("prompt_version") == PROMPT_VERSION
        and record.get("evidence_sha256") == stable_hash(evidence)
    )


def save_initial(record: dict, run: dict, evidence: dict) -> dict:
    output = {
        "model": MODEL,
        "prompt_version": PROMPT_VERSION,
        "evidence_sha256": stable_hash(evidence),
        "initial": run,
        "final": None,
        "final_reused_initial": False,
    }
    FACTUAL_AUDIT_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")
    return output


def main() -> None:
    args = parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    ensure_data_directories()
    article = DRAFT_PATH.read_text(encoding="utf-8").strip()
    evidence = json.loads(NORMALIZED_EVIDENCE_PATH.read_text(encoding="utf-8"))
    record = load_record()
    if (
        not args.refresh
        and record_matches(record, evidence)
        and cached_run_matches(record.get("initial"), article)
    ):
        audit = record["initial"]["audit"]
        print(
            f"CACHED initial Haiku factual audit: "
            f"{'PASSED' if audit['passed'] else 'FAILED'}, "
            f"{audit['audited_claim_count']} claims, {len(audit['issues'])} issues"
        )
        return

    run = audit_article(anthropic.Anthropic(), article, evidence)
    save_initial(record, run, evidence)
    audit = run["audit"]
    print(
        f"{'PASSED' if audit['passed'] else 'FAILED'} initial Haiku factual audit: "
        f"{audit['audited_claim_count']} claims, {len(audit['issues'])} issues, "
        f"{run['usage']['input_tokens']} input / "
        f"{run['usage']['output_tokens']} output tokens"
    )
    for issue in audit["issues"]:
        print(f"- {issue['category']}: {issue['explanation']}")
    print(f"Saved factual audit to {FACTUAL_AUDIT_PATH}")


if __name__ == "__main__":
    main()
