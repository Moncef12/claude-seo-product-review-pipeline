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


def _artifact_block(label: str, value: Any) -> str:
    return f'<div class="pipeline-artifact"><h4>{html.escape(label, quote=True)}</h4><pre>{html.escape(_display_value(value), quote=True)}</pre></div>'


def _step(
    number: int,
    title: str,
    artifacts: Sequence[tuple[str, Any]],
    description: str = "",
    next_step: str = "",
    result_label: str | None = None,
    outcome: str = "",
    result_state: str = "neutral",
) -> str:
    available = sum(value is not None for _, value in artifacts)
    status = result_label or ("AVAILABLE" if available else "UNAVAILABLE")
    state = result_state if result_label else ("neutral" if available else "failed")
    blocks = "".join(_artifact_block(label, value) for label, value in artifacts)
    result = f'<div class="pipeline-outcome {html.escape(state, quote=True)}"><strong>Result:</strong> {html.escape(outcome, quote=True)}</div>' if outcome else ""
    context = f'<p class="pipeline-description">{html.escape(description, quote=True)}</p>' if description else ""
    transition = f'<p class="pipeline-next"><strong>Next:</strong> {html.escape(next_step, quote=True)}</p>' if next_step else ""
    return f'''<details class="pipeline-step">
  <summary><span class="pipeline-number">{number}</span><span>{html.escape(title, quote=True)}</span><span class="pipeline-status {html.escape(state, quote=True)}">{html.escape(status, quote=True)}</span></summary>
  <div class="pipeline-step-body">{result}{context}{blocks}{transition}</div>
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
    validation = _mapping(value.get("validation"))
    python_result = validation.get("final_python", validation.get("python_result", "NOT RUN"))
    haiku_result = validation.get("final_haiku", validation.get("haiku_result", "NOT RUN"))
    repair = value.get("repair")
    repair_map = _mapping(repair)
    repair_result = repair_map.get("status") or ("called" if value.get("repair_called") else "skipped" if repair is not None else "NOT RUN")
    fields = (
        ("Total API calls", calls),
        ("Page fetches", fetches),
        ("Claude tokens", token_value),
        ("Estimated total USD", cost),
        ("Python", python_result),
        ("Haiku", haiku_result),
        ("Repair", repair_result),
    )
    cells = "".join(f'<div class="production-summary-item"><span>{html.escape(str(label), quote=True)}</span><strong>{html.escape(_display_value(item), quote=True)}</strong></div>' for label, item in fields)
    return f'''<section class="production-summary" aria-label="Production summary">
  <h2>Production summary</h2>
  <div class="production-summary-grid">{cells}</div>
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
    repair_record = _mapping(repair)
    repair_called = repair_record.get("repair_called")
    final_passed = final_python.get("passed") is True and final_audit.get("passed") is True
    if repair_called is False and final_passed:
        repair_label, repair_state = "REPAIR SKIPPED · FINAL PASS", "passed"
        repair_outcome = "Repair did NOT run. The initial Python and Haiku validations passed, so this stage made 0 Sonnet repair calls and reused the initial Haiku audit. Final Python: PASS. Final Haiku: PASS."
    elif repair_called is True and final_passed:
        repair_label, repair_state = "REPAIR RAN · FINAL PASS", "passed"
        repair_outcome = "Repair ran once with Sonnet, followed by final Python validation and a Haiku factual re-audit. Final Python: PASS. Final Haiku: PASS."
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
            ), "Fetch discovered candidates with safe HTTP behavior, score observable evidence, reject hard failures, and enforce unique root domains.", "Extract exactly the five selected cache records in one Haiku call.", f"{review_count}/5 REVIEWS SELECTED" if review_count else "NOT RUN", f"Qualification selected {review_count} dynamic independent reviews; official and community pages were excluded from the independent evidence set.", "passed" if review_count == 5 else "failed" if review_count else "neutral"),
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
            ), "Use live SERP intent, competitor coverage, and evidence gaps for a cached people-first planning brief; the plan adds no product facts.", "Give the complete plan and normalized evidence to Sonnet.", f"{outline_count} OUTLINE SECTIONS PLANNED" if outline_count else "NOT RUN", f"Haiku planning produced {outline_count} ordered outline sections with evidence-grounded editorial, AIO, and CRO decisions.", "passed" if outline_count else "neutral"),
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
            ), "Evaluate structure, length, provenance, policy, and plan-aligned commercial decision support without spending model tokens.", "Run the exhaustive Haiku factual audit.", python_label, f"Python checked the raw candidate: {initial_python.get('word_count', 0)} words, {len(initial_python.get('issues') or [])} issues.", python_state),
            _step(8, "Haiku audit", (
                ("Input: candidate article and normalized evidence", {"article": _read_artifact(DRAFT_PATH), "evidence": normalized}),
                ("Run metadata and token usage", _run_metadata(factual_audit, "model", "prompt_version", "evidence_sha256", "final_reused_initial")),
                ("Initial factual-audit run metadata", _run_metadata(initial_run, "article_sha256", "audited_at", "usage")),
                ("System prompt", _field(initial_run, "system_prompt")),
                ("User prompt and article input", _field(initial_run, "prompt")),
                ("Raw Haiku output", _field(initial_run, "raw_response")),
                ("Parsed factual audit", _field(initial_run, "audit")),
            ), "Audit every factual premise behind verdict, fit, objections, value judgments, comparisons, and CTAs against normalized evidence.", "Repair only combined Python and Haiku failures.", f"{audit_label} · {supported_claims}/{audited_claims} SUPPORTED", f"Haiku checked {audited_claims} factual claims: {supported_claims} supported and {len(initial_audit.get('issues') or [])} issues.", audit_state),
            _step(9, "Conditional repair/final gate", (
                ("Run metadata, decision, and token usage", _run_metadata(repair, "model", "prompt_version", "repair_called", "plan_sha256", "word_count", "usage")),
                ("System prompt", _field(repair, "system_prompt")),
                ("User repair prompt, failed checks, plan, evidence, and article", _field(repair, "prompt")),
                ("Raw repair output", _field(repair, "article")),
                ("Initial validation", _field(repair, "initial_validation")),
                ("Initial factual audit", _field(repair, "initial_factual_audit")),
                ("Final Python validation", _field(repair, "final_validation")),
                ("Final Haiku factual audit", final_audit),
            ), "Make zero calls after a pass, or one surgical Sonnet repair and one Haiku re-audit after a failure. No retry loop.", "Write the deterministic production summary.", repair_label, repair_outcome, repair_state),
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
    trace_panel = f'''    <section class="pipeline-trace" aria-label="Complete pipeline trace">
      <h2>Pipeline trace</h2>
      <p class="pipeline-intro">Collapsed debug view of discovery, qualification, evidence, planning, generation, validation, repair, accounting, cleanup, and final output artifacts.</p>
{trace}    </section>
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
    :root {{ color-scheme: light; --ink: #17202a; --muted: #5f6b76; --line: #dce2e8; }}
    body {{ margin: 0; background: #f5f7f9; color: var(--ink); font: 17px/1.65 system-ui, sans-serif; }}
    article {{ max-width: 820px; margin: 40px auto; padding: 48px; background: white; box-shadow: 0 8px 30px #17202a12; }}
    h1, h2 {{ line-height: 1.2; }}
    h1 {{ font-size: 2.25rem; }}
    h2 {{ margin-top: 2rem; font-size: 1.45rem; }}
    table {{ width: 100%; border-collapse: collapse; font-size: .95rem; }}
    th, td {{ border: 1px solid var(--line); padding: 9px 11px; text-align: left; vertical-align: top; }}
    th {{ background: #f0f3f6; }}
    hr {{ border: 0; border-top: 1px solid var(--line); margin: 2rem 0; }}
    strong {{ color: #0d4f8b; }}
    .production-summary {{ margin-bottom: 1.25rem; padding: 14px 16px; border: 1px solid var(--line); border-radius: 6px; background: #f8fafb; }}
    .production-summary h2 {{ margin: 0 0 .7rem; font-size: 1.1rem; }}
    .production-summary-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }}
    .production-summary-item {{ padding: 7px 9px; border: 1px solid var(--line); border-radius: 4px; background: white; }}
    .production-summary-item span, .production-summary-item strong {{ display: block; }}
    .production-summary-item span {{ color: var(--muted); font-size: .72rem; text-transform: uppercase; }}
    .production-summary-item strong {{ color: var(--ink); font-size: .95rem; overflow-wrap: anywhere; }}
    .pipeline-trace {{ margin-bottom: 2rem; padding: 16px; border: 1px solid var(--line); border-radius: 6px; background: #f8fafb; }}
    .pipeline-trace > h2 {{ margin: 0 0 .35rem; font-size: 1.2rem; }}
    .pipeline-intro {{ margin: 0 0 1rem; color: var(--muted); font-size: .9rem; }}
    .pipeline-step {{ margin: 8px 0; border: 1px solid var(--line); border-radius: 5px; background: white; }}
    .pipeline-step summary {{ display: flex; align-items: center; gap: 9px; cursor: pointer; padding: 9px 11px; font-weight: 600; list-style: none; }}
    .pipeline-step summary::-webkit-details-marker {{ display: none; }}
    .pipeline-number {{ display: inline-grid; width: 24px; height: 24px; place-items: center; border-radius: 50%; background: #0d4f8b; color: white; font-size: .8rem; }}
    .pipeline-status {{ margin-left: auto; padding: 3px 7px; border-radius: 999px; color: var(--muted); background: #edf1f4; font-size: .72rem; font-weight: 700; text-transform: uppercase; }}
    .pipeline-status.passed {{ color: #176638; background: #e6f5eb; }}
    .pipeline-status.failed {{ color: #9b2c2c; background: #fdeaea; }}
    .pipeline-step-body {{ padding: 0 11px 11px; }}
    .pipeline-outcome {{ margin: 10px 0; padding: 10px 12px; border-left: 4px solid #8795a1; border-radius: 4px; background: #f2f5f7; font-size: .9rem; }}
    .pipeline-outcome.passed {{ border-color: #25834d; background: #edf8f1; }}
    .pipeline-outcome.failed {{ border-color: #c33b3b; background: #fff0f0; }}
    .pipeline-description, .pipeline-next {{ margin: 8px 0; color: var(--muted); font-size: .84rem; }}
    .pipeline-next {{ padding-top: 8px; border-top: 1px solid var(--line); }}
    .pipeline-artifact h4 {{ margin: 10px 0 4px; font-size: .82rem; color: var(--muted); }}
    .pipeline-artifact pre, .raw-draft pre {{ max-height: 320px; margin: 0; padding: 12px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; background: #f5f7f9; }}
    .raw-draft {{ margin-bottom: 2rem; border: 1px solid var(--line); border-radius: 6px; background: #f8fafb; }}
    .raw-draft summary {{ cursor: pointer; padding: 10px 12px; font-weight: 600; }}
    @media (max-width: 700px) {{ article {{ margin: 0; padding: 24px; box-shadow: none; }} h1 {{ font-size: 1.8rem; }} .production-summary-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
  </style>
</head>
<body>
  <article>
{summary}{trace_panel}{draft_panel}{body}
  </article>
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
