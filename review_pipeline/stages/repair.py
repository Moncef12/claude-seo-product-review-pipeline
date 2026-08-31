import argparse
import hashlib
import json
from datetime import datetime, timezone

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
from review_pipeline.factual_validation import stable_hash
from review_pipeline.stages.validate import content_hash, validate_article


MODEL = "claude-sonnet-4-6"
PROMPT_VERSION = "z3fc-conditional-repair-v7-section-budgets"
DRAFT_PATH = OUTPUT_DIR / "draft.md"
FINAL_CANDIDATE_PATH = OUTPUT_DIR / "polished.md"

SYSTEM_PROMPT = """You repair an evidence-based product review.
Change only what is necessary to satisfy the supplied Python and factual-grounding
failures while preserving supported facts and source links. Never add first-hand
experience, prices, or unsupported claims. Do not use em dashes or double hyphens.
Return the complete publish-ready Markdown article only."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Make at most one conditional Sonnet repair call"
    )
    parser.add_argument("--refresh", action="store_true")
    return parser.parse_args()


def evidence_hash(evidence: dict) -> str:
    encoded = json.dumps(evidence, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def plan_hash(plan: dict) -> str:
    return hashlib.sha256(json.dumps(plan or {}, sort_keys=True).encode("utf-8")).hexdigest()


def repair_prompt(article: str, issues: list[dict], evidence: dict, plan: dict | None = None) -> str:
    return f"""Repair the article so every listed Python, factual-audit, or
plan-adherence issue is resolved.

VALIDATION ISSUES
{json.dumps(issues, indent=2)}

Rules:
- Begin with one H1 containing "Arzopa", "Z3FC", and "Review". Do not imply
  first-hand testing in the title and do not add unsupported product facts.
- Keep every existing required H2 heading exactly as supplied and in the same order.
  Never merge, rename, or remove a required heading.
- Keep exactly three FAQs.
- The finished article must contain 850 to 950 visible words. This is a strict
  limit. If the supplied validation reports excess words, delete at least the
  reported excess plus 150 visible words. Remove repeated facts, examples, and
  modifiers; never expand another section to compensate.
- Quick Verdict: 40-50 words. Five-row snapshot values: under 12 words. Pros and
  Cons: six total bullets, each under 14 words. Prose sections: 45-65 words each.
  FAQ answers: at most 25 words. These compact budgets take priority over preserving
  repeated detail.
- Use this exact meta-description line:
  `**Meta description:** Arzopa Z3FC review covering its 2.5K 180Hz screen, portable design, gaming performance, connectivity, measured results, limitations, and ideal users.`
- Preserve source provenance and disclose evidence conflicts conservatively.
- Use only URLs and factual claims present in the normalized evidence.
- Remove all first-hand testing language, including positive or negative uses of
  `hands-on`. Describe the methodology as an evidence synthesis, not an original
  product test.
- Quick Verdict must explicitly contain `recommend`, `recommended`, `worth`,
  `buy`, or `avoid` as part of a supported conditional decision.
- The last sentence of Final Verdict must contain `consider`, `choose`, `check`,
  or `buy`. Never mention current price/pricing, stock, availability, urgency, or
  an external/retailer link in that sentence.
- When a Haiku factual issue includes an exact article quote, correct or remove that
  specific claim using its explanation and evidence IDs.
- When a Haiku plan issue is partial or missing, add only the requested essential
  coverage and keep it grounded in the supplied evidence.
- When a Haiku buyer-question issue is partial or missing, answer that specific
  question somewhere natural in the article. Do not duplicate an existing answer
  merely to force it into the FAQ.
- When a Haiku editorial/commercial issue fails, improve only the requested buyer
  decision, trade-off, objection, value framing, or next step. Never weaken a
  limitation or invent a commercial fact to make the article more persuasive.
- Do not make unrelated stylistic rewrites.

EDITORIAL / SEO / AIO / CRO PLANNING BRIEF
{json.dumps(plan or {}, indent=2)}

Preserve plan-aligned intent, reader-fit decisions, evidence-grounded objections,
and natural CTA placement when supported by the normalized evidence. The plan is
not a source of product facts; evidence overrides it.

NORMALIZED EVIDENCE
{json.dumps(evidence, indent=2)}

ARTICLE TO REPAIR
{article}
"""


def call_sonnet(client, article: str, issues: list[dict], evidence: dict, plan: dict | None = None):
    return client.messages.create(
        model=MODEL,
        max_tokens=2600,
        temperature=0,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": repair_prompt(article, issues, evidence, plan),
            }
        ],
    )


def message_text(message) -> str:
    return "".join(
        block.text for block in message.content if block.type == "text"
    ).strip()


def repair_issues(mechanical: dict, factual: dict) -> list[dict]:
    output = [
        {"validator": "python", **issue}
        for issue in mechanical.get("issues") or []
    ]
    for issue in factual.get("issues") or []:
        category = str(issue.get("category") or "") if isinstance(issue, dict) else ""
        validator = (
            "haiku_buyer_question_audit"
            if category.startswith("buyer_question_")
            else "haiku_plan_audit"
            if category.startswith("plan_")
            else "haiku_editorial_commercial_audit"
            if category.startswith("decision_")
            else "haiku_factual_audit"
        )
        output.append({"validator": validator, **issue})
    return output


def issues_hash(issues: list[dict]) -> str:
    return stable_hash(issues)


def cached_repair(
    draft_hash: str,
    brief_hash: str,
    repair_issues_hash: str,
    editorial_plan_hash: str | dict | None = None,
) -> dict | None:
    if not REPAIR_PATH.exists():
        return None
    cached = json.loads(REPAIR_PATH.read_text(encoding="utf-8"))
    if isinstance(editorial_plan_hash, dict):
        editorial_plan_hash = plan_hash(editorial_plan_hash)
    expected = (
        draft_hash,
        brief_hash,
        repair_issues_hash,
        editorial_plan_hash or plan_hash({}),
        MODEL,
        PROMPT_VERSION,
    )
    actual = (
        cached.get("draft_sha256"),
        cached.get("evidence_sha256"),
        cached.get("issues_sha256"),
        cached.get("plan_sha256", plan_hash({})),
        cached.get("model"),
        cached.get("prompt_version"),
    )
    return cached if actual == expected else None


def validation_output(
    draft_hash: str,
    initial: dict,
    final: dict,
    repair_called: bool,
) -> dict:
    return {
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_sha256": draft_hash,
        "repair_called": repair_called,
        "initial": initial,
        "final": final,
    }


def save_record(
    article: str,
    draft_hash: str,
    brief_hash: str,
    initial: dict,
    initial_factual: dict,
    issues: list[dict],
    final: dict,
    message=None,
    prompt: str | None = None,
    editorial_plan_hash: str | None = None,
) -> dict:
    repair_called = message is not None
    usage = getattr(message, "usage", {}) if message else {}
    input_tokens = usage.get("input_tokens", 0) if isinstance(usage, dict) else getattr(usage, "input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0) if isinstance(usage, dict) else getattr(usage, "output_tokens", 0)
    record = {
        "model": MODEL,
        "prompt_version": PROMPT_VERSION,
        "cached": False,
        "last_run_cache_hit": False,
        "call_count": 1 if repair_called else 0,
        "draft_sha256": draft_hash,
        "evidence_sha256": brief_hash,
        "plan_sha256": editorial_plan_hash or plan_hash({}),
        "issues_sha256": issues_hash(issues),
        "repaired_at": datetime.now(timezone.utc).isoformat(),
        "repair_called": repair_called,
        "word_count": final["word_count"],
        "usage": {
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
        },
        "system_prompt": SYSTEM_PROMPT if repair_called else None,
        "prompt": prompt,
        "repair_issues": issues,
        "initial_validation": initial,
        "initial_factual_audit": initial_factual,
        "final_validation": final,
        "article": article,
    }
    REPAIR_PATH.write_text(json.dumps(record, indent=2), encoding="utf-8")
    VALIDATION_PATH.write_text(
        json.dumps(
            validation_output(draft_hash, initial, final, repair_called),
            indent=2,
        ),
        encoding="utf-8",
    )
    FINAL_CANDIDATE_PATH.write_text(f"{article}\n", encoding="utf-8")
    return record


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
    validation = json.loads(VALIDATION_PATH.read_text(encoding="utf-8"))
    factual_record = json.loads(FACTUAL_AUDIT_PATH.read_text(encoding="utf-8"))
    initial = validation["initial"]
    initial_factual_run = factual_record.get("initial") or {}
    initial_factual = initial_factual_run.get("audit") or {}
    draft_hash = content_hash(article)
    brief_hash = evidence_hash(evidence)
    editorial_plan_hash = plan_hash(plan)
    if initial_factual_run.get("article_sha256") != stable_hash(article):
        raise SystemExit("Initial factual audit is stale for the current draft")
    if factual_record.get("evidence_sha256") != stable_hash(evidence):
        raise SystemExit("Initial factual audit is stale for the current evidence")
    if factual_record.get("plan_sha256") != stable_hash(plan):
        raise SystemExit("Initial plan audit is stale for the current plan")
    combined_issues = repair_issues(initial, initial_factual)
    combined_issues_hash = issues_hash(combined_issues)

    cached = (
        None
        if args.refresh
        else cached_repair(draft_hash, brief_hash, combined_issues_hash, editorial_plan_hash)
    )
    if cached:
        cached["cached"] = True
        cached["last_run_cache_hit"] = True
        # The repair decision can remain cached when the issue list is unchanged,
        # but the trace must always show the current combined Haiku audit. This is
        # especially important when an audit prompt adds a new passing rubric.
        cached["initial_factual_audit"] = initial_factual
        if "call_count" not in cached or int(cached.get("call_count") or 0) == 0:
            usage = cached.get("usage") or {}
            if cached.get("repair_called") or int(usage.get("input_tokens") or 0) or int(usage.get("output_tokens") or 0):
                cached["call_count"] = 1
        REPAIR_PATH.write_text(json.dumps(cached, indent=2), encoding="utf-8")
        FINAL_CANDIDATE_PATH.write_text(f"{cached['article']}\n", encoding="utf-8")
        VALIDATION_PATH.write_text(
            json.dumps(
                validation_output(
                    draft_hash,
                    cached["initial_validation"],
                    cached["final_validation"],
                    cached["repair_called"],
                ),
                indent=2,
            ),
            encoding="utf-8",
        )
        print(
            f"CACHED {'repaired' if cached['repair_called'] else 'validated'} review: "
            f"{cached['word_count']} words"
        )
        return

    if initial["passed"] and initial_factual.get("passed"):
        record = save_record(
            article,
            draft_hash,
            brief_hash,
            initial,
            initial_factual,
            combined_issues,
            initial,
            editorial_plan_hash=editorial_plan_hash,
        )
        print(f"NO REPAIR NEEDED: {record['word_count']} words, 0 Sonnet calls")
        return

    prompt = repair_prompt(article, combined_issues, evidence, plan)
    message = call_sonnet(
        anthropic.Anthropic(),
        article,
        combined_issues,
        evidence,
        plan,
    )
    if message.stop_reason != "end_turn":
        raise RuntimeError(f"Sonnet repair stopped early: {message.stop_reason}")
    repaired = message_text(message)
    final = validate_article(repaired, editorial_plan=plan)
    record = save_record(
        repaired,
        draft_hash,
        brief_hash,
        initial,
        initial_factual,
        combined_issues,
        final,
        message,
        prompt,
        editorial_plan_hash,
    )
    print(
        f"REPAIRED with one Sonnet call: {record['word_count']} words, "
        f"{record['usage']['input_tokens']} input / "
        f"{record['usage']['output_tokens']} output tokens; "
        f"Python validation {'passed' if final['passed'] else 'failed'}"
    )


if __name__ == "__main__":
    main()
