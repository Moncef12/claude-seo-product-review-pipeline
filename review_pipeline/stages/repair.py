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
    PROJECT_ROOT,
    REPAIR_PATH,
    VALIDATION_PATH,
    ensure_data_directories,
)
from review_pipeline.factual_validation import stable_hash
from review_pipeline.stages.validate import content_hash, validate_article


MODEL = "claude-sonnet-4-6"
PROMPT_VERSION = "z3fc-conditional-repair-v4-dual-validation"
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


def repair_prompt(article: str, issues: list[dict], evidence: dict) -> str:
    return f"""Repair the article so every listed Python or factual-audit issue is resolved.

VALIDATION ISSUES
{json.dumps(issues, indent=2)}

Rules:
- Use this exact H1 as the first line:
  `# Arzopa Z3FC Review: A 2.5K 180Hz Portable Monitor`
- Keep every existing required H2 heading exactly as supplied and in the same order.
  Never merge, rename, or remove a required heading.
- Keep exactly three FAQs.
- The finished article must contain 950 to 1,050 visible words. This is a strict
  limit. Compress repeated claims and examples before removing useful evidence.
- Use this exact meta-description line:
  `**Meta description:** Arzopa Z3FC review covering its 2.5K 180Hz screen, portable design, gaming performance, connectivity, measured results, limitations, and ideal users.`
- Preserve source provenance and disclose evidence conflicts conservatively.
- Use only URLs and factual claims present in the normalized evidence.
- When a Haiku factual issue includes an exact article quote, correct or remove that
  specific claim using its explanation and evidence IDs.
- Do not make unrelated stylistic rewrites.

NORMALIZED EVIDENCE
{json.dumps(evidence, indent=2)}

ARTICLE TO REPAIR
{article}
"""


def call_sonnet(client, article: str, issues: list[dict], evidence: dict):
    return client.messages.create(
        model=MODEL,
        max_tokens=2600,
        temperature=0,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": repair_prompt(article, issues, evidence),
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
    output.extend(
        {"validator": "haiku_factual_audit", **issue}
        for issue in factual.get("issues") or []
    )
    return output


def issues_hash(issues: list[dict]) -> str:
    return stable_hash(issues)


def cached_repair(
    draft_hash: str,
    brief_hash: str,
    repair_issues_hash: str,
) -> dict | None:
    if not REPAIR_PATH.exists():
        return None
    cached = json.loads(REPAIR_PATH.read_text(encoding="utf-8"))
    expected = (
        draft_hash,
        brief_hash,
        repair_issues_hash,
        MODEL,
        PROMPT_VERSION,
    )
    actual = (
        cached.get("draft_sha256"),
        cached.get("evidence_sha256"),
        cached.get("issues_sha256"),
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
) -> dict:
    repair_called = message is not None
    record = {
        "model": MODEL,
        "prompt_version": PROMPT_VERSION,
        "draft_sha256": draft_hash,
        "evidence_sha256": brief_hash,
        "issues_sha256": issues_hash(issues),
        "repaired_at": datetime.now(timezone.utc).isoformat(),
        "repair_called": repair_called,
        "word_count": final["word_count"],
        "usage": {
            "input_tokens": message.usage.input_tokens if message else 0,
            "output_tokens": message.usage.output_tokens if message else 0,
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
    validation = json.loads(VALIDATION_PATH.read_text(encoding="utf-8"))
    factual_record = json.loads(FACTUAL_AUDIT_PATH.read_text(encoding="utf-8"))
    initial = validation["initial"]
    initial_factual_run = factual_record.get("initial") or {}
    initial_factual = initial_factual_run.get("audit") or {}
    draft_hash = content_hash(article)
    brief_hash = evidence_hash(evidence)
    if initial_factual_run.get("article_sha256") != stable_hash(article):
        raise SystemExit("Initial factual audit is stale for the current draft")
    if factual_record.get("evidence_sha256") != stable_hash(evidence):
        raise SystemExit("Initial factual audit is stale for the current evidence")
    combined_issues = repair_issues(initial, initial_factual)
    combined_issues_hash = issues_hash(combined_issues)

    cached = (
        None
        if args.refresh
        else cached_repair(draft_hash, brief_hash, combined_issues_hash)
    )
    if cached:
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
        )
        print(f"NO REPAIR NEEDED: {record['word_count']} words, 0 Sonnet calls")
        return

    prompt = repair_prompt(article, combined_issues, evidence)
    message = call_sonnet(
        anthropic.Anthropic(),
        article,
        combined_issues,
        evidence,
    )
    if message.stop_reason != "end_turn":
        raise RuntimeError(f"Sonnet repair stopped early: {message.stop_reason}")
    repaired = message_text(message)
    final = validate_article(repaired)
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
    )
    print(
        f"REPAIRED with one Sonnet call: {record['word_count']} words, "
        f"{record['usage']['input_tokens']} input / "
        f"{record['usage']['output_tokens']} output tokens; "
        f"Python validation {'passed' if final['passed'] else 'failed'}"
    )


if __name__ == "__main__":
    main()
