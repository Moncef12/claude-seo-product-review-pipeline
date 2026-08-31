"""Re-audit a repaired article and enforce the final combined validation gate."""

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
    REPAIR_PATH,
    VALIDATION_PATH,
    ensure_data_directories,
)
from review_pipeline.factual_validation import (
    MODEL,
    PROMPT_VERSION,
    audit_article,
    cached_run_matches,
    stable_hash,
)


FINAL_CANDIDATE_PATH = OUTPUT_DIR / "polished.md"
FAILED_REPAIR_PATH = OUTPUT_DIR / "failed-repair.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-audit repaired content and enforce both final validators"
    )
    parser.add_argument("--refresh", action="store_true")
    return parser.parse_args()


def load_json(path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def matching_record(record: dict, evidence: dict, plan: dict) -> bool:
    return (
        record.get("model") == MODEL
        and record.get("prompt_version") == PROMPT_VERSION
        and record.get("evidence_sha256") == stable_hash(evidence)
        and record.get("plan_sha256") == stable_hash(plan)
    )


def save_factual_record(record: dict) -> None:
    FACTUAL_AUDIT_PATH.write_text(json.dumps(record, indent=2), encoding="utf-8")


def update_repair_record(repair: dict, final_audit: dict) -> None:
    repair["final_factual_audit"] = final_audit
    REPAIR_PATH.write_text(json.dumps(repair, indent=2), encoding="utf-8")


def enforce_final_gate(
    article: str,
    python_report: dict,
    factual_report: dict,
) -> None:
    if python_report.get("passed") and factual_report.get("passed"):
        FAILED_REPAIR_PATH.unlink(missing_ok=True)
        return
    FAILED_REPAIR_PATH.write_text(f"{article}\n", encoding="utf-8")
    failures = []
    if not python_report.get("passed"):
        failures.append(f"Python={len(python_report.get('issues') or [])} issues")
    if not factual_report.get("passed"):
        failures.append(f"Haiku={len(factual_report.get('issues') or [])} issues")
    raise SystemExit(
        "Final article failed the combined validation gate ("
        + ", ".join(failures)
        + f"); see {FAILED_REPAIR_PATH}, {VALIDATION_PATH}, and {FACTUAL_AUDIT_PATH}"
    )


def main() -> None:
    args = parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    ensure_data_directories()
    article = FINAL_CANDIDATE_PATH.read_text(encoding="utf-8").strip()
    evidence = load_json(NORMALIZED_EVIDENCE_PATH)
    plan_record = load_json(PLAN_PATH)
    plan = plan_record.get("plan", plan_record)
    if not isinstance(plan, dict) or not plan:
        raise SystemExit("SEO/AIO/CRO plan is missing; run the plan stage first")
    validation = load_json(VALIDATION_PATH)
    repair = load_json(REPAIR_PATH)
    record = load_json(FACTUAL_AUDIT_PATH)
    if not matching_record(record, evidence, plan) or not record.get("initial"):
        raise SystemExit("Initial combined Haiku audit is missing or stale")

    if not repair.get("repair_called"):
        final_run = record["initial"]
        record["final"] = final_run
        record["final_reused_initial"] = True
        save_factual_record(record)
        update_repair_record(repair, final_run["audit"])
        enforce_final_gate(article, validation["final"], final_run["audit"])
        print("FINAL combined Haiku audit reused initial pass: 0 Haiku calls")
        return

    if (
        not args.refresh
        and cached_run_matches(record.get("final"), article, plan)
    ):
        final_run = record["final"]
        print(
            f"CACHED final Haiku combined audit: "
            f"{'PASSED' if final_run['audit']['passed'] else 'FAILED'}, "
            f"{final_run['audit']['supported_claim_count']}/{final_run['audit']['audited_claim_count']} claims, "
            f"{final_run['audit']['plan_covered_count']}/{final_run['audit']['plan_checked_count']} plan items, "
            f"{final_run['audit']['buyer_question_covered_count']}/{final_run['audit']['buyer_question_checked_count']} buyer questions, "
            f"{final_run['audit']['decision_met_count']}/{final_run['audit']['decision_checked_count']} decision standards, "
            f"{len(final_run['audit']['issues'])} issues"
        )
    else:
        final_run = audit_article(anthropic.Anthropic(), article, evidence, plan)
        record["final"] = final_run
        record["final_reused_initial"] = False
        save_factual_record(record)
        print(
            f"{'PASSED' if final_run['audit']['passed'] else 'FAILED'} final Haiku combined audit: "
            f"{final_run['audit']['supported_claim_count']}/{final_run['audit']['audited_claim_count']} claims, "
            f"{final_run['audit']['plan_covered_count']}/{final_run['audit']['plan_checked_count']} plan items, "
            f"{final_run['audit']['buyer_question_covered_count']}/{final_run['audit']['buyer_question_checked_count']} buyer questions, "
            f"{final_run['audit']['decision_met_count']}/{final_run['audit']['decision_checked_count']} decision standards, "
            f"{len(final_run['audit']['issues'])} issues, "
            f"{final_run['usage']['input_tokens']} input / "
            f"{final_run['usage']['output_tokens']} output tokens"
        )
        for issue in final_run["audit"]["issues"]:
            print(f"- {issue['category']}: {issue['explanation']}")

    update_repair_record(repair, final_run["audit"])
    enforce_final_gate(article, validation["final"], final_run["audit"])
    print("PASSED final Python and Haiku validation gates")


if __name__ == "__main__":
    main()
