"""Structured Haiku audit for factual grounding against normalized evidence."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

import anthropic

from review_pipeline.config import HAIKU_MODEL


MODEL = HAIKU_MODEL
PROMPT_VERSION = "z3fc-factual-audit-v6-claim-matrix"

SYSTEM_PROMPT = """You are a strict factual-grounding auditor.
Audit an article only against the supplied normalized evidence. Do not use outside
knowledge. Check every factual assertion, number, product specification, source
attribution, and treatment of conflicting evidence. Report only factual-grounding
problems, not style preferences. Return valid JSON matching the supplied schema."""

AUDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "claim_checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "article_quote": {"type": "string"},
                    "verdict": {
                        "type": "string",
                        "enum": [
                            "supported",
                            "unsupported_claim",
                            "contradiction",
                            "unverified_number",
                            "source_misattribution",
                            "claim_status_overstatement",
                            "missing_conflict_disclosure",
                        ],
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["none", "critical", "major"],
                    },
                    "explanation": {"type": "string"},
                    "evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "suggested_correction": {"type": "string"},
                },
                "required": [
                    "index",
                    "article_quote",
                    "verdict",
                    "severity",
                    "explanation",
                    "evidence_ids",
                    "suggested_correction",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["claim_checks"],
    "additionalProperties": False,
}

ISSUE_CATEGORIES = {
    "unsupported_claim",
    "contradiction",
    "unverified_number",
    "source_misattribution",
    "claim_status_overstatement",
    "missing_conflict_disclosure",
}
ISSUE_SEVERITIES = {"critical", "major"}


def stable_hash(value: Any) -> str:
    if isinstance(value, str):
        encoded = value.encode("utf-8")
    else:
        encoded = json.dumps(value, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def audit_prompt(article: str, evidence: dict) -> str:
    return f"""Audit every factual assertion in the article against the normalized
evidence. Return exactly one JSON object matching the structured-output schema.

Coverage contract:
- Return one claim_checks row for every distinct factual assertion in the title,
  metadata, prose, tables, bullets, and FAQs. Do not output only failures.
- Use consecutive index values starting at 1 in article order.
- Quote the smallest visible article span that contains the assertion. Visible link
  text may be quoted without the Markdown URL.
- Use verdict `supported` and severity `none` when the assertion is grounded.
- For supported rows, briefly name the evidence IDs or conflict position that
  grounds the assertion and leave suggested_correction as an empty string.
- For every other verdict, use severity `major` or `critical` and provide the exact
  factual correction. These rows block publication.

Grounding rules:
- Use only the supplied evidence. Do not validate claims from general knowledge.
- Split prose, bullets, tables, the title, and metadata into atomic factual claims.
- A claim passes when it is directly stated by, or conservatively entailed by, one
  or more evidence claims.
- Treat each evidence claim's status as authoritative. A `manufacturer_claim`
  remains a manufacturer claim even when several publishers repeat it. Publishers
  in the `sources` array reported the claim; they did not necessarily measure or
  independently confirm it.
- Treat positions and editorial guidance inside `conflicts` as first-class evidence,
  even though conflict positions do not have claim IDs.
- Recommendations may synthesize supported strengths, weaknesses, compatibility,
  and purchase advice. Do not require an identical sentence in the evidence.
- Flag any invented feature, specification, measurement, comparison, or use case.
- Flag every number or range that is not supported by the evidence.
- Flag a source attribution when that publisher does not support the attributed
  claim.
- Flag manufacturer claims or reviewer observations presented as independently
  measured facts.
- Conversely, do not flag a specification when the article clearly labels it as a
  manufacturer claim. An extra inline publisher attribution is not required.
- Accept cautious limitations such as "no verified tier" or "the evidence does not
  establish connection-specific support" when the normalized evidence contains no
  such detail. Absence-of-evidence qualifiers are not contradictions.
- Never suggest wording such as "confirmed by reviewers" for a
  `manufacturer_claim` unless a separate `observed` or `measured` evidence item
  supports that stronger status.
- Flag a disputed value presented as settled when the evidence records a conflict.
- A numeric range is supported when its endpoints are supported; it does not need
  to list every intermediate measurement. A concise conflict summary need not name
  every publisher unless it claims to be exhaustive or hides the disagreement.
- Do not flag headings, transitions, cautious editorial phrasing, or ordinary
  product-category descriptions unless they introduce a factual assertion.
- Report only substantive factual errors that require a correction. Do not fail an
  article merely because more attribution or qualification could optionally be
  added when its existing wording is already accurate.
- Before adding an issue, perform a false-positive check. If your explanation would
  say the quoted wording is accurate, acceptable, appropriate, or already correctly
  qualified, do not report the issue. Never report optional attribution improvements.
- Use evidence_ids from the normalized claims whenever relevant. An unsupported
  claim may have an empty evidence_ids list when no evidence claim supports it.
- Do not return aggregate pass or count fields. Python derives them from the complete
  claim_checks matrix.

Calibration examples for this evidence format:
- PASS: "AMD FreeSync is supported, though no verified tier or connection-specific
  guarantee is available from the evidence" when FreeSync is only a
  `manufacturer_claim` and no tier or connection guarantee appears.
- PASS: "The manufacturer claims a 9ms pixel response time" when the 9ms item has
  status `manufacturer_claim`. An inline publisher citation is optional.
- PASS: a statement paraphrasing a publisher position recorded inside `conflicts`.
- PASS: "TechRadar confirmed compatibility" when TechRadar directly observed the
  device working and the article preserves the recorded conditions.
- FAIL: "FreeSync Premium is supported" when no FreeSync tier is in the evidence.
- FAIL: "Reviewers measured a 9ms response time" when 9ms is only a manufacturer
  claim.
- FAIL: unconditional Switch compatibility when the evidence requires a dock and
  external power.

NORMALIZED EVIDENCE
{json.dumps(evidence, indent=2)}

ARTICLE TO AUDIT
{article}
"""


def call_haiku(client: anthropic.Anthropic, article: str, evidence: dict):
    return client.messages.create(
        model=MODEL,
        max_tokens=7000,
        temperature=0,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": audit_prompt(article, evidence)}],
        extra_body={
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": AUDIT_SCHEMA,
                }
            }
        },
    )


def message_text(message) -> str:
    return "".join(
        block.text for block in message.content if block.type == "text"
    ).strip()


def parse_json(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("Haiku factual audit did not contain a JSON object")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("Haiku factual audit must be a JSON object")
    return value


def evidence_claim_ids(evidence: dict) -> set[str]:
    return {
        str(claim.get("id"))
        for claim in evidence.get("claims", [])
        if claim.get("id")
    }


def visible_text(value: str) -> str:
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", value)
    text = re.sub(r"[*_`#>]", "", text)
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().casefold()


def self_exonerating_issue(issue: dict) -> bool:
    text = " ".join(
        str(issue.get(field) or "")
        for field in ("explanation", "suggested_correction")
    ).casefold()
    direct_phrases = (
        "this is not an issue",
        "no correction needed",
        "correct and supported",
        "which is accurate",
        "already accurate",
        "properly attributed",
    )
    if any(phrase in text for phrase in direct_phrases):
        return True
    return bool(
        re.search(
            r"\b(?:wording|phrasing|claim|article|statement)\b.{0,40}"
            r"\b(?:is|was|appears|presents)\b.{0,12}"
            r"\b(?:accurate|acceptable|appropriate|correct)\b",
            text,
        )
    )


def discard_self_exonerating_issues(audit: dict) -> dict:
    """Remove rows whose own explanation says no factual problem exists."""

    issues = audit.get("issues")
    if not isinstance(issues, list):
        return audit
    discarded = [issue for issue in issues if self_exonerating_issue(issue)]
    if not discarded:
        return audit
    output = dict(audit)
    output["issues"] = [issue for issue in issues if issue not in discarded]
    output["discarded_self_exonerating_issues"] = discarded
    audited = output.get("audited_claim_count")
    supported = output.get("supported_claim_count")
    if isinstance(audited, int) and isinstance(supported, int):
        output["supported_claim_count"] = min(audited, supported + len(discarded))
    if not output["issues"] and isinstance(audited, int):
        output["supported_claim_count"] = audited
    output["passed"] = not output["issues"]
    return output


def summarize_claim_checks(payload: dict, evidence: dict, article: str) -> dict:
    """Validate the exhaustive claim matrix and derive the blocking audit report."""

    checks = payload.get("claim_checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("Haiku factual audit returned no claim checks")
    known_ids = evidence_claim_ids(evidence)
    normalized_checks = []
    discarded = []
    for expected_index, raw_check in enumerate(checks, start=1):
        if not isinstance(raw_check, dict):
            raise ValueError("Haiku factual claim check must be an object")
        check = dict(raw_check)
        if check.get("index") != expected_index:
            raise ValueError("Haiku factual claim-check indexes must be consecutive")
        quote = str(check.get("article_quote") or "").strip()
        explanation = str(check.get("explanation") or "").strip()
        if not quote or not explanation:
            raise ValueError("Haiku factual claim check is missing its quote or explanation")
        check["quote_verified"] = visible_text(quote) in visible_text(article)
        unknown = set(check.get("evidence_ids") or []) - known_ids
        if unknown:
            raise ValueError(f"Haiku factual audit used unknown evidence IDs: {unknown}")

        verdict = check.get("verdict")
        if verdict != "supported" and verdict not in ISSUE_CATEGORIES:
            raise ValueError("Haiku factual claim check used an invalid verdict")
        if verdict != "supported" and self_exonerating_issue(check):
            discarded.append(dict(check))
            check["verdict"] = "supported"
            check["severity"] = "none"
            check["suggested_correction"] = ""
            verdict = "supported"
        if verdict == "supported":
            if check.get("severity") != "none":
                raise ValueError("Supported factual claim must have severity none")
        else:
            if check.get("severity") not in ISSUE_SEVERITIES:
                raise ValueError("Blocking factual claim used an invalid severity")
            if not str(check.get("suggested_correction") or "").strip():
                raise ValueError("Blocking factual claim is missing a correction")
        normalized_checks.append(check)

    issues = [
        {
            "category": check["verdict"],
            "severity": check["severity"],
            "article_quote": check["article_quote"],
            "explanation": check["explanation"],
            "evidence_ids": check.get("evidence_ids") or [],
            "suggested_correction": check["suggested_correction"],
            "quote_verified": check["quote_verified"],
        }
        for check in normalized_checks
        if check["verdict"] != "supported"
    ]
    output = {
        "passed": not issues,
        "audited_claim_count": len(normalized_checks),
        "supported_claim_count": len(normalized_checks) - len(issues),
        "issues": issues,
        "claim_checks": normalized_checks,
    }
    if discarded:
        output["discarded_self_exonerating_checks"] = discarded
    return validate_audit(output, evidence, article)


def validate_audit(
    audit: dict,
    evidence: dict,
    article: str | None = None,
) -> dict:
    issues = audit.get("issues")
    audited = audit.get("audited_claim_count")
    supported = audit.get("supported_claim_count")
    if not isinstance(issues, list):
        raise ValueError("Haiku factual audit issues must be a list")
    if not isinstance(audited, int) or isinstance(audited, bool) or audited <= 0:
        raise ValueError("Haiku factual audit returned an invalid audited claim count")
    if (
        not isinstance(supported, int)
        or isinstance(supported, bool)
        or not 0 <= supported <= audited
    ):
        raise ValueError("Haiku factual audit returned an invalid supported claim count")
    if bool(audit.get("passed")) != (not issues):
        raise ValueError("Haiku factual audit pass flag contradicts its issues")
    if not issues and supported != audited:
        raise ValueError("A passing factual audit must support every audited claim")
    if issues and supported >= audited:
        raise ValueError("A failing factual audit must contain an unsupported claim")

    known_ids = evidence_claim_ids(evidence)
    for issue in issues:
        if not isinstance(issue, dict):
            raise ValueError("Haiku factual audit issue must be an object")
        if issue.get("category") not in ISSUE_CATEGORIES:
            raise ValueError("Haiku factual audit used an invalid issue category")
        if issue.get("severity") not in ISSUE_SEVERITIES:
            raise ValueError("Haiku factual audit used an invalid issue severity")
        unknown = set(issue.get("evidence_ids") or []) - known_ids
        if unknown:
            raise ValueError(f"Haiku factual audit used unknown evidence IDs: {unknown}")
        for field in ("article_quote", "explanation", "suggested_correction"):
            if not str(issue.get(field) or "").strip():
                raise ValueError(f"Haiku factual audit issue is missing {field}")
    return audit


def audit_article(client: anthropic.Anthropic, article: str, evidence: dict) -> dict:
    prompt = audit_prompt(article, evidence)
    message = call_haiku(client, article, evidence)
    if message.stop_reason != "end_turn":
        raise RuntimeError(f"Haiku factual audit stopped early: {message.stop_reason}")
    raw_response = message_text(message)
    audit = summarize_claim_checks(parse_json(raw_response), evidence, article)
    return {
        "article_sha256": stable_hash(article),
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "usage": {
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
        },
        "system_prompt": SYSTEM_PROMPT,
        "prompt": prompt,
        "raw_response": raw_response,
        "audit": audit,
    }


def cached_run_matches(run: Any, article: str) -> bool:
    return isinstance(run, dict) and run.get("article_sha256") == stable_hash(article)


__all__ = [
    "MODEL",
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "AUDIT_SCHEMA",
    "audit_article",
    "audit_prompt",
    "cached_run_matches",
    "discard_self_exonerating_issues",
    "evidence_claim_ids",
    "stable_hash",
    "summarize_claim_checks",
    "validate_audit",
    "visible_text",
]
