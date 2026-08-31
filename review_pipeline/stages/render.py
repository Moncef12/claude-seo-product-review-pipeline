"""Render the final review and a safe, collapsed twelve-step pipeline trace."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import markdown

from review_pipeline.config import (
    ARTICLE_MAX_WORDS,
    ARTICLE_MIN_WORDS,
    AUTHORITY_RAW_PATH,
    DATAFORSEO_ENDPOINT,
    DISCOVERY_DIR,
    EXTRACTIONS_PATH,
    EVIDENCE_DIR,
    FACTUAL_AUDIT_PATH,
    OUTPUT_DIR,
    PLAN_PATH,
    PRODUCT,
    PRODUCTION_SUMMARY_PATH,
    QUALIFICATION_PATH,
    REQUIRED_HEADINGS,
    REVIEW_MANIFEST_PATH,
    SCRAPED_REVIEWS_DIR,
    SERP_RAW_PATH,
    SOURCES_PATH,
    SEARCH_QUERIES,
    VALIDATION_PATH,
    ensure_data_directories,
)


REVIEW_PATH = OUTPUT_DIR / "review.md"
HTML_PATH = OUTPUT_DIR / "review.html"
DRAFT_PATH = OUTPUT_DIR / "draft.md"
NORMALIZED_EVIDENCE_PATH = EVIDENCE_DIR / "normalized.json"
GENERATION_PATH = OUTPUT_DIR / "generation.json"
REPAIR_PATH = OUTPUT_DIR / "repair.json"
CLEANUP_PATH = OUTPUT_DIR / "watermark-cleanup.json"
POLISHED_PATH = OUTPUT_DIR / "polished.md"


def document_title(markdown_text: str) -> str:
    match = re.search(r"^#\s+(.+)$", markdown_text, flags=re.MULTILINE)
    return match.group(1).strip() if match else "Product Review"


def document_description(markdown_text: str) -> str:
    match = re.search(r"^\*\*Meta description:\*\*\s*(.+)$", markdown_text, flags=re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def render_body(markdown_text: str) -> str:
    # Python-Markdown treats a bold label followed immediately by a list item as
    # one paragraph. Keep fenced examples untouched while making Pros/Cons lists
    # render as lists in the article UI.
    lines = markdown_text.splitlines(keepends=True)
    normalized: list[str] = []
    in_fence = False
    for index, line in enumerate(lines):
        normalized.append(line)
        if re.match(r"^\s*(```|~~~)", line):
            in_fence = not in_fence
            continue
        if in_fence or not re.match(r"^\s*\*\*(?:Pros|Cons)\*\*\s*$", line.rstrip("\r\n")):
            continue
        if index + 1 < len(lines) and re.match(r"^\s*[-+*]\s+", lines[index + 1]):
            normalized.append("\n")
    return markdown.markdown("".join(normalized), extensions=["tables", "fenced_code"])


def _read_artifact(path: Path) -> Any | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    if path.suffix.lower() == ".json":
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def _display_value(value: Any) -> str:
    if value is None:
        return "Unavailable"
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False)
    except (TypeError, ValueError):
        return str(value)


def _json_tree_value(value: Any) -> Any | None:
    """Return a structured JSON value only for complete object/array values."""
    if isinstance(value, Mapping):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    if isinstance(value, str):
        candidate = value.strip()
        if not ((candidate.startswith("{") and candidate.endswith("}")) or (candidate.startswith("[") and candidate.endswith("]"))):
            return None
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            return None
        return parsed if isinstance(parsed, (Mapping, list)) else None
    return None


def _json_scalar(value: Any) -> str:
    if value is None:
        return '<span class="json-null">null</span>'
    if isinstance(value, bool):
        return f'<span class="json-boolean">{str(value).lower()}</span>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<span class="json-number">{html.escape(json.dumps(value, ensure_ascii=False), quote=True)}</span>'
    text = json.dumps(str(value), ensure_ascii=False)
    return f'<span class="json-string">{html.escape(text, quote=True)}</span>'


def _json_summary(value: Any) -> str:
    if isinstance(value, Mapping):
        count = len(value)
        return "{" + f"{count} key{'s' if count != 1 else ''}" + "}"
    return "[" + f"{len(value)} item{'s' if len(value) != 1 else ''}" + "]"


def _json_tree(value: Any, *, root: bool = True) -> str:
    """Render a structured value as static, escaped, nested-details JSON."""
    is_object = isinstance(value, Mapping)
    entries = list(value.items()) if is_object else list(enumerate(value))
    rows: list[str] = []
    for index, (key, child) in enumerate(entries):
        key_html = f'<span class="json-key">{html.escape(json.dumps(str(key), ensure_ascii=False), quote=True)}</span>' if is_object else f'<span class="json-index">{index}</span>'
        separator = '<span class="json-punctuation">:</span>' if is_object else ''
        comma = '<span class="json-punctuation">,</span>' if index < len(entries) - 1 else ''
        structured = _json_tree_value(child)
        if structured is not None:
            rows.append(
                f'<details class="json-node"><summary>{key_html}{separator} '
                f'<span class="json-summary">{html.escape(_json_summary(structured), quote=True)}</span></summary>'
                f'<div class="json-children">{_json_tree(structured, root=False)}</div></details>{comma}'
            )
        else:
            rows.append(f'<div class="json-entry">{key_html}{separator} {_json_scalar(child)} {comma}</div>')
    opener, closer = ("{", "}") if is_object else ("[", "]")
    body = "".join(rows)
    container_class = "json-object" if is_object else "json-array"
    summary = f'<span class="json-summary">{html.escape(_json_summary(value), quote=True)}</span> ' if root else ""
    return f'<div class="json-container {container_class}">{summary}<span class="json-punctuation">{opener}</span>{body}<span class="json-punctuation">{closer}</span></div>'


def _artifact_block(label: str, value: Any) -> str:
    escaped_label = html.escape(label, quote=True)
    structured = _json_tree_value(value)
    content = f'<div class="json-preview">{_json_tree(structured)}</div>' if structured is not None else f'<pre>{html.escape(_display_value(value), quote=True)}</pre>'
    return (
        f'<details class="pipeline-artifact">'
        f'<summary>{escaped_label}</summary>'
        f'{content}'
        f'</details>'
    )


def _artifact_direction(label: str) -> str:
    """Classify trace artifacts into the two reviewer-facing I/O groups."""
    normalized = label.strip().casefold()
    input_prefixes = (
        "input",
        "system prompt",
        "user prompt",
        "user repair prompt",
        "validation rules",
        "deterministic transformation",
        "cleanup configuration",
        "renderer configuration",
    )
    return "input" if normalized.startswith(input_prefixes) else "output"


def _artifact_groups(artifacts: Sequence[tuple[str, Any]]) -> str:
    grouped: dict[str, list[tuple[str, Any]]] = {"input": [], "output": []}
    for label, value in artifacts:
        grouped[_artifact_direction(label)].append((label, value))
    sections = []
    for direction, title in (("input", "Input"), ("output", "Output")):
        blocks = "".join(_artifact_block(label, value) for label, value in grouped[direction])
        sections.append(
            f'<section class="pipeline-io-group {direction}" aria-label="{title}">'
            f'<h3>{title}</h3>{blocks}</section>'
        )
    return '<div class="pipeline-io">' + "".join(sections) + "</div>"


def _step(
    number: int,
    title: str,
    artifacts: Sequence[tuple[str, Any]],
    description: str = "",
    next_step: str = "",
    result_label: str | None = None,
    outcome: str = "",
    result_state: str = "neutral",
    notice_html: str = "",
) -> str:
    available = sum(value is not None for _, value in artifacts)
    status = result_label or ("AVAILABLE" if available else "UNAVAILABLE")
    state = result_state if result_label else ("neutral" if available else "failed")
    blocks = _artifact_groups(artifacts)
    result = f'<div class="pipeline-outcome {html.escape(state, quote=True)}"><strong>Result:</strong> {html.escape(outcome, quote=True)}</div>' if outcome else ""
    context = f'<p class="pipeline-description">{html.escape(description, quote=True)}</p>' if description else ""
    transition = f'<p class="pipeline-next"><strong>Next:</strong> {html.escape(next_step, quote=True)}</p>' if next_step else ""
    return f'''<details class="pipeline-step">
  <summary><span class="pipeline-number">{number}</span><span>{html.escape(title, quote=True)}</span><span class="pipeline-status {html.escape(state, quote=True)}">{html.escape(status, quote=True)}</span></summary>
  <div class="pipeline-step-body">{result}{notice_html}{context}{blocks}{transition}</div>
</details>
'''


def _json_files(directory: Path) -> list[tuple[str, Any]]:
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError:
        return []
    return [(path.name, _read_artifact(path)) for path in paths]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _field(record: Any, key: str) -> Any | None:
    return record.get(key) if isinstance(record, Mapping) else None


def _run_metadata(record: Any, *fields: str) -> dict[str, Any] | None:
    if not isinstance(record, Mapping):
        return None
    return {field: record.get(field) for field in fields}


def _passed_label(passed: Any, issues: int) -> tuple[str, str]:
    if passed is True:
        return "PASS", "passed"
    if passed is False:
        return f"FAIL · {issues} ISSUE{'S' if issues != 1 else ''}", "failed"
    return "NOT RUN", "neutral"


def _failure_lines(report: Any, validator: str = "") -> list[str]:
    lines = []
    for issue in _mapping(report).get("issues") or []:
        if not isinstance(issue, Mapping):
            continue
        code = str(issue.get("code") or issue.get("category") or "validation").replace("_", " ").strip()
        message = str(issue.get("message") or issue.get("explanation") or "Validation failed.").strip()
        prefix = f"{validator} " if validator else ""
        lines.append(f"{prefix}{code}: {message}")
    return lines


def _failure_summary(report: Any, validator: str = "") -> str:
    lines = _failure_lines(report, validator)
    return " Initial failures: " + " | ".join(lines) if lines else ""


def _failure_block(failures: Any, *, default_validator: str = "Validation") -> str:
    failure_items = []
    if not isinstance(failures, Sequence) or isinstance(failures, (str, bytes)):
        return ""
    for failure in failures:
        if not isinstance(failure, Mapping):
            continue
        validator = str(failure.get("validator") or default_validator)
        code = str(failure.get("code") or failure.get("category") or "failure").replace("_", " ")
        message = str(failure.get("message") or failure.get("explanation") or "Validation failed.")
        failure_items.append(
            f"<li><strong>{html.escape(validator, quote=True)} · {html.escape(code, quote=True)}:</strong> {html.escape(message, quote=True)}</li>"
        )
    if not failure_items:
        return ""
    return (
        '<div class="production-failures"><h3>Initial validation failures (repaired)</h3><ul>'
        + "".join(failure_items)
        + "</ul></div>"
    )


def _qualification_block(qualification: Any) -> str:
    """Render a plain-language, expandable view of every scored candidate."""
    data = _mapping(qualification)
    considered = data.get("considered")
    if not isinstance(considered, Sequence) or isinstance(considered, (str, bytes)):
        return ""
    selected_count = data.get("selected_count") or len(data.get("selected") or [])
    selected_scores = [
        row.get("total_score") for row in considered
        if isinstance(row, Mapping) and row.get("result") == "selected"
        and isinstance(row.get("total_score"), (int, float))
    ]
    cutoff = min(selected_scores) if selected_scores else None
    cutoff_text = (
        f"Selection cutoff: the lowest selected score was {cutoff:g}; the top {selected_count} eligible sources were kept, with one source per root domain."
        if cutoff is not None else
        f"Selection cutoff: keep the top {selected_count} eligible sources, with one source per root domain."
    )
    weights = data.get("weights")
    weight_text = ""
    if isinstance(weights, Mapping):
        labels = ", ".join(f"{str(key).replace('_', ' ')} ({value:g} pts)" for key, value in weights.items() if isinstance(value, (int, float)))
        if labels:
            weight_text = f" Scoring adds points for {html.escape(labels, quote=True)}."
    rows: list[str] = []
    for index, row in enumerate(considered, 1):
        if not isinstance(row, Mapping):
            continue
        publisher = row.get("publisher") or row.get("root_domain") or "Unknown source"
        domain = row.get("root_domain") or "domain unavailable"
        score = row.get("total_score")
        score_text = f"{score:g}" if isinstance(score, (int, float)) else _display_value(score)
        result = str(row.get("result") or ("selected" if row.get("eligible") else "rejected"))
        state = "selected" if result.casefold() == "selected" else "rejected"
        reasons = row.get("hard_rejection_reasons") or row.get("reasons") or ["No additional reason recorded."]
        if not isinstance(reasons, Sequence) or isinstance(reasons, (str, bytes)):
            reasons = [reasons]
        reason_items = "".join(f"<li>{html.escape(_display_value(reason), quote=True)}</li>" for reason in reasons)
        breakdown = row.get("score_breakdown") or {}
        breakdown_items = "".join(
            f"<li><strong>{html.escape(str(key).replace('_', ' '), quote=True)}:</strong> "
            f"{html.escape(_display_value(value.get('points') if isinstance(value, Mapping) else value), quote=True)} points"
            f"{(' — ' + html.escape(str(value.get('reason')), quote=True)) if isinstance(value, Mapping) and value.get('reason') else ''}</li>"
            for key, value in breakdown.items()
        ) if isinstance(breakdown, Mapping) else ""
        rows.append(
            '<details class="qualification-row">'
            f'<summary><span>{html.escape(str(publisher), quote=True)} <small>({html.escape(str(domain), quote=True)})</small></span>'
            f'<strong>{html.escape(score_text, quote=True)} points</strong><span class="qualification-status {state}">{html.escape(state, quote=True)}</span></summary>'
            f'<div class="qualification-detail"><p><strong>Why:</strong> {html.escape(str(row.get("result") or state), quote=True)}</p>'
            f'<p><strong>Reasons</strong></p><ul>{reason_items}</ul>'
            f'<p><strong>Score breakdown</strong></p><ul>{breakdown_items or "<li>Unavailable</li>"}</ul></div></details>'
        )
    if not rows:
        return ""
    return (
        '<section class="qualification-summary" aria-label="Source qualification results">'
        '<h3>Source qualification</h3>'
        '<p>Each candidate below was scored using observable signals. Select a row to see exactly how its score was calculated and why it was selected or rejected.</p>'
        f'<p class="qualification-cutoff">{cutoff_text}{weight_text}</p>'
        '<div class="qualification-table" role="list" aria-label="Scored source candidates">'
        + "".join(rows) + '</div></section>'
    )


def _plan_brief_block(plan: Any) -> str:
    """Render the planning artifact as a plain-language reviewer brief."""
    data = _mapping(_field(plan, "plan")) or _mapping(_field(plan, "parsed_plan")) or _mapping(plan)
    if not data:
        return ""

    def text(value: Any, fallback: str = "Unavailable") -> str:
        if value is None or value == "":
            return fallback
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return html.escape("; ".join(str(item) for item in value), quote=True)
        return html.escape(str(value), quote=True)

    def list_items(values: Any, fields: tuple[str, ...]) -> str:
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
            return '<li>Unavailable</li>'
        items: list[str] = []
        for value in values:
            if isinstance(value, Mapping):
                parts = [text(value.get(field)) for field in fields if value.get(field) not in (None, "")]
                if parts:
                    items.append(f"<li>{' — '.join(parts)}</li>")
            elif value not in (None, ""):
                items.append(f"<li>{text(value)}</li>")
        return "".join(items) or '<li>Unavailable</li>'

    def card(title: str, body: str) -> str:
        return f'<details class="plan-brief-card"><summary>{text(title)}</summary><div class="plan-brief-detail">{body}</div></details>'

    overview = (
        '<div class="plan-brief-overview">'
        f'<p><strong>What readers are trying to decide:</strong> {text(data.get("primary_intent"))}</p>'
        f'<p><strong>Who this is for:</strong> {text(data.get("target_reader"))}</p>'
        f'<p><strong>Where they are in the journey:</strong> {text(data.get("funnel_stage"))}</p>'
        f'<p><strong>Recommended article angle:</strong> {text(data.get("article_angle"))}</p>'
        f'<p><strong>Call to action:</strong> {text(data.get("cta_placement"))}</p>'
        '</div>'
    )
    cards = (
        card("Recurring search topics", f'<ul>{list_items(data.get("recurring_serp_topics"), ("topic", "serp_basis"))}</ul>')
        + card("Questions buyers are asking", f'<ul>{list_items(data.get("buyer_questions"), ("question", "serp_basis"))}</ul>')
        + card("What competing coverage misses", f'<ul>{list_items(data.get("content_gaps"), ("gap", "serp_basis"))}</ul>')
        + card("Editorial decisions", f'<ul>{list_items(data.get("editorial_decisions"), ("decision", "serp_basis"))}</ul>')
        + card("Answers to make easy to find", f'<ul>{list_items(data.get("aio_direct_answer_targets"), ("question", "answer_direction"))}</ul>')
        + card("Buyer concerns and how to address them", f'<ul>{list_items(data.get("cro_buyer_objections"), ("objection", "response_direction"))}</ul>')
        + card("Planned article order", f'<ol>{list_items(data.get("ordered_outline"), ("heading", "purpose"))}</ol>')
    )
    return (
        '<section class="plan-brief" aria-label="Plain-language SEO AIO CRO planning brief">'
        '<h3>Planning brief for the reviewer</h3>'
        '<p class="plan-brief-intro">Haiku used search intent, competitor coverage, and evidence gaps to shape this people-first brief. It adds no product facts.</p>'
        + overview + '<div class="plan-brief-cards">' + cards + '</div></section>'
    )


def _audit_brief_block(audit: Any) -> str:
    """Render Haiku's three independent review gates for human reviewers."""
    data = _mapping(audit)
    if not data:
        return ""

    def esc(value: Any, fallback: str = "Unavailable") -> str:
        return html.escape(fallback if value in (None, "") else str(value), quote=True)

    def badge(status: Any) -> str:
        normalized = str(status or "missing").replace("_", " ").title()
        state = (
            "covered"
            if normalized.casefold() in {"covered", "met", "pass", "passed", "supported"}
            else "partial"
            if "partial" in normalized.casefold()
            else "missing"
        )
        return f'<span class="audit-badge {state}">{esc(normalized)}</span>'

    factual_issues = data.get("factual_issues") or []
    if not isinstance(factual_issues, Sequence) or isinstance(factual_issues, (str, bytes, bytearray)):
        factual_issues = []
    issue_rows = []
    for issue in factual_issues:
        item = _mapping(issue)
        evidence = item.get("evidence_ids") or item.get("supporting_evidence_ids") or []
        if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes, bytearray)):
            evidence_text = ", ".join(str(value) for value in evidence)
        else:
            evidence_text = str(evidence)
        body = (
            f'<p><strong>Category:</strong> {esc(item.get("category") or item.get("code"))}</p>'
            f'<p><strong>Article passage:</strong> {esc(item.get("quote") or item.get("article_quote"))}</p>'
            f'<p><strong>What Haiku found:</strong> {esc(item.get("explanation") or item.get("message"))}</p>'
            f'<p><strong>Suggested correction:</strong> {esc(item.get("correction") or item.get("suggested_correction"))}</p>'
            f'<p><strong>Evidence IDs:</strong> {esc(evidence_text)}</p>'
        )
        issue_rows.append(f'<details class="audit-row"><summary>{esc(item.get("category") or item.get("code") or "Factual issue")}</summary><div class="audit-detail">{body}</div></details>')
    factual_status = "PASS" if data.get("factual_passed", data.get("passed")) is True else "FAIL" if data.get("factual_passed", data.get("passed")) is False else "NOT RUN"
    plan_status = "PASS" if data.get("plan_passed", data.get("passed")) is True else "FAIL" if data.get("plan_passed", data.get("passed")) is False else "NOT RUN"
    decision_status = "PASS" if data.get("decision_passed") is True else "FAIL" if data.get("decision_passed") is False else "NOT RUN"

    checks = data.get("plan_checks") or []
    if not isinstance(checks, Sequence) or isinstance(checks, (str, bytes, bytearray)):
        checks = []
    check_rows = []
    for check in checks:
        item = _mapping(check)
        area = str(item.get("area") or item.get("id") or "Plan check").replace("_", " ").title()
        evidence = item.get("evidence_ids") or []
        evidence_text = ", ".join(str(value) for value in evidence) if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes, bytearray)) else str(evidence)
        evidence_display = evidence_text or "Not required for this planning item"
        correction = item.get("suggested_correction") or item.get("correction")
        body = (
            f'<p><strong>Advice from Haiku:</strong> {esc(item.get("recommendation"))}</p>'
            f'<p><strong>Status:</strong> {badge(item.get("status"))}</p>'
            f'<p><strong>Article passage:</strong> {esc(item.get("article_quote"))}</p>'
            f'<p><strong>Evidence IDs:</strong> {esc(evidence_display)}</p>'
            f'<p><strong>Explanation:</strong> {esc(item.get("explanation"))}</p>'
            + (f'<p><strong>Correction:</strong> {esc(correction)}</p>' if correction else '')
        )
        check_rows.append(f'<details class="audit-row"><summary>{esc(area)} {badge(item.get("status"))}</summary><div class="audit-detail">{body}</div></details>')

    decision_checks = data.get("decision_checks") or []
    if not isinstance(decision_checks, Sequence) or isinstance(decision_checks, (str, bytes, bytearray)):
        decision_checks = []
    decision_rows = []
    for check in decision_checks:
        item = _mapping(check)
        area = str(item.get("area") or item.get("id") or "Decision check").replace("_", " ").title()
        evidence = item.get("evidence_ids") or []
        evidence_text = ", ".join(str(value) for value in evidence) if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes, bytearray)) else str(evidence)
        evidence_display = evidence_text or "No separate factual premise required"
        correction = item.get("suggested_correction") or item.get("correction")
        body = (
            f'<p><strong>Decision standard:</strong> {esc(item.get("requirement"))}</p>'
            f'<p><strong>Status:</strong> {badge(item.get("status"))}</p>'
            f'<p><strong>Article passage:</strong> {esc(item.get("article_quote"))}</p>'
            f'<p><strong>Supporting evidence IDs:</strong> {esc(evidence_display)}</p>'
            f'<p><strong>Why it passes or fails:</strong> {esc(item.get("explanation"))}</p>'
            + (f'<p><strong>Correction:</strong> {esc(correction)}</p>' if correction else '')
        )
        decision_rows.append(f'<details class="audit-row"><summary>{esc(area)} {badge(item.get("status"))}</summary><div class="audit-detail">{body}</div></details>')

    claims = data.get("claim_checks") or []
    if not isinstance(claims, Sequence) or isinstance(claims, (str, bytes, bytearray)):
        claims = []
    claim_rows = []
    for claim in claims:
        item = _mapping(claim)
        verdict = str(item.get("verdict") or item.get("status") or "unavailable")
        verdict_label = "Supported" if verdict.casefold() in {"supported", "pass", "passed"} else verdict.replace("_", " ").title()
        verdict_state = "covered" if verdict_label == "Supported" else "missing"
        evidence = item.get("evidence_ids") or []
        evidence_text = ", ".join(str(value) for value in evidence) if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes, bytearray)) else str(evidence)
        correction = item.get("suggested_correction") or item.get("correction")
        body = (
            f'<p><strong>Verdict:</strong> <span class="audit-badge {verdict_state}">{esc(verdict_label)}</span></p>'
            f'<p><strong>Article passage:</strong> {esc(item.get("article_quote") or item.get("quote"))}</p>'
            f'<p><strong>Evidence IDs:</strong> {esc(evidence_text)}</p>'
            f'<p><strong>Explanation:</strong> {esc(item.get("explanation"))}</p>'
            + (f'<p><strong>Suggested correction:</strong> {esc(correction)}</p>' if correction else '')
        )
        sequence = item.get("index")
        label = f'Claim {sequence}' if sequence is not None else "Factual claim"
        claim_rows.append(f'<details class="audit-row"><summary>{esc(label)} <span class="audit-badge {verdict_state}">{esc(verdict_label)}</span></summary><div class="audit-detail">{body}</div></details>')
    claim_supported = data.get("supported_claim_count")
    claim_audited = data.get("audited_claim_count")
    if claim_supported is None:
        claim_supported = sum(1 for row in claims if _mapping(row).get("verdict", "").casefold() == "supported")
    if claim_audited is None:
        claim_audited = len(claim_rows)
    plan_covered = data.get("plan_covered_count")
    plan_checked = data.get("plan_checked_count")
    if plan_covered is None:
        plan_covered = sum(1 for row in checks if _mapping(row).get("status", "").casefold() == "covered")
    if plan_checked is None:
        plan_checked = len(check_rows)
    decision_met = data.get("decision_met_count")
    decision_checked = data.get("decision_checked_count")
    if decision_met is None:
        decision_met = sum(1 for row in decision_checks if _mapping(row).get("status", "").casefold() == "met")
    if decision_checked is None:
        decision_checked = len(decision_rows)
    factual_count = f'{esc(claim_supported)}/{esc(claim_audited)}'
    plan_count = f'{esc(plan_covered)}/{esc(plan_checked)}'
    decision_count = f'{esc(decision_met)}/{esc(decision_checked)}'
    factual_body = (
        ('<p class="audit-clear">No factual issues were found.</p>' if not issue_rows else '<div class="audit-section"><h4>Factual issues</h4>' + "".join(issue_rows) + '</div>')
        + ('<div class="audit-section"><h4>Validated factual claims</h4>' + "".join(claim_rows) + '</div>' if claim_rows else '')
    )
    plan_body = '<div class="audit-section">' + ("".join(check_rows) if check_rows else '<p class="audit-clear">No plan checks are available.</p>') + '</div>'
    buyer_checks = data.get("buyer_question_checks") or []
    if not isinstance(buyer_checks, Sequence) or isinstance(buyer_checks, (str, bytes, bytearray)):
        buyer_checks = []
    buyer_rows = []
    for check in buyer_checks:
        item = _mapping(check)
        question = item.get("question") or item.get("buyer_question") or "Buyer question"
        recommendation = item.get("recommendation") or item.get("answer_direction") or item.get("response_direction")
        evidence = item.get("evidence_ids") or item.get("supporting_evidence_ids") or []
        evidence_text = ", ".join(str(value) for value in evidence) if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes, bytearray)) else str(evidence)
        evidence_display = evidence_text or "Not required for this buyer question"
        correction = item.get("correction") or item.get("suggested_correction")
        body = (
            f'<p><strong>Question:</strong> {esc(question)}</p>'
            f'<p><strong>Recommendation:</strong> {esc(recommendation)}</p>'
            f'<p><strong>Status:</strong> {badge(item.get("status") or item.get("verdict"))}</p>'
            f'<p><strong>Article passage:</strong> {esc(item.get("article_quote") or item.get("quote"))}</p>'
            f'<p><strong>Evidence IDs:</strong> {esc(evidence_display)}</p>'
            f'<p><strong>Explanation:</strong> {esc(item.get("explanation") or item.get("message"))}</p>'
            + (f'<p><strong>Correction:</strong> {esc(correction)}</p>' if correction else '')
        )
        buyer_rows.append(f'<details class="audit-row"><summary>{esc(question)} {badge(item.get("status") or item.get("verdict"))}</summary><div class="audit-detail">{body}</div></details>')
    buyer_passed = data.get("buyer_question_passed")
    buyer_status = "PASS" if buyer_passed is True else "FAIL" if buyer_passed is False else "NOT RUN"
    buyer_covered = data.get("buyer_question_covered_count")
    buyer_checked = data.get("buyer_question_checked_count")
    if buyer_covered is None:
        buyer_covered = sum(1 for row in buyer_checks if str(_mapping(row).get("status") or _mapping(row).get("verdict") or "").casefold() in {"covered", "met", "pass", "passed"})
    if buyer_checked is None:
        buyer_checked = len(buyer_rows)
    buyer_body = '<div class="audit-section">' + ("".join(buyer_rows) if buyer_rows else '<p class="audit-clear">No buyer-question checks are available.</p>') + '</div>'
    plan_body += f'<details class="audit-subsection"><summary>Buyer-question coverage · {esc(buyer_status)} · {esc(buyer_covered)}/{esc(buyer_checked)}</summary><div class="audit-subsection-body">{buyer_body}</div></details>'
    decision_body = '<div class="audit-section">' + ("".join(decision_rows) if decision_rows else '<p class="audit-clear">No editorial/commercial checks are available.</p>') + '</div>'
    return (
        '<section class="audit-brief" aria-label="Haiku audit results">'
        '<h3>Haiku review, explained</h3>'
        '<p class="audit-brief-intro">Factual grounding, SEO/AIO/CRO coverage, and editorial/commercial decision quality are checked separately. All three must pass; planning or conversion goals can never override the evidence.</p>'
        '<div class="audit-rubrics">'
        f'<details class="audit-rubric"><summary>Factual grounding · {esc(factual_status)} · {factual_count}</summary><div class="audit-rubric-body">{factual_body}</div></details>'
        f'<details class="audit-rubric"><summary>SEO/AIO/CRO plan coverage · {esc(plan_status)} · {plan_count}</summary><div class="audit-rubric-body">{plan_body}</div></details>'
        f'<details class="audit-rubric"><summary>Editorial/commercial decision quality · {esc(decision_status)} · {decision_count}</summary><div class="audit-rubric-body">{decision_body}</div></details>'
        '</div></section>'
    )


def _compact_number(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _display_value(value)
    absolute = abs(value)
    if absolute >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if absolute >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:g}"


def _api_calls_display(summary: Mapping[str, Any], total: Any) -> str:
    calls = _mapping(summary.get("calls"))
    parts = [f"{_display_value(total)} total"]
    for label, key in (("Anthropic", "anthropic"), ("DataForSEO", "dataforseo")):
        count = calls.get(key)
        if count is not None:
            parts.append(f"{label} {count}")
    return " · ".join(parts)


def _tokens_display(tokens: Mapping[str, Any], fallback: Any) -> str:
    total = tokens.get("total", fallback)
    parts = [f"{_compact_number(total)} total"]
    if tokens.get("input") is not None:
        parts.append(f"{_compact_number(tokens['input'])} in")
    if tokens.get("output") is not None:
        parts.append(f"{_compact_number(tokens['output'])} out")
    return " · ".join(parts)


def _cost_display(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _display_value(value)
    return f"${value:.2f}"


def _validation_summary_block(summary: Mapping[str, Any]) -> str:
    validation = _mapping(summary.get("validation"))
    python_result = str(validation.get("final_python", summary.get("final_python_result", "NOT RUN"))).upper()
    combined_haiku_result = str(validation.get("final_haiku", summary.get("final_haiku_result", "NOT RUN"))).upper()
    haiku_result = str(validation.get("final_haiku_factual", combined_haiku_result)).upper()
    plan_result = str(validation.get("final_haiku_plan", "NOT RUN")).upper()
    buyer_question_result = str(validation.get("final_haiku_buyer_questions", "NOT RUN")).upper()
    decision_result = str(validation.get("final_haiku_decision", "NOT RUN")).upper()
    failures = summary.get("initial_failures") or []
    python_failure_count = sum(
        1
        for failure in failures
        if isinstance(failure, Mapping) and str(failure.get("validator") or "").lower() == "python"
    )
    python_detail = "Final article passed deterministic structure, policy, provenance, and decision checks."
    if python_result != "PASS":
        python_detail = "Final deterministic checks did not pass."
    if python_failure_count:
        suffix = f"{python_failure_count} initial issue{'s' if python_failure_count != 1 else ''} repaired."
        if python_result != "PASS":
            suffix = f"{python_failure_count} initial issue{'s' if python_failure_count != 1 else ''}; final validation still failed."
        python_detail = f"{python_detail} {suffix}"

    supported = validation.get("final_haiku_supported", summary.get("final_haiku_supported_count"))
    audited = validation.get("final_haiku_audited", summary.get("final_haiku_audited_count"))
    if supported is not None and audited is not None:
        haiku_detail = f"{supported}/{audited} final claims supported by normalized evidence."
    else:
        haiku_detail = "Final evidence-grounding audit completed."
    if haiku_result != "PASS":
        haiku_detail = f"{haiku_detail} The final claim audit did not pass."

    plan_covered = validation.get("final_plan_covered", summary.get("final_plan_covered_count"))
    plan_checked = validation.get("final_plan_checked", summary.get("final_plan_checked_count"))
    if plan_covered is not None and plan_checked is not None:
        plan_detail = (
            f"{plan_covered}/{plan_checked} essential SEO/AIO/CRO requirements "
            "covered by the final article."
        )
    else:
        plan_detail = "Final SEO/AIO/CRO brief-adherence audit completed."
    if plan_result != "PASS":
        plan_detail = f"{plan_detail} The final plan-coverage audit did not pass."
    buyer_covered = validation.get("final_buyer_questions_covered", summary.get("final_buyer_questions_covered_count"))
    buyer_checked = validation.get("final_buyer_questions_checked", summary.get("final_buyer_questions_checked_count"))
    if buyer_covered is not None and buyer_checked is not None:
        plan_detail = f"{plan_detail} Buyer questions: {buyer_covered}/{buyer_checked} answered."
    if buyer_question_result == "FAIL":
        plan_detail = f"{plan_detail} Buyer-question coverage did not pass."

    decision_met = validation.get("final_decision_met", summary.get("final_decision_met_count"))
    decision_checked = validation.get("final_decision_checked", summary.get("final_decision_checked_count"))
    if decision_met is not None and decision_checked is not None:
        decision_detail = (
            f"{decision_met}/{decision_checked} buyer-decision standards met: "
            "recommendation, segmentation, trade-offs, objections, value, and next step."
        )
    else:
        decision_detail = "Final editorial/commercial decision-quality audit completed."
    if decision_result != "PASS":
        decision_detail = f"{decision_detail} The final decision-quality audit did not pass."

    def row(label: str, result: str, detail: str) -> str:
        state = "passed" if result == "PASS" else "failed" if result == "FAIL" else "neutral"
        return (
            '<div class="validation-summary-row">'
            f'<span class="validation-summary-label">{html.escape(label, quote=True)}</span>'
            f'<strong class="validation-badge {state}">{html.escape(result, quote=True)}</strong>'
            f'<span class="validation-summary-detail">{html.escape(detail, quote=True)}</span>'
            "</div>"
        )

    return (
        '<section class="validation-summary" aria-label="Validation results">'
        "<h3>Validation results</h3>"
        + row("Python rules", python_result, python_detail)
        + row("Haiku claim audit", haiku_result, haiku_detail)
        + row("Haiku plan coverage", plan_result, plan_detail)
        + row("Haiku editorial/commercial", decision_result, decision_detail)
        + "</section>"
    )


def _repair_artifact() -> Any:
    repair = _read_artifact(REPAIR_PATH)
    if repair is not None:
        return repair
    validation = _read_artifact(VALIDATION_PATH)
    if isinstance(validation, Mapping):
        final = validation.get("final") or validation.get("final_validation") or {}
        if isinstance(final, Mapping) and final.get("passed"):
            return {"status": "skipped", "repair_called": False, "reason": "Initial candidate passed deterministic validation; no repair call was made."}
    return {"status": "unavailable", "reason": "No repair record is available."}


def _summary_card(summary: Any) -> str:
    value = _mapping(summary)
    calls = value.get("total_api_calls", value.get("total_calls", value.get("total_external_api_calls", "Unavailable")))
    fetches = value.get("source_fetch_count", _mapping(value.get("calls")).get("source_fetches", "Unavailable"))
    tokens = _mapping(value.get("tokens"))
    token_value = tokens.get("total", value.get("total_tokens", "Unavailable"))
    cost = value.get("estimated_total_usd", _mapping(value.get("cost")).get("estimated_total_usd", "Unavailable"))
    repair = value.get("repair")
    repair_map = _mapping(repair)
    repair_result = repair_map.get("status") or ("called" if value.get("repair_called") else "skipped" if repair is not None else "NOT RUN")
    fields = (
        ("API calls", _api_calls_display(value, calls)),
        ("Page fetches", fetches),
        ("Claude tokens", _tokens_display(tokens, token_value)),
        ("Estimated cost", _cost_display(cost)),
        ("Repair", repair_result),
    )
    cells = "".join(f'<div class="production-summary-item"><span>{html.escape(str(label), quote=True)}</span><strong>{html.escape(_display_value(item), quote=True)}</strong></div>' for label, item in fields)
    validation_block = _validation_summary_block(value)
    return f'''<section class="production-summary" aria-label="Production summary">
  <h2>Production summary</h2>
  <div class="production-summary-grid">{cells}</div>
  {validation_block}
</section>
'''


def production_summary_card(summary: Any = None) -> str:
    return _summary_card(_read_artifact(PRODUCTION_SUMMARY_PATH) if summary is None else summary)


def pipeline_trace() -> str:
    """Build twelve numbered collapsed steps from whatever artifacts are present."""

    sources = _read_artifact(SOURCES_PATH)
    authority = _read_artifact(AUTHORITY_RAW_PATH)
    manifest = _read_artifact(REVIEW_MANIFEST_PATH)
    qualification = _read_artifact(QUALIFICATION_PATH)
    scraped = _json_files(SCRAPED_REVIEWS_DIR)
    extraction = _read_artifact(EXTRACTIONS_PATH)
    normalized = _read_artifact(NORMALIZED_EVIDENCE_PATH)
    plan = _read_artifact(PLAN_PATH)
    generation = _read_artifact(GENERATION_PATH)
    validation = _read_artifact(VALIDATION_PATH)
    factual_audit = _read_artifact(FACTUAL_AUDIT_PATH)
    repair = _repair_artifact()
    summary = _read_artifact(PRODUCTION_SUMMARY_PATH)
    polished = _read_artifact(POLISHED_PATH)
    review = _read_artifact(REVIEW_PATH)
    cleanup = _read_artifact(CLEANUP_PATH)
    source_map = _mapping(sources)
    source_count = len(source_map.get("sources") or [])
    authority_map = _mapping(authority)
    authority_hits = len(authority_map.get("cache_hit_domains") or [])
    authority_requested = len(authority_map.get("requested_domains") or [])
    authority_call = bool(authority_map.get("called") or authority_map.get("call_made"))
    authority_note = (
        f" Authority database: {authority_hits} fresh domain hits; "
        f"{authority_requested} domains requested in {'one bulk call' if authority_call else 'zero provider calls'}."
        if authority_map
        else ""
    )
    review_count = len(manifest) if isinstance(manifest, list) else 0
    extraction_output = _mapping(_field(extraction, "extraction"))
    extracted_claims = len(extraction_output.get("claims") or [])
    normalized_claims = len(_mapping(normalized).get("claims") or [])
    plan_value = _mapping(_field(plan, "plan")) or _mapping(plan)
    outline_count = len(plan_value.get("ordered_outline") or [])
    generation_words = _mapping(generation).get("word_count")
    initial_python = _mapping(_mapping(validation).get("initial"))
    final_python = _mapping(_mapping(validation).get("final"))
    python_label, python_state = _passed_label(initial_python.get("passed"), len(initial_python.get("issues") or []))
    initial_run = _mapping(_mapping(factual_audit).get("initial"))
    initial_audit = _mapping(initial_run.get("audit"))
    final_run = _mapping(_mapping(factual_audit).get("final"))
    final_audit = _mapping(final_run.get("audit"))
    if not final_audit and _mapping(factual_audit).get("final_reused_initial"):
        final_audit = initial_audit
    audit_label, audit_state = _passed_label(initial_audit.get("passed"), len(initial_audit.get("issues") or []))
    audited_claims = initial_audit.get("audited_claim_count", 0)
    supported_claims = initial_audit.get("supported_claim_count", 0)
    checked_plan_items = initial_audit.get("plan_checked_count", 0)
    covered_plan_items = initial_audit.get("plan_covered_count", 0)
    checked_decision_items = initial_audit.get("decision_checked_count", 0)
    met_decision_items = initial_audit.get("decision_met_count", 0)
    checked_buyer_questions = initial_audit.get("buyer_question_checked_count", 0)
    covered_buyer_questions = initial_audit.get("buyer_question_covered_count", 0)
    python_failures = _failure_summary(initial_python, "Python")
    haiku_failures = _failure_summary(initial_audit, "Haiku")
    combined_failures = python_failures + haiku_failures
    repair_record = _mapping(repair)
    repair_called = repair_record.get("repair_called")
    final_passed = final_python.get("passed") is True and final_audit.get("passed") is True
    if repair_called is False and final_passed:
        repair_label, repair_state = "REPAIR SKIPPED · FINAL PASS", "passed"
        repair_outcome = "Repair did NOT run. The initial Python, Haiku factual, Haiku plan, Haiku buyer-question, and Haiku editorial/commercial validations passed, so this stage made 0 Sonnet repair calls and reused the initial Haiku audit. Final Python: PASS. Final Haiku factual: PASS. Final Haiku plan: PASS. Final Haiku buyer questions: PASS. Final Haiku editorial/commercial: PASS."
    elif repair_called is True and final_passed:
        repair_label, repair_state = "REPAIR RAN · FINAL PASS", "passed"
        repair_outcome = "Repair ran once with Sonnet, followed by final Python validation and one combined Haiku re-audit. Final Python: PASS. Final Haiku factual: PASS. Final Haiku plan: PASS. Final Haiku buyer questions: PASS. Final Haiku editorial/commercial: PASS."
    elif repair_called is True:
        repair_label, repair_state = "REPAIR RAN · FINAL FAIL", "failed"
        repair_outcome = f"Repair ran once. Final Python: {'PASS' if final_python.get('passed') else 'FAIL'}. Final Haiku: {'PASS' if final_audit.get('passed') else 'FAIL'}. The article was not publishable."
    else:
        repair_label, repair_state = "NOT COMPLETED", "neutral"
        repair_outcome = "No complete repair decision and final dual-validation result is available."

    return "".join(
        (
            _step(1, "Discover live Google sources", (
                ("Input: DataForSEO request", {"endpoint": DATAFORSEO_ENDPOINT, "product": PRODUCT, "queries": _mapping(source_map.get("collection")).get("queries") or SEARCH_QUERIES, "location": "United States", "language": "English", "device": "desktop"}),
                ("Output: SERP signals and candidate sources", sources),
                ("Raw output: DataForSEO response", _read_artifact(SERP_RAW_PATH)),
                ("Raw authority bulk-ranks response", authority),
            ), "Search the primary exact-product query and conditional pros/cons fallback; preserve raw SERP and authority responses.", "Qualify and fetch a bounded candidate pool.", f"{source_count} SOURCES FOUND" if source_count else "NOT RUN", f"Google discovery produced {source_count} unique candidate sources and preserved SERP intent signals.{authority_note}", "passed" if source_count else "neutral"),
            _step(2, "Dynamic qualification/scrape", (
                ("Input: discovered candidates", sources),
                ("Output: preliminary pool and full qualification artifact", qualification),
                ("Output: selected five-source manifest", manifest),
                *[(f"Output: cached source body ({name})", value) for name, value in scraped],
            ), "Fetch discovered candidates with safe HTTP behavior, score observable evidence, reject hard failures, and enforce unique root domains.", "Extract exactly the five selected cache records in one Haiku call.", f"{review_count}/5 REVIEWS SELECTED" if review_count else "NOT RUN", f"Qualification selected {review_count} dynamic independent reviews; official and community pages were excluded from the independent evidence set.", "passed" if review_count == 5 else "failed" if review_count else "neutral", _qualification_block(qualification)),
            _step(3, "Extract evidence with one Haiku call", (
                ("Run metadata and token usage", _run_metadata(extraction, "model", "prompt_version", "reviews_sha256", "extracted_at", "usage")),
                ("System prompt", _field(extraction, "system_prompt")),
                ("User prompt and selected source input", _field(extraction, "prompt")),
                ("Raw Haiku output", _field(extraction, "raw_response")),
                ("Parsed structured output", _field(extraction, "extraction")),
            ), "Use structured JSON to merge equivalent claims, retain provenance, and expose disagreements.", "Normalize the structured evidence deterministically.", f"{extracted_claims} CLAIMS EXTRACTED" if extracted_claims else "NOT RUN", f"One Haiku extraction produced {extracted_claims} evidence claims from {len(extraction_output.get('sources') or [])} selected sources.", "passed" if extracted_claims else "neutral"),
            _step(4, "Normalize the evidence", (
                ("Input: parsed Haiku evidence", _field(extraction, "extraction")),
                ("Deterministic transformation", "Clean fields, validate provenance, deduplicate claims, attach stable IDs, count categories, and retain conflicts."),
                ("Output: compact normalized evidence", normalized),
            ), "This stage uses Python only and makes no model call.", "Build the cached SEO/AIO/CRO planning brief.", f"{normalized_claims} CLAIMS NORMALIZED" if normalized_claims else "NOT RUN", f"Python normalized {normalized_claims} claims and retained {len(_mapping(normalized).get('conflicts') or [])} source conflicts.", "passed" if normalized_claims else "neutral"),
            _step(5, "SEO/AIO/CRO plan", (
                ("Input: normalized evidence", normalized),
                ("Input: SERP signals", source_map.get("serp_signals")),
                ("Input: qualification and selected-page headings", qualification),
                ("Run metadata and token usage", _run_metadata(plan, "model", "prompt_version", "inputs_sha256", "planned_at", "usage")),
                ("System prompt", _field(plan, "system_prompt")),
                ("User prompt and planning inputs", _field(plan, "user_prompt") or _field(plan, "prompt")),
                ("Raw Haiku output", _field(plan, "raw_response")),
                ("Parsed planning brief", _field(plan, "plan")),
            ), "Use live SERP intent, competitor coverage, and evidence gaps for a cached people-first planning brief; the plan adds no product facts.", "Give the complete plan and normalized evidence to Sonnet.", f"{outline_count} OUTLINE SECTIONS PLANNED" if outline_count else "NOT RUN", f"Haiku planning produced {outline_count} ordered outline sections with evidence-grounded editorial, AIO, and CRO decisions.", "passed" if outline_count else "neutral", _plan_brief_block(plan)),
            _step(6, "Generate the article with one Sonnet call", (
                ("Run metadata and token usage", _run_metadata(generation, "model", "prompt_version", "evidence_sha256", "plan_sha256", "generated_at", "word_count", "usage")),
                ("System prompt", _field(generation, "system_prompt")),
                ("User prompt, complete plan, and normalized evidence", _field(generation, "prompt")),
                ("Raw Sonnet output", _field(generation, "article")),
            ), "Create one publish-ready Markdown candidate while treating evidence as authoritative over planning wording.", "Run deterministic Python structure and decision-support checks.", f"GENERATED · {generation_words} WORDS" if generation_words else "NOT RUN", f"Sonnet generated one {generation_words}-word raw candidate." if generation_words else "No Sonnet candidate is available.", "passed" if generation_words else "neutral"),
            _step(7, "Python validation", (
                ("Input: raw Sonnet candidate", _read_artifact(DRAFT_PATH)),
                ("Validation rules", {"minimum_words": ARTICLE_MIN_WORDS, "maximum_words": ARTICLE_MAX_WORDS, "required_h2_headings": REQUIRED_HEADINGS, "expected_sources": [item for item in manifest if isinstance(item, Mapping)], "editorial_checks": ["quick verdict decision", "Best for/Avoid if/Biggest compromise labels", "buyer-fit guidance", "final conversion cue"]}),
                ("Output: validation report", validation),
            ), "Evaluate structure, length, provenance, policy, and plan-aligned commercial decision support without spending model tokens.", "Run the exhaustive Haiku factual audit.", python_label, f"Python checked the raw candidate: {initial_python.get('word_count', 0)} words, {len(initial_python.get('issues') or [])} issues.", python_state, _failure_block(initial_python.get("issues") or [], default_validator="Python")),
            _step(8, "Haiku factual, plan, and decision audit", (
                ("Input: candidate, evidence, and audit checklists", {"article": _read_artifact(DRAFT_PATH), "evidence": normalized, "plan_requirements": initial_run.get("plan_requirements"), "buyer_question_requirements": initial_run.get("buyer_question_requirements"), "editorial_commercial_requirements": initial_run.get("decision_requirements")}),
                ("Run metadata and token usage", _run_metadata(factual_audit, "model", "prompt_version", "evidence_sha256", "plan_sha256", "final_reused_initial")),
                ("Initial combined-audit run metadata", _run_metadata(initial_run, "article_sha256", "plan_requirements_sha256", "buyer_question_requirements_sha256", "decision_requirements_sha256", "audited_at", "usage")),
                ("System prompt", _field(initial_run, "system_prompt")),
                ("User prompt, evidence, plan checklist, and article", _field(initial_run, "prompt")),
                ("Raw Haiku output", _field(initial_run, "raw_response")),
                ("Parsed factual, plan, buyer-question, and decision audit", _field(initial_run, "audit")),
            ), "In one Haiku call, audit every factual premise, every essential SEO/AIO/CRO recommendation, and evidence-constrained buyer questions and decision standards.", "Repair only combined Python, factual, plan-coverage, buyer-question, and decision-quality failures.", f"{audit_label} · {supported_claims}/{audited_claims} CLAIMS · {covered_plan_items}/{checked_plan_items} PLAN · {covered_buyer_questions}/{checked_buyer_questions} BUYER QUESTIONS · {met_decision_items}/{checked_decision_items} DECISIONS", f"Haiku checked {audited_claims} factual claims ({supported_claims} supported), {checked_plan_items} essential plan items ({covered_plan_items} covered), {checked_buyer_questions} buyer questions ({covered_buyer_questions} covered), and {checked_decision_items} editorial/commercial standards ({met_decision_items} met), with {len(initial_audit.get('issues') or [])} total issues.{haiku_failures}", audit_state, _audit_brief_block(initial_audit)),
            _step(9, "Conditional repair/final gate", (
                ("Run metadata, decision, and token usage", _run_metadata(repair, "model", "prompt_version", "repair_called", "plan_sha256", "word_count", "usage")),
                ("System prompt", _field(repair, "system_prompt")),
                ("User repair prompt, failed checks, plan, evidence, and article", _field(repair, "prompt")),
                ("Raw repair output", _field(repair, "article")),
                ("Initial validation", _field(repair, "initial_validation")),
                ("Initial factual audit", _field(repair, "initial_factual_audit")),
                ("Final Python validation", _field(repair, "final_validation")),
                ("Final Haiku factual, plan, buyer-question, and decision audit", final_audit),
            ), "Make zero calls after a pass, or one surgical Sonnet repair and one combined Haiku re-audit after a failure. No retry loop.", "Write the deterministic production summary.", repair_label, repair_outcome + combined_failures, repair_state),
            _step(10, "Production summary", (
                ("Input: all recorded run artifacts", {"discovery": sources, "authority": authority, "qualification": qualification, "extraction": extraction, "plan": plan, "generation": generation, "validation": validation, "factual_audit": factual_audit, "repair": repair}),
                ("Output: production-summary.json", summary),
            ), "Derive calls, recorded costs, token usage, validation outcomes, repair status, and final word count without guessing.", "Apply deterministic Layer A cleanup to the gated candidate.", "AVAILABLE" if summary else "NOT RUN", "Production accounting is deterministic and records its pricing basis and exclusions." if summary else "No production summary is available.", "passed" if summary else "neutral"),
            _step(11, "Layer A cleanup", (
                ("Input: validated Markdown candidate", polished),
                ("Cleanup configuration", {"tool": "guillaumemeyer/watermarks-remover", "mode": "Layer A deterministic Unicode cleanup"}),
                ("Output: cleanup report", cleanup),
                ("Output: cleaned review Markdown", review),
            ), "Remove or normalize deterministic Unicode artifacts without rewriting factual content.", "Render the cleaned Markdown as standalone HTML.", "UNCHANGED · 0 ARTIFACTS" if _mapping(cleanup).get("changed") is False else "CLEANED" if cleanup else "NOT RUN", "Layer A cleanup ran and found no removable artifacts." if _mapping(cleanup).get("changed") is False else "Layer A cleanup completed." if cleanup else "Cleanup is not available.", "passed" if cleanup else "neutral"),
            _step(12, "Render", (
                ("Input: cleaned review Markdown", review),
                ("Renderer configuration", {"format": "standalone HTML5", "title": document_title(review or ""), "meta_description": document_description(review or ""), "debug_trace": "collapsed by default", "output": str(HTML_PATH)}),
                ("Output", "The final rendered review appears immediately below this trace."),
            ), "Convert Markdown tables, headings, links, and prose into a local review page with this audit trace.", "Review the final article below.", "RENDERED", f"The final review was rendered to {HTML_PATH.name} and appears below this trace." if review else "No cleaned review is available.", "passed" if review else "neutral"),
        )
    )


def html_document(
    title: str,
    description: str,
    body: str,
    raw_draft: str = "",
    pipeline_html: str | None = None,
    production_summary: Any = None,
) -> str:
    trace = pipeline_trace() if pipeline_html is None else pipeline_html
    trace_panel = f'''      <div class="pipeline-trace" aria-label="Complete pipeline trace">
      <h2>Pipeline trace</h2>
      <p class="pipeline-intro">Collapsed debug view of discovery, qualification, evidence, planning, generation, validation, repair, accounting, cleanup, and final output artifacts.</p>
{trace}      </div>
'''
    draft_panel = ""
    if raw_draft:
        draft_panel = f'''    <details class="raw-draft">
      <summary>Initial Sonnet candidate (raw Markdown)</summary>
      <pre>{html.escape(raw_draft, quote=True)}</pre>
    </details>
'''
    summary = production_summary_card(production_summary)
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{html.escape(description, quote=True)}">
  <title>{html.escape(title, quote=True)}</title>
  <style>
    :root {{ color-scheme: light; --canvas: #f8fafd; --surface: #fff; --ink: #1f1f1f; --muted: #5f6368; --line: #d0d7de; --primary: #0b57d0; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--canvas); color: var(--ink); font: 16px/1.6 system-ui, sans-serif; }}
    article {{ max-width: 960px; margin: 0 auto; padding: 28px 24px 56px; }}
    .page-header {{ margin-bottom: 16px; }}
    .eyebrow {{ margin: 0 0 5px; color: var(--primary); font-size: .72rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }}
    .page-title {{ margin: 0; font-size: clamp(1.65rem, 4vw, 2.2rem); line-height: 1.2; }}
    .tabs {{ position: sticky; top: 0; z-index: 10; display: flex; gap: 4px; overflow-x: auto; padding: 4px 0; border-bottom: 1px solid var(--line); background: var(--canvas); }}
    .tab {{ flex: 0 0 auto; border: 0; border-bottom: 2px solid transparent; margin-bottom: -1px; padding: 9px 12px; color: var(--muted); background: transparent; cursor: pointer; font: inherit; font-size: .9rem; font-weight: 650; }}
    .tab:hover, .tab[aria-selected="true"] {{ color: var(--primary); }}
    .tab[aria-selected="true"] {{ border-bottom-color: var(--primary); }}
    .tab:focus-visible, summary:focus-visible {{ outline: 3px solid #8ab4f8; outline-offset: 2px; border-radius: 4px; }}
    .view-panel {{ min-width: 0; }}
    .js-enabled .view-panel:not(.is-active) {{ display: none; }}
    .review-content {{ max-width: 820px; margin: 0 auto; padding: 24px; background: var(--surface); border: 1px solid var(--line); border-radius: 12px; }}
    h1, h2 {{ line-height: 1.2; }}
    .review-content h1 {{ font-size: 2.25rem; }}
    h2 {{ margin-top: 2rem; font-size: 1.45rem; }}
    table {{ width: 100%; border-collapse: collapse; font-size: .95rem; }}
    th, td {{ border: 1px solid var(--line); padding: 9px 11px; text-align: left; vertical-align: top; }}
    th {{ background: #f1f3f4; }}
    hr {{ border: 0; border-top: 1px solid var(--line); margin: 2rem 0; }}
    strong {{ color: var(--primary); }}
    .production-summary {{ padding: 18px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); }}
    .production-summary h2 {{ margin: 0 0 .7rem; font-size: 1.1rem; }}
    .production-summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); gap: 8px; }}
    .production-summary-item {{ padding: 8px 10px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); }}
    .production-summary-item span, .production-summary-item strong {{ display: block; }}
    .production-summary-item span {{ color: var(--muted); font-size: .72rem; text-transform: uppercase; }}
    .production-summary-item strong {{ color: var(--ink); font-size: .95rem; overflow-wrap: anywhere; }}
    .validation-summary {{ margin-top: 12px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); }}
    .validation-summary h3 {{ margin: 0 0 6px; font-size: .92rem; }}
    .validation-summary-row {{ display: grid; grid-template-columns: 130px 48px minmax(0, 1fr); align-items: center; gap: 8px; padding: 5px 0; }}
    .validation-summary-row + .validation-summary-row {{ border-top: 1px solid var(--line); }}
    .validation-summary-label {{ font-size: .86rem; font-weight: 650; }}
    .validation-badge {{ padding: 2px 6px; border-radius: 999px; text-align: center; font-size: .68rem; }}
    .validation-badge.passed {{ color: #176638; background: #e6f5eb; }}
    .validation-badge.failed {{ color: #9b2c2c; background: #fdeaea; }}
    .validation-badge.neutral {{ color: var(--muted); background: #edf1f4; }}
    .validation-summary-detail {{ color: var(--muted); font-size: .8rem; }}
    .production-failures {{ margin-top: 10px; padding: 10px 12px; border-left: 4px solid #c47a14; border-radius: 4px; background: #fff8e8; }}
    .production-failures h3 {{ margin: 0 0 6px; font-size: .92rem; }}
    .production-failures ul {{ margin: 0; padding-left: 1.2rem; }}
    .production-failures li {{ margin: .25rem 0; font-size: .86rem; }}
    .qualification-summary {{ margin: 10px 0; padding: 12px; border: 1px solid var(--line); border-radius: 10px; background: #f8fafc; }}
    .qualification-summary h3 {{ margin: 0 0 4px; font-size: .95rem; }}
    .qualification-summary p {{ margin: 5px 0; color: var(--muted); font-size: .84rem; }}
    .qualification-cutoff {{ padding: 7px 9px; border-left: 3px solid var(--primary); background: #eef4ff; }}
    .qualification-row {{ margin: 6px 0; border: 1px solid var(--line); border-radius: 7px; background: var(--surface); overflow: hidden; }}
    .qualification-row > summary {{ display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 9px; align-items: center; cursor: pointer; padding: 8px 10px; list-style: none; font-size: .84rem; }}
    .qualification-row > summary::-webkit-details-marker {{ display: none; }}
    .qualification-row > summary:hover {{ background: #f0f4f7; }}
    .qualification-row small {{ color: var(--muted); }}
    .qualification-status {{ padding: 2px 7px; border-radius: 999px; font-size: .68rem; font-weight: 700; text-transform: uppercase; }}
    .qualification-status.selected {{ color: #176638; background: #e6f5eb; }}
    .qualification-status.rejected {{ color: #9b2c2c; background: #fdeaea; }}
    .qualification-detail {{ padding: 7px 12px 10px; border-top: 1px solid var(--line); }}
    .qualification-detail p {{ margin: 5px 0 2px; }}
    .qualification-detail ul {{ margin: 0; padding-left: 1.25rem; color: var(--muted); font-size: .8rem; }}
    .plan-brief {{ margin: 10px 0; padding: 12px; border: 1px solid var(--line); border-radius: 10px; background: #f8fafc; }}
    .plan-brief h3 {{ margin: 0 0 4px; font-size: .95rem; }}
    .plan-brief-intro {{ margin: 5px 0 10px; color: var(--muted); font-size: .84rem; }}
    .plan-brief-overview {{ padding: 8px 10px; border-left: 3px solid var(--primary); background: #eef4ff; }}
    .plan-brief-overview p {{ margin: 5px 0; font-size: .84rem; }}
    .plan-brief-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 7px; margin-top: 9px; }}
    .plan-brief-card {{ border: 1px solid var(--line); border-radius: 7px; background: var(--surface); overflow: hidden; }}
    .plan-brief-card > summary {{ cursor: pointer; padding: 8px 10px; font-size: .82rem; font-weight: 650; list-style: none; }}
    .plan-brief-card > summary::-webkit-details-marker {{ display: none; }}
    .plan-brief-card > summary::before {{ content: "›"; display: inline-block; width: 14px; color: var(--primary); font-size: 1.1rem; line-height: .8; transition: transform .15s ease; }}
    .plan-brief-card[open] > summary::before {{ transform: rotate(90deg); }}
    .plan-brief-card > summary:hover {{ background: #f0f4f7; }}
    .plan-brief-detail {{ padding: 7px 12px 9px; border-top: 1px solid var(--line); }}
    .plan-brief-detail ul, .plan-brief-detail ol {{ margin: 0; padding-left: 1.2rem; color: var(--muted); font-size: .8rem; }}
    .plan-brief-detail li {{ margin: 4px 0; }}
    .audit-brief {{ margin: 10px 0; padding: 12px; border: 1px solid var(--line); border-radius: 10px; background: #f8fafc; }}
    .audit-brief h3 {{ margin: 0 0 4px; font-size: .95rem; }}
    .audit-brief-intro, .audit-clear {{ margin: 5px 0 10px; color: var(--muted); font-size: .84rem; }}
    .audit-summary {{ padding: 7px 10px; border-left: 3px solid var(--primary); background: #eef4ff; }}
    .audit-summary p {{ margin: 4px 0; font-size: .84rem; }}
    .audit-status {{ font-weight: 700; }}
    .audit-section {{ margin-top: 10px; }}
    .audit-section h4 {{ margin: 0 0 5px; font-size: .86rem; }}
    .audit-rubrics {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; margin-top: 10px; }}
    .audit-rubric {{ min-width: 0; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); overflow: hidden; }}
    .audit-rubric > summary {{ cursor: pointer; padding: 9px 10px; font-size: .82rem; font-weight: 700; list-style: none; }}
    .audit-rubric > summary::-webkit-details-marker {{ display: none; }}
    .audit-rubric > summary::before {{ content: "›"; display: inline-block; width: 14px; color: var(--primary); font-size: 1.1rem; line-height: .8; transition: transform .15s ease; }}
    .audit-rubric[open] > summary::before {{ transform: rotate(90deg); }}
    .audit-rubric-body {{ padding: 0 10px 10px; border-top: 1px solid var(--line); }}
    .audit-rubric-body .audit-section {{ margin-top: 8px; }}
    .audit-row {{ margin: 6px 0; border: 1px solid var(--line); border-radius: 7px; background: var(--surface); overflow: hidden; }}
    .audit-row > summary {{ display: flex; align-items: center; gap: 8px; cursor: pointer; padding: 8px 10px; font-size: .82rem; font-weight: 650; list-style: none; }}
    .audit-row > summary::-webkit-details-marker {{ display: none; }}
    .audit-row > summary::before {{ content: "›"; color: var(--primary); font-size: 1.1rem; line-height: .8; }}
    .audit-row[open] > summary::before {{ transform: rotate(90deg); }}
    .audit-detail {{ padding: 7px 12px 9px; border-top: 1px solid var(--line); color: var(--muted); font-size: .8rem; }}
    .audit-detail p {{ margin: 5px 0; }}
    .audit-badge {{ margin-left: auto; padding: 2px 7px; border-radius: 999px; font-size: .68rem; font-weight: 700; text-transform: uppercase; }}
    .audit-badge.covered {{ color: #176638; background: #e6f5eb; }}
    .audit-badge.partial {{ color: #8a5a00; background: #fff4d6; }}
    .audit-badge.missing {{ color: #9b2c2c; background: #fdeaea; }}
    .pipeline-trace {{ padding: 18px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); }}
    .pipeline-trace > h2 {{ margin: 0 0 .35rem; font-size: 1.2rem; }}
    .pipeline-intro {{ margin: 0 0 1rem; color: var(--muted); font-size: .9rem; }}
    .pipeline-step {{ margin: 8px 0; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); }}
    .pipeline-step > summary {{ display: flex; align-items: center; gap: 9px; cursor: pointer; padding: 9px 11px; font-weight: 600; list-style: none; }}
    .pipeline-step > summary::-webkit-details-marker {{ display: none; }}
    .pipeline-number {{ display: inline-grid; width: 24px; height: 24px; place-items: center; border-radius: 50%; background: var(--primary); color: white; font-size: .8rem; }}
    .pipeline-status {{ margin-left: auto; padding: 3px 7px; border-radius: 999px; color: var(--muted); background: #edf1f4; font-size: .72rem; font-weight: 700; text-transform: uppercase; }}
    .pipeline-status.passed {{ color: #176638; background: #e6f5eb; }}
    .pipeline-status.failed {{ color: #9b2c2c; background: #fdeaea; }}
    .pipeline-step-body {{ padding: 0 11px 11px; }}
    .pipeline-outcome {{ margin: 10px 0; padding: 10px 12px; border-left: 4px solid #8795a1; border-radius: 4px; background: #f2f5f7; font-size: .9rem; }}
    .pipeline-outcome.passed {{ border-color: #25834d; background: #edf8f1; }}
    .pipeline-outcome.failed {{ border-color: #c33b3b; background: #fff0f0; }}
    .pipeline-description, .pipeline-next {{ margin: 8px 0; color: var(--muted); font-size: .84rem; }}
    .pipeline-next {{ padding-top: 8px; border-top: 1px solid var(--line); }}
    .pipeline-io {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin: 10px 0; align-items: start; }}
    .pipeline-io-group {{ min-width: 0; padding: 10px; border: 1px solid var(--line); border-radius: 10px; background: #f8fafc; }}
    .pipeline-io-group > h3 {{ margin: 0 0 8px; color: var(--ink); font-size: .82rem; letter-spacing: .05em; text-transform: uppercase; }}
    .pipeline-artifact {{ margin: 8px 0; border: 1px solid var(--line); border-radius: 8px; background: #fbfcfd; overflow: hidden; }}
    .pipeline-artifact > summary {{ display: flex; align-items: center; gap: 8px; cursor: pointer; padding: 8px 10px; color: var(--muted); font-size: .82rem; font-weight: 600; list-style: none; transition: background .15s ease, color .15s ease; }}
    .pipeline-artifact > summary::-webkit-details-marker {{ display: none; }}
    .pipeline-artifact > summary::before {{ content: "›"; display: inline-block; color: var(--primary); font-size: 1.15rem; line-height: .8; transform: rotate(0deg); transition: transform .15s ease; }}
    .pipeline-artifact[open] > summary::before {{ transform: rotate(90deg); }}
    .pipeline-artifact > summary:hover {{ color: var(--ink); background: #f0f4f7; }}
    .pipeline-artifact > summary:focus-visible {{ outline: 2px solid var(--primary); outline-offset: -2px; background: #eef4f8; color: var(--ink); }}
    .pipeline-artifact pre {{ border-top: 1px solid var(--line); }}
    .pipeline-artifact pre, .raw-draft pre {{ max-height: 320px; margin: 0; padding: 12px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; background: #f5f7f9; }}
    .json-preview, .json-container {{ max-height: 320px; overflow: auto; padding: 10px 12px; font: 13px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace; background: #f5f7f9; }}
    .json-container {{ max-height: none; overflow: visible; padding: 0; }}
    .json-entry, .json-node {{ padding-left: 18px; }}
    .json-node summary {{ cursor: pointer; list-style: none; }}
    .json-node summary::-webkit-details-marker {{ display: none; }}
    .json-node summary::before {{ content: "›"; display: inline-block; width: 14px; color: var(--primary); font-size: 1.1rem; line-height: .8; transition: transform .15s ease; }}
    .json-node[open] summary::before {{ transform: rotate(90deg); }}
    .json-children {{ padding-left: 16px; }}
    .json-key {{ color: #7b1fa2; }}
    .json-string {{ color: #137333; }}
    .json-number {{ color: #b06000; }}
    .json-boolean {{ color: #0b57d0; }}
    .json-null, .json-summary, .json-index {{ color: var(--muted); }}
    .json-punctuation {{ color: var(--ink); }}
    .raw-draft {{ margin-top: 18px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); }}
    .raw-draft summary {{ cursor: pointer; padding: 10px 12px; font-weight: 600; }}
    @media (max-width: 700px) {{ article {{ padding: 20px 14px 40px; }} .review-content {{ padding: 18px; }} .production-summary-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} .validation-summary-row {{ grid-template-columns: 1fr auto; }} .validation-summary-detail {{ grid-column: 1 / -1; }} .pipeline-io {{ grid-template-columns: 1fr; }} }}
  </style>
  <script>document.documentElement.classList.add("js-enabled");</script>
</head>
<body>
  <article>
    <header class="page-header">
      <p class="eyebrow">Evidence-grounded product review</p>
      <p class="page-title">{html.escape(title, quote=True)}</p>
    </header>
    <nav class="tabs" role="tablist" aria-label="Review sections">
      <button class="tab" id="tab-overview" role="tab" aria-controls="panel-overview" aria-selected="true" tabindex="0" data-view="overview">Overview</button>
      <button class="tab" id="tab-review" role="tab" aria-controls="panel-review" aria-selected="false" tabindex="-1" data-view="review">Final review</button>
      <button class="tab" id="tab-pipeline" role="tab" aria-controls="panel-pipeline" aria-selected="false" tabindex="-1" data-view="pipeline">Pipeline trace</button>
    </nav>
    <main>
      <section id="panel-overview" class="view-panel is-active" role="tabpanel" aria-labelledby="tab-overview">{summary}</section>
      <section id="panel-review" class="view-panel" role="tabpanel" aria-labelledby="tab-review"><div class="review-content">{body}</div></section>
      <section id="panel-pipeline" class="view-panel" role="tabpanel" aria-labelledby="tab-pipeline">{trace_panel}{draft_panel}</section>
    </main>
  </article>
  <script>
    (() => {{
      const tabs = [...document.querySelectorAll(".tab")];
      const panels = [...document.querySelectorAll(".view-panel")];
      const activate = (name, updateHash = true) => {{
        const tab = tabs.find(item => item.dataset.view === name) || tabs[0];
        const view = tab.dataset.view;
        tabs.forEach(item => {{ const selected = item === tab; item.setAttribute("aria-selected", selected); item.tabIndex = selected ? 0 : -1; }});
        panels.forEach(panel => panel.classList.toggle("is-active", panel.id === `panel-${{view}}`));
        if (updateHash && location.hash !== `#${{view}}`) history.pushState(null, "", `#${{view}}`);
      }};
      tabs.forEach((tab, index) => tab.addEventListener("click", () => activate(tab.dataset.view)));
      tabs.forEach((tab, index) => tab.addEventListener("keydown", event => {{
        if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
        event.preventDefault(); const next = tabs[(index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length]; next.focus(); activate(next.dataset.view);
      }}));
      window.addEventListener("hashchange", () => activate(location.hash.slice(1), false));
      activate(location.hash.slice(1) || "overview", false);
      const trace = document.querySelector(".pipeline-trace");
      if (trace) trace.querySelectorAll(":scope > .pipeline-step").forEach(step => step.addEventListener("toggle", () => {{
        if (step.open) trace.querySelectorAll(":scope > .pipeline-step").forEach(other => {{ if (other !== step) other.open = false; }});
      }}));
    }})();
  </script>
</body>
</html>
'''


def main() -> None:
    ensure_data_directories()
    source = REVIEW_PATH.read_text(encoding="utf-8")
    try:
        raw_draft = DRAFT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        raw_draft = ""
    rendered = html_document(document_title(source), document_description(source), render_body(source), raw_draft, pipeline_trace())
    HTML_PATH.write_text(rendered, encoding="utf-8")
    print(f"Rendered HTML review to {HTML_PATH}")


if __name__ == "__main__":
    main()
