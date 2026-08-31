"""Structured Haiku audit for grounding, plan adherence, and decision quality."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

import anthropic

from review_pipeline.config import HAIKU_MODEL


MODEL = HAIKU_MODEL
PROMPT_VERSION = "z3fc-evidence-plan-buyer-decision-audit-v10"

SYSTEM_PROMPT = """You are a strict factual-grounding and content-brief auditor.
Audit factual claims only against the supplied normalized evidence; never use
outside knowledge. Separately check whether the article executes each supplied
essential SEO/AIO/CRO plan requirement. Also assess whether it makes useful,
evidence-constrained editorial and commercial decisions instead of merely listing
features. The plan is editorial direction, never a source of product facts. Report
substantive grounding, brief-adherence, or decision-quality failures, not style
preferences. Return valid JSON matching the supplied schema."""

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
        "plan_checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["covered", "partially_covered", "missing"],
                    },
                    "article_quote": {"type": "string"},
                    "explanation": {"type": "string"},
                    "suggested_correction": {"type": "string"},
                },
                "required": [
                    "id",
                    "status",
                    "article_quote",
                    "explanation",
                    "suggested_correction",
                ],
                "additionalProperties": False,
            },
        },
        "buyer_question_checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["covered", "partially_covered", "missing"],
                    },
                    "article_quote": {"type": "string"},
                    "explanation": {"type": "string"},
                    "suggested_correction": {"type": "string"},
                },
                "required": [
                    "id",
                    "status",
                    "article_quote",
                    "explanation",
                    "suggested_correction",
                ],
                "additionalProperties": False,
            },
        },
        "decision_checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["met", "partially_met", "missing"],
                    },
                    "article_quote": {"type": "string"},
                    "explanation": {"type": "string"},
                    "evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "suggested_correction": {"type": "string"},
                },
                "required": [
                    "id",
                    "status",
                    "article_quote",
                    "explanation",
                    "evidence_ids",
                    "suggested_correction",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "claim_checks",
        "plan_checks",
        "buyer_question_checks",
        "decision_checks",
    ],
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
PLAN_FAILURE_STATUSES = {"partially_covered", "missing"}
DECISION_FAILURE_STATUSES = {"partially_met", "missing"}


def essential_plan_requirements(plan: dict | None) -> list[dict]:
    """Create a stable checklist from the plan's executable recommendations.

    SERP observations, content-gap ideas, and the ordered outline are useful
    planning inputs but are not all mandatory article requirements. The outline
    is already checked deterministically. This checklist focuses the model audit
    on intent, editorial decisions, direct answers, objections, and conversion.
    """

    value = plan if isinstance(plan, dict) else {}
    if isinstance(value.get("plan"), dict):
        value = value["plan"]
    requirements: list[dict] = []

    def add(area: str, recommendation: Any, evidence_ids: Any = None) -> None:
        text = str(recommendation or "").strip()
        if not text:
            return
        requirements.append(
            {
                "id": f"P{len(requirements) + 1:02d}",
                "area": area,
                "recommendation": text,
                "evidence_ids": [str(item) for item in (evidence_ids or []) if item],
            }
        )

    add("search_intent", value.get("primary_intent"))
    add("article_angle", value.get("article_angle"))
    for item in value.get("editorial_decisions") or []:
        if isinstance(item, dict):
            add("editorial_decision", item.get("decision"), item.get("evidence_ids"))
    for item in value.get("aio_direct_answer_targets") or []:
        if isinstance(item, dict):
            question = str(item.get("question") or "").strip()
            direction = str(item.get("answer_direction") or "").strip()
            add(
                "aio_direct_answer",
                f"Answer '{question}' with this direction: {direction}",
                item.get("evidence_ids"),
            )
    for item in value.get("cro_buyer_objections") or []:
        if isinstance(item, dict):
            objection = str(item.get("objection") or "").strip()
            response = str(item.get("response_direction") or "").strip()
            add(
                "cro_objection",
                f"Address '{objection}' with this direction: {response}",
                item.get("evidence_ids"),
            )
    if str(value.get("cta_placement") or "").strip():
        add(
            "cro_conversion_cue",
            "End Final Verdict with a natural next-step cue for readers whose needs "
            "fit the product. Judge purpose and placement semantically; equivalent "
            "wording is covered and exact plan phrasing is never required.",
        )
    return requirements


def buyer_question_requirements(plan: dict | None) -> list[dict]:
    """Return every explicit buyer question as its own dynamic coverage check."""

    value = plan if isinstance(plan, dict) else {}
    if isinstance(value.get("plan"), dict):
        value = value["plan"]
    requirements: list[dict] = []
    for item in value.get("buyer_questions") or []:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        requirements.append(
            {
                "id": f"B{len(requirements) + 1:02d}",
                "area": "buyer_question",
                "question": question,
                "recommendation": f"Answer this buyer question directly: {question}",
                "evidence_ids": [
                    str(identifier)
                    for identifier in item.get("evidence_ids") or []
                    if identifier
                ],
            }
        )
    return requirements


def editorial_commercial_requirements(plan: dict | None) -> list[dict]:
    """Return a stable, product-agnostic test for client-facing decision quality.

    These requirements deliberately test the quality of the article's decisions,
    rather than repeating the SEO/AIO/CRO checklist. Factual grounding remains a
    separate hard gate, so commercial usefulness can never excuse invented facts.
    """

    value = plan if isinstance(plan, dict) else {}
    if isinstance(value.get("plan"), dict):
        value = value["plan"]

    def ids_from(key: str) -> list[str]:
        identifiers: list[str] = []
        for item in value.get(key) or []:
            if not isinstance(item, dict):
                continue
            for identifier in item.get("evidence_ids") or []:
                text = str(identifier or "").strip()
                if text and text not in identifiers:
                    identifiers.append(text)
        return identifiers

    editorial_ids = ids_from("editorial_decisions")
    objection_ids = ids_from("cro_buyer_objections")
    direct_answer_ids = ids_from("aio_direct_answer_targets")
    broad_ids = list(dict.fromkeys([*editorial_ids, *objection_ids, *direct_answer_ids]))

    return [
        {
            "id": "D01",
            "area": "purchase_recommendation",
            "requirement": (
                "Give a clear conditional purchase recommendation that identifies "
                "the best-fit buyer and the most important compromise."
            ),
            "evidence_ids": broad_ids,
        },
        {
            "id": "D02",
            "area": "buyer_segmentation",
            "requirement": (
                "Distinguish who should buy from who should avoid the product using "
                "concrete needs, workflows, or constraints rather than generic labels."
            ),
            "evidence_ids": broad_ids,
        },
        {
            "id": "D03",
            "area": "tradeoff_prioritization",
            "requirement": (
                "Prioritize the decision-critical strengths and limitations, explain "
                "their buyer impact, and avoid treating the article as a feature dump."
            ),
            "evidence_ids": editorial_ids or broad_ids,
        },
        {
            "id": "D04",
            "area": "objection_handling",
            "requirement": (
                "Address the material buyer objections honestly and preserve important "
                "limitations instead of minimizing them to increase conversion."
            ),
            "evidence_ids": objection_ids or broad_ids,
        },
        {
            "id": "D05",
            "area": "commercial_value_framing",
            "requirement": (
                "Frame value or relative merit from supported performance and use-case "
                "fit, without invented pricing, availability, urgency, or competitor claims."
            ),
            "evidence_ids": broad_ids,
        },
        {
            "id": "D06",
            "area": "fit_based_next_step",
            "requirement": (
                "End with a natural, non-manipulative next step tied to reader fit after "
                "presenting both reasons to buy and reasons to avoid."
            ),
            "evidence_ids": broad_ids,
        },
    ]


def stable_hash(value: Any) -> str:
    if isinstance(value, str):
        encoded = value.encode("utf-8")
    else:
        encoded = json.dumps(value, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def audit_prompt(
    article: str,
    evidence: dict,
    plan_requirements: list[dict] | None = None,
    buyer_questions: list[dict] | None = None,
    decision_requirements: list[dict] | None = None,
) -> str:
    requirements = plan_requirements or []
    questions = buyer_questions or []
    decisions = decision_requirements or []
    return f"""Audit every factual assertion in the article against the normalized
evidence, then audit its execution of every essential plan requirement and every
editorial/commercial decision requirement. Return exactly one JSON object matching
the structured-output schema.

Coverage contract:
- Return one claim_checks row for every distinct factual assertion in the title,
  metadata, prose, tables, bullets, and FAQs. Do not output only failures.
- Audit the factual premises behind the quick verdict, overall recommendation,
  best-for and avoid-if guidance, buyer-fit statements, buyer objections,
  compromise/value judgments, comparisons, and every commercial call to action.
  Editorial opinion may synthesize supported evidence, but its factual premises
  must still be represented by supported evidence IDs.
- Use consecutive index values starting at 1 in article order.
- Quote the smallest visible article span that contains the assertion. Visible link
  text may be quoted without the Markdown URL. Keep the quote to 25 words or fewer.
- Use verdict `supported` and severity `none` when the assertion is grounded.
- For supported rows, briefly name the evidence IDs or conflict position that
  grounds the assertion in no more than 18 words and leave suggested_correction
  as an empty string.
- For every other verdict, use severity `major` or `critical` and provide the exact
  factual correction in no more than 25 words. These rows block publication.

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

Plan-adherence contract:
- Return exactly one plan_checks row for each supplied requirement, in the same
  order, copying its P01/P02/... ID exactly.
- Use `covered` only when the article substantively executes the full requirement.
- Use `partially_covered` when the article executes only part of the requirement,
  and `missing` when it does not execute it.
- For covered or partial rows, quote the smallest exact visible article span that
  demonstrates the coverage. Keep the quote to 25 words or fewer.
- For missing rows, use an empty article_quote. For every partial or missing row,
  give one specific correction that would satisfy the plan without adding facts.
- Plan wording is not evidence. Any suggested addition must remain supportable by
  normalized evidence. Evidence constraints override plan wording.
- Judge semantic coverage, not keyword repetition, heading names, or stylistic
  similarity. A requirement may be covered across nearby sentences.
- Do not fail optional SERP observations, content-gap brainstorming, or outline
  ordering: they were deliberately excluded from the essential checklist.
- Do not return aggregate pass or count fields. Python derives them from plan_checks.

Buyer-question coverage contract:
- Return exactly one buyer_question_checks row for every supplied B01/B02/...
  question, in the same order and copying its ID exactly.
- Judge whether the full article gives a direct, useful, evidence-constrained answer.
  The answer may appear in the FAQ, a body section, or both; do not require a
  particular heading or duplicate an answer merely to place it in the FAQ.
- Use `covered` only when the question is substantively answered,
  `partially_covered` when the answer omits a decision-important part, and
  `missing` when no useful answer appears.
- For covered or partial rows, quote the smallest exact visible article span that
  demonstrates the answer, no more than 35 words. Use an empty quote when missing.
- Question wording and SERP context are not product evidence. The supplied evidence
  IDs identify the allowed factual basis, and the factual gate remains authoritative.
- For partial or missing rows, give one precise correction grounded in the supplied
  evidence. Leave suggested_correction empty for covered rows.
- Do not return aggregate fields. Python derives them from buyer_question_checks.

Editorial/commercial decision contract:
- Return exactly one decision_checks row for each supplied D01/D02/... requirement,
  in the same order and copying its ID exactly.
- This is a decision-usefulness test, not a prose-style score. Judge whether the
  article helps a real buyer choose, avoid, or qualify the product using supported
  trade-offs, objections, value framing, and a proportionate next step.
- Use `met` only when the full requirement is substantively satisfied,
  `partially_met` when only part is satisfied, and `missing` when it is absent.
- Quote the smallest exact visible article span demonstrating the decision, no more
  than 35 words. A requirement may be satisfied across adjacent sentences.
- Return the normalized evidence IDs supporting the factual premises of the
  decision. Every returned ID must exist in the supplied evidence.
- An opinion or recommendation may synthesize evidence, but it cannot introduce a
  new specification, measurement, price, availability claim, competitor fact, or
  first-hand experience.
- Never reward aggressive conversion language. Honest limitations, conditional
  recommendations, and clear avoid guidance are positive commercial decisions.
- For partial or missing rows, give one surgical correction that improves the
  decision without adding facts. Leave suggested_correction empty for met rows.
- The decision gate cannot override the factual gate. A useful recommendation built
  on an unsupported premise still fails publication.
- Do not return aggregate pass or count fields. Python derives them from
  decision_checks.

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

ESSENTIAL SEO/AIO/CRO PLAN REQUIREMENTS
{json.dumps(requirements, indent=2)}

EXPLICIT BUYER QUESTIONS TO ANSWER
{json.dumps(questions, indent=2)}

EDITORIAL/COMMERCIAL DECISION REQUIREMENTS
{json.dumps(decisions, indent=2)}

ARTICLE TO AUDIT
{article}
"""


def call_haiku(
    client: anthropic.Anthropic,
    article: str,
    evidence: dict,
    plan_requirements: list[dict],
    buyer_questions: list[dict],
    decision_requirements: list[dict],
):
    return client.messages.create(
        model=MODEL,
        max_tokens=11000,
        temperature=0,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": audit_prompt(
                    article,
                    evidence,
                    plan_requirements,
                    buyer_questions,
                    decision_requirements,
                ),
            }
        ],
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


def quote_matches_article(quote: str, article: str) -> bool:
    """Match a copied visible quote while ignoring Markdown punctuation.

    Table pipes, list markers, smart punctuation, and link markup are formatting,
    not factual differences. The normalized word sequence must still occur
    verbatim, so a paraphrase cannot masquerade as an article quote.
    """

    def lexical(value: str) -> str:
        return re.sub(r"[^\w]+", " ", visible_text(value), flags=re.UNICODE).strip()

    normalized_quote = lexical(quote)
    return bool(normalized_quote) and normalized_quote in lexical(article)


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
        check["quote_verified"] = quote_matches_article(quote, article)
        if check.get("verdict") != "supported" and not check["quote_verified"]:
            raise ValueError(
                "Blocking Haiku factual issue quoted text that is not present in the article"
            )
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


def summarize_plan_checks(
    payload: dict,
    requirements: list[dict],
    article: str,
) -> dict:
    """Validate Haiku's plan matrix and derive its blocking report."""

    checks = payload.get("plan_checks")
    if not isinstance(checks, list):
        raise ValueError("Haiku plan audit returned no plan checks")
    if len(checks) != len(requirements):
        raise ValueError(
            "Haiku plan audit must return exactly one row per essential requirement"
        )

    normalized_checks = []
    issues = []
    for requirement, raw_check in zip(requirements, checks):
        if not isinstance(raw_check, dict):
            raise ValueError("Haiku plan check must be an object")
        check = dict(raw_check)
        if check.get("id") != requirement.get("id"):
            raise ValueError("Haiku plan-check IDs must match the supplied order")
        status = check.get("status")
        if status not in {"covered", *PLAN_FAILURE_STATUSES}:
            raise ValueError("Haiku plan check used an invalid status")
        explanation = str(check.get("explanation") or "").strip()
        if not explanation:
            raise ValueError("Haiku plan check is missing its explanation")
        quote = str(check.get("article_quote") or "").strip()
        check["quote_verified"] = quote_matches_article(quote, article) if quote else False
        # Unlike a blocking factual correction, plan coverage is a semantic
        # judgment and may cite a compressed visible span. Preserve whether the
        # quote matched exactly, but do not turn a harmless paraphrase into a
        # pipeline parser failure.
        correction = str(check.get("suggested_correction") or "").strip()
        if status == "covered" and correction:
            # Structured models occasionally leave an optional improvement in a
            # passing row. It is nonblocking metadata, not a plan failure.
            check["discarded_optional_correction"] = correction
            check["suggested_correction"] = ""
            correction = ""
        if status in PLAN_FAILURE_STATUSES and not correction:
            raise ValueError("Unmet plan requirement is missing a correction")
        check["area"] = requirement.get("area")
        check["recommendation"] = requirement.get("recommendation")
        check["evidence_ids"] = requirement.get("evidence_ids") or []
        normalized_checks.append(check)
        if status in PLAN_FAILURE_STATUSES:
            issues.append(
                {
                    "category": f"plan_{status}",
                    "severity": "major",
                    "plan_requirement_id": requirement.get("id"),
                    "plan_area": requirement.get("area"),
                    "recommendation": requirement.get("recommendation"),
                    "article_quote": quote,
                    "explanation": explanation,
                    "evidence_ids": requirement.get("evidence_ids") or [],
                    "suggested_correction": correction,
                    "quote_verified": check["quote_verified"],
                }
            )
    return {
        "passed": not issues,
        "checked_count": len(normalized_checks),
        "covered_count": sum(
            check["status"] == "covered" for check in normalized_checks
        ),
        "issues": issues,
        "checks": normalized_checks,
    }


def summarize_buyer_question_checks(
    payload: dict,
    requirements: list[dict],
    article: str,
) -> dict:
    """Validate coverage of every explicit buyer question from the plan."""

    checks = payload.get("buyer_question_checks")
    if not requirements and checks is None:
        return {
            "passed": True,
            "checked_count": 0,
            "covered_count": 0,
            "issues": [],
            "checks": [],
        }
    if not isinstance(checks, list):
        raise ValueError("Haiku buyer-question audit returned no checks")
    if len(checks) != len(requirements):
        raise ValueError(
            "Haiku buyer-question audit must return exactly one row per question"
        )

    normalized_checks = []
    issues = []
    for requirement, raw_check in zip(requirements, checks):
        if not isinstance(raw_check, dict):
            raise ValueError("Haiku buyer-question check must be an object")
        check = dict(raw_check)
        if check.get("id") != requirement.get("id"):
            raise ValueError("Haiku buyer-question IDs must match the supplied order")
        status = check.get("status")
        if status not in {"covered", *PLAN_FAILURE_STATUSES}:
            raise ValueError("Haiku buyer-question check used an invalid status")
        explanation = str(check.get("explanation") or "").strip()
        if not explanation:
            raise ValueError("Haiku buyer-question check is missing its explanation")
        quote = str(check.get("article_quote") or "").strip()
        check["quote_verified"] = quote_matches_article(quote, article) if quote else False
        correction = str(check.get("suggested_correction") or "").strip()
        if status == "covered" and correction:
            check["discarded_optional_correction"] = correction
            check["suggested_correction"] = ""
            correction = ""
        if status in PLAN_FAILURE_STATUSES and not correction:
            raise ValueError("Unanswered buyer question is missing a correction")
        check["area"] = requirement.get("area")
        check["question"] = requirement.get("question")
        check["recommendation"] = requirement.get("recommendation")
        check["evidence_ids"] = requirement.get("evidence_ids") or []
        normalized_checks.append(check)
        if status in PLAN_FAILURE_STATUSES:
            issues.append(
                {
                    "category": f"buyer_question_{status}",
                    "severity": "major",
                    "buyer_question_id": requirement.get("id"),
                    "question": requirement.get("question"),
                    "article_quote": quote,
                    "explanation": explanation,
                    "evidence_ids": requirement.get("evidence_ids") or [],
                    "suggested_correction": correction,
                    "quote_verified": check["quote_verified"],
                }
            )
    return {
        "passed": not issues,
        "checked_count": len(normalized_checks),
        "covered_count": sum(
            check["status"] == "covered" for check in normalized_checks
        ),
        "issues": issues,
        "checks": normalized_checks,
    }


def summarize_decision_checks(
    payload: dict,
    requirements: list[dict],
    evidence: dict,
    article: str,
) -> dict:
    """Validate the editorial/commercial matrix and derive its blocking report."""

    checks = payload.get("decision_checks")
    if not isinstance(checks, list):
        raise ValueError("Haiku decision-quality audit returned no decision checks")
    if len(checks) != len(requirements):
        raise ValueError(
            "Haiku decision-quality audit must return exactly one row per requirement"
        )

    known_ids = evidence_claim_ids(evidence)
    normalized_checks = []
    issues = []
    for requirement, raw_check in zip(requirements, checks):
        if not isinstance(raw_check, dict):
            raise ValueError("Haiku decision-quality check must be an object")
        check = dict(raw_check)
        if check.get("id") != requirement.get("id"):
            raise ValueError("Haiku decision-quality IDs must match the supplied order")
        status = check.get("status")
        if status not in {"met", *DECISION_FAILURE_STATUSES}:
            raise ValueError("Haiku decision-quality check used an invalid status")
        explanation = str(check.get("explanation") or "").strip()
        if not explanation:
            raise ValueError("Haiku decision-quality check is missing its explanation")
        quote = str(check.get("article_quote") or "").strip()
        check["quote_verified"] = quote_matches_article(quote, article) if quote else False
        evidence_ids = [str(item) for item in check.get("evidence_ids") or []]
        unknown = set(evidence_ids) - known_ids
        if unknown:
            raise ValueError(
                f"Haiku decision-quality audit used unknown evidence IDs: {unknown}"
            )
        correction = str(check.get("suggested_correction") or "").strip()
        if status == "met" and correction:
            check["discarded_optional_correction"] = correction
            check["suggested_correction"] = ""
            correction = ""
        if status in DECISION_FAILURE_STATUSES and not correction:
            raise ValueError("Unmet decision-quality requirement is missing a correction")
        check["area"] = requirement.get("area")
        check["requirement"] = requirement.get("requirement")
        check["expected_evidence_ids"] = requirement.get("evidence_ids") or []
        check["evidence_ids"] = evidence_ids
        normalized_checks.append(check)
        if status in DECISION_FAILURE_STATUSES:
            issues.append(
                {
                    "category": f"decision_{status}",
                    "severity": "major",
                    "decision_requirement_id": requirement.get("id"),
                    "decision_area": requirement.get("area"),
                    "requirement": requirement.get("requirement"),
                    "article_quote": quote,
                    "explanation": explanation,
                    "evidence_ids": evidence_ids,
                    "suggested_correction": correction,
                    "quote_verified": check["quote_verified"],
                }
            )
    return {
        "passed": not issues,
        "checked_count": len(normalized_checks),
        "met_count": sum(check["status"] == "met" for check in normalized_checks),
        "issues": issues,
        "checks": normalized_checks,
    }


def summarize_audit(
    payload: dict,
    evidence: dict,
    article: str,
    requirements: list[dict],
    buyer_questions: list[dict] | None = None,
    decision_requirements: list[dict] | None = None,
) -> dict:
    """Derive four independent Haiku results plus one strict publication gate."""

    factual = summarize_claim_checks(payload, evidence, article)
    plan = summarize_plan_checks(payload, requirements, article)
    buyer_question_result = summarize_buyer_question_checks(
        payload,
        buyer_questions or [],
        article,
    )
    decisions = summarize_decision_checks(
        payload,
        decision_requirements or editorial_commercial_requirements({}),
        evidence,
        article,
    )
    return {
        "passed": (
            factual["passed"]
            and plan["passed"]
            and buyer_question_result["passed"]
            and decisions["passed"]
        ),
        "factual_passed": factual["passed"],
        "plan_passed": plan["passed"],
        "buyer_question_passed": buyer_question_result["passed"],
        "decision_passed": decisions["passed"],
        "audited_claim_count": factual["audited_claim_count"],
        "supported_claim_count": factual["supported_claim_count"],
        "plan_checked_count": plan["checked_count"],
        "plan_covered_count": plan["covered_count"],
        "buyer_question_checked_count": buyer_question_result["checked_count"],
        "buyer_question_covered_count": buyer_question_result["covered_count"],
        "decision_checked_count": decisions["checked_count"],
        "decision_met_count": decisions["met_count"],
        "factual_issues": factual["issues"],
        "plan_issues": plan["issues"],
        "buyer_question_issues": buyer_question_result["issues"],
        "decision_issues": decisions["issues"],
        "issues": [
            *factual["issues"],
            *plan["issues"],
            *buyer_question_result["issues"],
            *decisions["issues"],
        ],
        "claim_checks": factual["claim_checks"],
        "plan_checks": plan["checks"],
        "buyer_question_checks": buyer_question_result["checks"],
        "decision_checks": decisions["checks"],
        **(
            {"discarded_self_exonerating_checks": factual["discarded_self_exonerating_checks"]}
            if factual.get("discarded_self_exonerating_checks")
            else {}
        ),
    }


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


def audit_article(
    client: anthropic.Anthropic,
    article: str,
    evidence: dict,
    plan: dict | None = None,
) -> dict:
    requirements = essential_plan_requirements(plan)
    buyer_questions = buyer_question_requirements(plan)
    decision_requirements = editorial_commercial_requirements(plan)
    if not requirements:
        raise ValueError("Haiku audit requires an SEO/AIO/CRO plan")
    prompt = audit_prompt(
        article,
        evidence,
        requirements,
        buyer_questions,
        decision_requirements,
    )
    message = call_haiku(
        client,
        article,
        evidence,
        requirements,
        buyer_questions,
        decision_requirements,
    )
    if message.stop_reason != "end_turn":
        raise RuntimeError(f"Haiku factual audit stopped early: {message.stop_reason}")
    raw_response = message_text(message)
    audit = summarize_audit(
        parse_json(raw_response),
        evidence,
        article,
        requirements,
        buyer_questions,
        decision_requirements,
    )
    return {
        "article_sha256": stable_hash(article),
        "plan_requirements_sha256": stable_hash(requirements),
        "plan_requirements": requirements,
        "buyer_question_requirements_sha256": stable_hash(buyer_questions),
        "buyer_question_requirements": buyer_questions,
        "decision_requirements_sha256": stable_hash(decision_requirements),
        "decision_requirements": decision_requirements,
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


def cached_run_matches(
    run: Any,
    article: str,
    plan: dict | None = None,
) -> bool:
    if not isinstance(run, dict) or run.get("article_sha256") != stable_hash(article):
        return False
    if plan is None:
        return True
    requirements = essential_plan_requirements(plan)
    buyer_questions = buyer_question_requirements(plan)
    decision_requirements = editorial_commercial_requirements(plan)
    return (
        run.get("plan_requirements_sha256") == stable_hash(requirements)
        and run.get("buyer_question_requirements_sha256")
        == stable_hash(buyer_questions)
        and run.get("decision_requirements_sha256")
        == stable_hash(decision_requirements)
    )


__all__ = [
    "MODEL",
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "AUDIT_SCHEMA",
    "audit_article",
    "audit_prompt",
    "buyer_question_requirements",
    "cached_run_matches",
    "discard_self_exonerating_issues",
    "editorial_commercial_requirements",
    "evidence_claim_ids",
    "essential_plan_requirements",
    "stable_hash",
    "summarize_claim_checks",
    "summarize_buyer_question_checks",
    "summarize_decision_checks",
    "summarize_plan_checks",
    "summarize_audit",
    "validate_audit",
    "visible_text",
    "quote_matches_article",
]
