"""Run or reuse the combined Haiku grounding, plan, and decision audit."""

from __future__ import annotations

import argparse
import json

import anthropic
from dotenv import load_dotenv

from review_pipeline.config import (
    FACTUAL_AUDIT_PATH,
    NORMALIZED_EVIDENCE_PATH,
    OUTPUT_DIR,
    PLAN_PATH,
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


def record_matches(record: dict, evidence: dict, plan: dict) -> bool:
    return (
        record.get("model") == MODEL
        and record.get("prompt_version") == PROMPT_VERSION
        and record.get("evidence_sha256") == stable_hash(evidence)
        and record.get("plan_sha256") == stable_hash(plan)
    )


def save_initial(record: dict, run: dict, evidence: dict, plan: dict) -> dict:
    output = {
        "model": MODEL,
        "prompt_version": PROMPT_VERSION,
        "evidence_sha256": stable_hash(evidence),
        "plan_sha256": stable_hash(plan),
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
    plan_record = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    plan = plan_record.get("plan", plan_record) if isinstance(plan_record, dict) else {}
    if not isinstance(plan, dict) or not plan:
        raise SystemExit("SEO/AIO/CRO plan is missing; run the plan stage first")
    record = load_record()
    if (
        not args.refresh
        and record_matches(record, evidence, plan)
        and cached_run_matches(record.get("initial"), article, plan)
    ):
        audit = record["initial"]["audit"]
        print(
            f"CACHED initial Haiku combined audit: "
            f"{'PASSED' if audit['passed'] else 'FAILED'}, "
            f"{audit['supported_claim_count']}/{audit['audited_claim_count']} claims, "
            f"{audit['plan_covered_count']}/{audit['plan_checked_count']} plan items, "
            f"{audit['buyer_question_covered_count']}/{audit['buyer_question_checked_count']} buyer questions, "
            f"{audit['decision_met_count']}/{audit['decision_checked_count']} decision standards"
        )
        return

    run = audit_article(anthropic.Anthropic(), article, evidence, plan)
    save_initial(record, run, evidence, plan)
    audit = run["audit"]
    print(
        f"{'PASSED' if audit['passed'] else 'FAILED'} initial Haiku combined audit: "
        f"{audit['supported_claim_count']}/{audit['audited_claim_count']} claims, "
        f"{audit['plan_covered_count']}/{audit['plan_checked_count']} plan items, "
        f"{audit['buyer_question_covered_count']}/{audit['buyer_question_checked_count']} buyer questions, "
        f"{audit['decision_met_count']}/{audit['decision_checked_count']} decision standards, "
        f"{len(audit['issues'])} issues, "
        f"{run['usage']['input_tokens']} input / "
        f"{run['usage']['output_tokens']} output tokens"
    )
    for issue in audit["issues"]:
        print(f"- {issue['category']}: {issue['explanation']}")
    print(f"Saved factual audit to {FACTUAL_AUDIT_PATH}")


if __name__ == "__main__":
    main()
