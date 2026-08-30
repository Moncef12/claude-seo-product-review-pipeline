"""Render the final review and a safe, collapsed pipeline trace."""

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
    DATAFORSEO_ENDPOINT,
    DISCOVERY_DIR,
    EXTRACTIONS_PATH,
    EVIDENCE_DIR,
    FACTUAL_AUDIT_PATH,
    OUTPUT_DIR,
    PRODUCT,
    REQUIRED_HEADINGS,
    REVIEW_SOURCES,
    REVIEW_MANIFEST_PATH,
    SCRAPED_REVIEWS_DIR,
    SEARCH_QUERIES,
    SOURCES_PATH,
    ensure_data_directories,
)


REVIEW_PATH = OUTPUT_DIR / "review.md"
HTML_PATH = OUTPUT_DIR / "review.html"
DRAFT_PATH = OUTPUT_DIR / "draft.md"
SERP_RAW_PATH = DISCOVERY_DIR / "dataforseo-raw.json"
NORMALIZED_EVIDENCE_PATH = EVIDENCE_DIR / "normalized.json"
GENERATION_PATH = OUTPUT_DIR / "generation.json"
VALIDATION_PATH = OUTPUT_DIR / "validation.json"
REPAIR_PATH = OUTPUT_DIR / "repair.json"
CLEANUP_PATH = OUTPUT_DIR / "watermark-cleanup.json"
POLISHED_PATH = OUTPUT_DIR / "polished.md"


def document_title(markdown_text: str) -> str:
    match = re.search(r"^#\s+(.+)$", markdown_text, flags=re.MULTILINE)
    return match.group(1).strip() if match else "Product Review"


def document_description(markdown_text: str) -> str:
    match = re.search(
        r"^\*\*Meta description:\*\*\s*(.+)$",
        markdown_text,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    return match.group(1).strip() if match else ""


def render_body(markdown_text: str) -> str:
    # Python-Markdown treats a bold label followed immediately by a list item as
    # one paragraph.  The article contract uses these labels as list headings,
    # so add the required blank line at this narrow boundary only.  Keep fenced
    # code untouched, since an example containing ``**Pros**`` is not article UI.
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
            newline = "\n" if line.endswith("\n") else "\n"
            normalized.append(newline)
    return markdown.markdown("".join(normalized), extensions=["tables", "fenced_code"])


def _read_artifact(path: Path) -> Any | None:
    """Read JSON or text, returning None for missing/unreadable artifacts."""

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
    safe_label = html.escape(label, quote=True)
    safe_value = html.escape(_display_value(value), quote=True)
    return f'<div class="pipeline-artifact"><h4>{safe_label}</h4><pre>{safe_value}</pre></div>'


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
    safe_title = html.escape(title, quote=True)
    safe_status = html.escape(status, quote=True)
    safe_description = html.escape(description, quote=True)
    safe_outcome = html.escape(outcome, quote=True)
    safe_next = html.escape(next_step, quote=True)
    blocks = "".join(_artifact_block(label, value) for label, value in artifacts)
    context = f'<p class="pipeline-description">{safe_description}</p>' if description else ""
    result = f'<div class="pipeline-outcome {html.escape(state, quote=True)}"><strong>Result:</strong> {safe_outcome}</div>' if outcome else ""
    transition = f'<p class="pipeline-next"><strong>Next:</strong> {safe_next}</p>' if next_step else ""
    return f'''<details class="pipeline-step">
  <summary><span class="pipeline-number">{number}</span><span>{safe_title}</span><span class="pipeline-status {html.escape(state, quote=True)}">{safe_status}</span></summary>
  <div class="pipeline-step-body">{result}{context}{blocks}{transition}</div>
</details>
'''


def _json_files(directory: Path) -> list[tuple[str, Any]]:
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError:
        return []
    return [(path.name, _read_artifact(path)) for path in paths]


def _repair_artifact() -> Any:
    repair = _read_artifact(REPAIR_PATH)
    if repair is not None:
        return repair
    validation = _read_artifact(VALIDATION_PATH)
    if isinstance(validation, Mapping):
        final = validation.get("final") or validation.get("final_validation") or {}
        if isinstance(final, Mapping) and final.get("passed"):
            return {"status": "skipped", "reason": "Initial candidate passed deterministic validation; no repair call was made."}
    return {"status": "unavailable", "reason": "No repair record is available."}


def _field(record: Any, key: str) -> Any | None:
    return record.get(key) if isinstance(record, Mapping) else None


def _run_metadata(record: Any, *fields: str) -> dict[str, Any] | None:
    if not isinstance(record, Mapping):
        return None
    return {field: record.get(field) for field in fields}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _passed_label(passed: Any, issues: int) -> tuple[str, str]:
    if passed is True:
        return "PASS", "passed"
    if passed is False:
        return f"FAIL · {issues} ISSUE{'S' if issues != 1 else ''}", "failed"
    return "NOT RUN", "neutral"


def pipeline_trace() -> str:
    """Build the numbered, collapsed trace from whatever artifacts are available."""

    sources = _read_artifact(SOURCES_PATH)
    manifest = _read_artifact(REVIEW_MANIFEST_PATH)
    scraped = _json_files(SCRAPED_REVIEWS_DIR)
    extraction = _read_artifact(EXTRACTIONS_PATH)
    normalized = _read_artifact(NORMALIZED_EVIDENCE_PATH)
    generation = _read_artifact(GENERATION_PATH)
    validation = _read_artifact(VALIDATION_PATH)
    factual_audit = _read_artifact(FACTUAL_AUDIT_PATH)
    repair = _repair_artifact()
    polished = _read_artifact(POLISHED_PATH)
    review = _read_artifact(REVIEW_PATH)
    cleanup = _read_artifact(CLEANUP_PATH)
    source_count = len(_mapping(sources).get("sources") or [])
    review_count = len(manifest) if isinstance(manifest, list) else 0
    extraction_output = _mapping(_field(extraction, "extraction"))
    extracted_claims = len(extraction_output.get("claims") or [])
    normalized_claims = len(_mapping(normalized).get("claims") or [])
    generation_words = _mapping(generation).get("word_count")
    initial_python = _mapping(_mapping(validation).get("initial"))
    python_issues = len(initial_python.get("issues") or [])
    python_label, python_state = _passed_label(initial_python.get("passed"), python_issues)
    initial_audit_run = _mapping(_mapping(factual_audit).get("initial"))
    initial_audit = _mapping(initial_audit_run.get("audit"))
    audit_issues = len(initial_audit.get("issues") or [])
    audit_label, audit_state = _passed_label(initial_audit.get("passed"), audit_issues)
    audited_claims = initial_audit.get("audited_claim_count", 0)
    supported_claims = initial_audit.get("supported_claim_count", 0)
    repair_record = _mapping(repair)
    repair_called = repair_record.get("repair_called")
    final_python = _mapping(repair_record.get("final_validation"))
    final_factual = _mapping(repair_record.get("final_factual_audit"))
    final_passed = final_python.get("passed") is True and final_factual.get("passed") is True
    if repair_called is False and final_passed:
        repair_label, repair_state = "REPAIR SKIPPED · FINAL PASS", "passed"
        audit_action = "reused the initial Haiku audit; no Haiku re-audit was needed"
        repair_outcome = (
            "Repair did NOT run. The initial Python and Haiku validations passed, "
            "so this stage made 0 Sonnet repair calls and " + audit_action + ". "
            "Final Python: PASS. Final Haiku: PASS."
        )
    elif repair_called is True and final_passed:
        repair_label, repair_state = "REPAIR RAN · FINAL PASS", "passed"
        repair_outcome = (
            "Repair ran once with Sonnet, followed by final Python validation and "
            "a Haiku factual re-audit. Final Python: PASS. Final Haiku: PASS."
        )
    elif repair_called is True:
        repair_label, repair_state = "REPAIR RAN · FINAL FAIL", "failed"
        repair_outcome = (
            f"Repair ran once. Final Python: {'PASS' if final_python.get('passed') else 'FAIL'}. "
            f"Final Haiku: {'PASS' if final_factual.get('passed') else 'FAIL'}. The article was not publishable."
        )
    else:
        repair_label, repair_state = "NOT COMPLETED", "neutral"
        repair_outcome = "No complete repair decision and final dual-validation result is available."
    return "".join(
        (
            _step(
                1,
                "Discover live Google sources",
                (
                    ("Input: DataForSEO request", {"endpoint": DATAFORSEO_ENDPOINT, "product": PRODUCT, "queries": SEARCH_QUERIES, "location": "United States", "language": "English", "device": "desktop"}),
                    ("Output: selected and ranked sources", sources),
                    ("Raw output: DataForSEO response", _read_artifact(SERP_RAW_PATH)),
                ),
                "Search Google through DataForSEO and preserve the provider response.",
                "Fetch and cache the five selected independent reviews.",
                f"{source_count} SOURCES FOUND" if source_count else "NOT RUN",
                f"Google discovery produced {source_count} unique candidate sources.",
                "passed" if source_count else "neutral",
            ),
            _step(
                2,
                "Scrape and cache five reviews",
                (("Input: selected review URLs", REVIEW_SOURCES), ("Output: scrape manifest", manifest), *[(f"Output: cached source body ({name})", value) for name, value in scraped]),
                "Download each selected page, remove page chrome, normalize its article text, and cache it on disk.",
                "Send the five cached source bodies to Haiku in one evidence-extraction call.",
                f"{review_count}/5 REVIEWS CACHED" if review_count else "NOT RUN",
                f"{review_count} selected independent reviews were scraped and cached on disk.",
                "passed" if review_count == 5 else "failed" if review_count else "neutral",
            ),
            _step(
                3,
                "Extract evidence with one Haiku call",
                (
                    ("Run metadata and token usage", _run_metadata(extraction, "model", "prompt_version", "reviews_sha256", "extracted_at", "usage")),
                    ("System prompt", _field(extraction, "system_prompt")),
                    ("User prompt and source input", _field(extraction, "prompt")),
                    ("Raw Haiku output", _field(extraction, "raw_response")),
                    ("Parsed structured output", _field(extraction, "extraction")),
                ),
                "Use structured JSON output to merge equivalent claims, retain provenance, and expose conflicts.",
                "Normalize the structured evidence deterministically.",
                f"{extracted_claims} CLAIMS EXTRACTED" if extracted_claims else "NOT RUN",
                f"One Haiku extraction produced {extracted_claims} evidence claims from {len(extraction_output.get('sources') or [])} sources.",
                "passed" if extracted_claims else "neutral",
            ),
            _step(
                4,
                "Normalize the evidence",
                (
                    ("Input: parsed Haiku evidence", _field(extraction, "extraction")),
                    ("Deterministic transformation", "Clean fields, validate publisher provenance, deduplicate claims, attach stable claim IDs, count categories, and retain source conflicts."),
                    ("Output: compact normalized evidence", normalized),
                ),
                "This stage uses Python only. It makes no model call.",
                "Give the compact evidence to Sonnet for one final-article candidate.",
                f"{normalized_claims} CLAIMS NORMALIZED" if normalized_claims else "NOT RUN",
                f"Python normalized {normalized_claims} claims and retained {len(_mapping(normalized).get('conflicts') or [])} source conflicts.",
                "passed" if normalized_claims else "neutral",
            ),
            _step(
                5,
                "Generate the article with one Sonnet call",
                (
                    ("Run metadata and token usage", _run_metadata(generation, "model", "prompt_version", "evidence_sha256", "generated_at", "word_count", "usage")),
                    ("System prompt", _field(generation, "system_prompt")),
                    ("User prompt and normalized-evidence input", _field(generation, "prompt")),
                    ("Raw Sonnet output", _field(generation, "article")),
                ),
                "Create one publish-ready Markdown candidate from normalized evidence.",
                "Run deterministic policy, structure, provenance, metadata, and length checks.",
                f"GENERATED · {generation_words} WORDS" if generation_words else "NOT RUN",
                f"Sonnet generated one {generation_words}-word raw candidate." if generation_words else "No Sonnet candidate is available.",
                "passed" if generation_words else "neutral",
            ),
            _step(
                6,
                "Validate deterministically",
                (
                    ("Input: raw Sonnet candidate", _read_artifact(DRAFT_PATH)),
                    ("Validation rules", {"minimum_words": ARTICLE_MIN_WORDS, "maximum_words": ARTICLE_MAX_WORDS, "required_h2_headings": REQUIRED_HEADINGS, "expected_sources": REVIEW_SOURCES, "additional_checks": ["one H1 with brand, model, and review", "meta description length", "no first-hand testing claims", "no em dash or standalone double hyphen", "only approved links", "no current pricing or currency", "exactly three FAQ questions"]}),
                    ("Output: validation report", validation),
                ),
                "Evaluate mechanical requirements without spending model tokens.",
                "Run the Haiku factual-grounding audit before making the combined repair decision.",
                python_label,
                f"Python checked the raw candidate: {initial_python.get('word_count', 0)} words, {python_issues} issues. {'All deterministic checks passed.' if initial_python.get('passed') else 'Repair is required if either validator fails.'}",
                python_state,
            ),
            _step(
                7,
                "Audit factual claims with one Haiku call",
                (
                    ("Input: candidate article and normalized evidence", {"article": _read_artifact(DRAFT_PATH), "evidence": normalized}),
                    ("Run metadata and token usage", _run_metadata(factual_audit, "model", "prompt_version", "evidence_sha256", "final_reused_initial")),
                    ("Initial factual-audit run metadata", _run_metadata(factual_audit.get("initial") if isinstance(factual_audit, Mapping) else None, "article_sha256", "audited_at", "usage")),
                    ("System prompt", _field(factual_audit.get("initial") if isinstance(factual_audit, Mapping) else None, "system_prompt")),
                    ("User prompt and article input", _field(factual_audit.get("initial") if isinstance(factual_audit, Mapping) else None, "prompt")),
                    ("Raw Haiku output", _field(factual_audit.get("initial") if isinstance(factual_audit, Mapping) else None, "raw_response")),
                    ("Parsed factual audit", _field(factual_audit.get("initial") if isinstance(factual_audit, Mapping) else None, "audit")),
                ),
                "Check each factual claim against the normalized evidence and record supported claims and issues.",
                "Repair only when deterministic or factual validation fails; then run final Python and Haiku validation.",
                f"{audit_label} · {supported_claims}/{audited_claims} SUPPORTED",
                f"Haiku checked {audited_claims} factual claims: {supported_claims} supported and {audit_issues} issues. {'The factual audit passed.' if initial_audit.get('passed') else 'The factual audit requires repair.'}",
                audit_state,
            ),
            _step(
                8,
                "Conditional repair and final dual validation",
                (
                    ("Run metadata, decision, and token usage", _run_metadata(repair, "model", "prompt_version", "repair_called", "word_count", "usage")),
                    ("System prompt", _field(repair, "system_prompt")),
                    ("User repair prompt, failed checks, evidence, and article input", _field(repair, "prompt")),
                    ("Raw repair output", _field(repair, "article")),
                    ("Initial validation", _field(repair, "initial_validation")),
                    ("Initial factual audit", _field(repair, "initial_factual_audit")),
                    ("Final Python validation", _field(repair, "final_validation")),
                    ("Final factual-audit run metadata", _run_metadata(factual_audit.get("final") if isinstance(factual_audit, Mapping) else None, "article_sha256", "audited_at", "usage")),
                    ("Final factual-audit system prompt", _field(factual_audit.get("final") if isinstance(factual_audit, Mapping) else None, "system_prompt")),
                    ("Final factual-audit user prompt and article input", _field(factual_audit.get("final") if isinstance(factual_audit, Mapping) else None, "prompt")),
                    ("Final Haiku raw output", _field(factual_audit.get("final") if isinstance(factual_audit, Mapping) else None, "raw_response")),
                    ("Final Haiku factual audit", _field(factual_audit.get("final") if isinstance(factual_audit, Mapping) else None, "audit")),
                ),
                "Make zero calls after a pass, or one surgical Sonnet repair and one Haiku re-audit after a failure. No retry loop is allowed.",
                "Apply deterministic Layer A watermark cleanup to the validated candidate.",
                repair_label,
                repair_outcome,
                repair_state,
            ),
            _step(
                9,
                "Run watermark cleanup",
                (
                    ("Input: validated Markdown candidate", polished),
                    ("Cleanup configuration", {"tool": "guillaumemeyer/watermarks-remover", "mode": "Layer A deterministic Unicode cleanup"}),
                    ("Output: cleanup report", cleanup),
                    ("Output: cleaned review Markdown", review),
                ),
                "Remove or normalize deterministic Unicode watermark artifacts without rewriting factual content.",
                "Render the cleaned Markdown as standalone HTML.",
                "UNCHANGED · 0 ARTIFACTS" if _mapping(cleanup).get("changed") is False else "CLEANED",
                "Layer A cleanup ran and found no removable or replaceable Unicode artifacts." if _mapping(cleanup).get("changed") is False else "Layer A cleanup changed the validated Markdown.",
                "passed",
            ),
            _step(
                10,
                "Render the final HTML",
                (
                    ("Input: cleaned review Markdown", review),
                    ("Renderer configuration", {"format": "standalone HTML5", "title": document_title(review or ""), "meta_description": document_description(review or ""), "debug_trace": "collapsed by default", "output": str(HTML_PATH)}),
                    ("Output", "The final rendered review appears immediately below this trace. The current page is review.html."),
                ),
                "Convert Markdown tables, headings, links, and prose into a local review page with this audit trace.",
                "Review the final article below.",
                "RENDERED",
                f"The final review was rendered to {HTML_PATH.name} and appears below this trace.",
                "passed" if review else "neutral",
            ),
        )
    )


def html_document(
    title: str,
    description: str,
    body: str,
    raw_draft: str = "",
    pipeline_html: str | None = None,
) -> str:
    trace = pipeline_trace() if pipeline_html is None else pipeline_html
    trace_panel = f'''    <section class="pipeline-trace" aria-label="Complete pipeline trace">
      <h2>Pipeline trace</h2>
      <p class="pipeline-intro">Collapsed debug view of the available discovery, evidence, generation, validation, repair, cleanup, and final-output artifacts.</p>
{trace}    </section>
'''
    draft_panel = ""
    if raw_draft:
        draft_panel = f'''    <details class="raw-draft">
      <summary>Initial Sonnet candidate (raw Markdown)</summary>
      <pre>{html.escape(raw_draft, quote=True)}</pre>
    </details>
'''
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
    @media (max-width: 700px) {{ article {{ margin: 0; padding: 24px; box-shadow: none; }} h1 {{ font-size: 1.8rem; }} }}
  </style>
</head>
<body>
  <article>
{trace_panel}{draft_panel}{body}
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
    rendered = html_document(
        document_title(source),
        document_description(source),
        render_body(source),
        raw_draft,
        pipeline_trace(),
    )
    HTML_PATH.write_text(rendered, encoding="utf-8")
    print(f"Rendered HTML review to {HTML_PATH}")


if __name__ == "__main__":
    main()
