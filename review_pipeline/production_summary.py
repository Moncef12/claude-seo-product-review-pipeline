"""Pure, inspectable accounting for one review-pipeline production run."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from review_pipeline.config import (
    CLAUDE_PRICING_USD_PER_MILLION,
    HAIKU_MODEL,
    PRICING_EFFECTIVE_DATE,
    SONNET_MODEL,
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _usage(record: Any) -> dict[str, int]:
    value = _mapping(record)
    usage = _mapping(value.get("usage"))
    return {
        "input_tokens": max(0, _integer(usage.get("input_tokens"))),
        "output_tokens": max(0, _integer(usage.get("output_tokens"))),
    }


def _was_called(record: Any, default: bool = True) -> bool:
    value = _mapping(record)
    usage = _usage(value)
    positive_usage = bool(usage["input_tokens"] or usage["output_tokens"])
    producer_output = bool(value.get("raw_response") or value.get("article") or value.get("plan") or value.get("extraction"))
    if value.get("repair_called") is False and not positive_usage:
        return False
    if "call_count" in value:
        if _integer(value.get("call_count")) > 0:
            return True
        # A previous implementation rewrote cache hits to call_count=0. Keep
        # such artifacts billable when recorded producer usage/output is
        # present, while a zero-usage no-repair record remains zero calls.
        if positive_usage or producer_output:
            return True
        return False
    if positive_usage or producer_output:
        return True
    if value.get("cached") is True or value.get("reused") is True:
        return False
    return default and bool(value.get("article") or value.get("plan") or value.get("extraction"))


def _source_fetch_count(scrape: Any, manifest: Any = None) -> int:
    value = _mapping(scrape)
    for key in ("fetch_count", "source_fetch_count", "calls"):
        if key in value:
            candidate = value[key]
            if isinstance(candidate, list):
                return sum(not _mapping(item).get("cached") for item in candidate)
            return max(0, _integer(candidate))
    if isinstance(scrape, list):
        return sum(not _mapping(item).get("cached") for item in scrape)
    if isinstance(manifest, list):
        return len(manifest)
    return 0


def _audit(record: Any, which: str) -> Mapping[str, Any]:
    value = _mapping(record)
    run = _mapping(value.get(which))
    if run:
        return _mapping(run.get("audit"))
    if which == "initial" and isinstance(value.get("audit"), Mapping):
        return _mapping(value.get("audit"))
    return {}


def _result(audit: Mapping[str, Any]) -> str:
    passed = audit.get("passed")
    if passed is True:
        return "PASS"
    if passed is False:
        return "FAIL"
    return "NOT RUN"


def _call_cost(model: str, usage: Mapping[str, Any]) -> float:
    rates = CLAUDE_PRICING_USD_PER_MILLION.get(model)
    if rates is None:
        lowered = model.casefold()
        if "haiku" in lowered:
            rates = {"input": 1.0, "output": 5.0}
        elif "sonnet" in lowered:
            rates = {"input": 3.0, "output": 15.0}
    if rates is None:
        return 0.0
    if not rates:
        return 0.0
    return (
        _number(usage.get("input_tokens")) * rates["input"]
        + _number(usage.get("output_tokens")) * rates["output"]
    ) / 1_000_000.0


def _claude_call(
    calls: list[dict[str, Any]],
    stage: str,
    record: Any,
    model: str,
    *,
    called: bool | None = None,
) -> None:
    if not isinstance(record, Mapping):
        return
    actual = _was_called(record) if called is None else called
    usage = _usage(record) if actual else {"input_tokens": 0, "output_tokens": 0}
    calls.append(
        {
            "provider": "Anthropic",
            "stage": stage,
            "model": record.get("model") or model,
            "called": actual,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "estimated_cost_usd": round(_call_cost(str(record.get("model") or model), usage), 6),
        }
    )


def build_production_summary(
    discovery: Any = None,
    authority: Any = None,
    scrape: Any = None,
    manifest: Any = None,
    extraction: Any = None,
    plan: Any = None,
    generation: Any = None,
    validation: Any = None,
    factual_audit: Any = None,
    repair: Any = None,
    final_article: str | None = None,
    artifacts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build accounting from recorded artifacts without inventing missing work.

    Callers may pass stage records directly or one ``artifacts`` mapping.  The
    latter is useful in tests and keeps the function independent of filesystem
    paths.  A reused initial audit is counted once by construction.
    """

    if artifacts is not None:
        values = dict(artifacts)
        discovery = values.get("discovery", discovery)
        authority = values.get("authority", authority)
        scrape = values.get("scrape", values.get("sources", scrape))
        manifest = values.get("manifest", manifest)
        extraction = values.get("extraction", extraction)
        plan = values.get("plan", plan)
        generation = values.get("generation", generation)
        validation = values.get("validation", validation)
        factual_audit = values.get("factual_audit", values.get("audit", factual_audit))
        repair = values.get("repair", repair)
        final_article = values.get("final_article", final_article)
    # Be friendly to ``build_production_summary({"generation": ...})``.
    if isinstance(discovery, Mapping) and any(key in discovery for key in ("generation", "validation", "factual_audit")):
        values = dict(discovery)
        discovery = values.get("discovery")
        authority = values.get("authority", authority)
        scrape = values.get("scrape", scrape)
        manifest = values.get("manifest", manifest)
        extraction = values.get("extraction", extraction)
        plan = values.get("plan", plan)
        generation = values.get("generation", generation)
        validation = values.get("validation", validation)
        factual_audit = values.get("factual_audit", values.get("audit", factual_audit))
        repair = values.get("repair", repair)
        final_article = values.get("final_article", final_article)

    collection = _mapping(_mapping(discovery).get("collection"))
    discovery_tasks = collection.get("tasks") or collection.get("calls") or []
    if isinstance(discovery_tasks, list):
        # Per-article provenance counts every stored SERP task, including one
        # reused from cache during the current execution.
        discovery_calls = sum(bool(_mapping(task)) for task in discovery_tasks)
        discovery_cost = sum(_number(_mapping(task).get("cost")) for task in discovery_tasks)
    else:
        discovery_calls, discovery_cost = 0, 0.0
    if not discovery_calls and "search_call_count" in collection:
        discovery_calls = max(0, _integer(collection.get("search_call_count")))
    elif not discovery_calls and "call_count" in collection:
        discovery_calls = max(0, _integer(collection.get("call_count")))
    recorded_total = collection.get("total_dataforseo_cost")
    authority_value = _mapping(authority)
    if not authority_value:
        authority_value = _mapping(_mapping(discovery).get("authority"))
    authority_stored = bool(authority_value) and bool(
        authority_value.get("raw_response") is not None
        or authority_value.get("targets")
        or authority_value.get("target_set_sha256")
        or authority_value.get("cost") is not None
        or authority_value.get("call_count") is not None
        or authority_value.get("called") is not None
        or authority_value.get("tasks")
    )
    # Shared authority data may be reused across products without a provider
    # request. Prefer the per-run call marker; fall back to one call only for
    # legacy artifacts that predate the shared domain database.
    if "call_count" in authority_value:
        authority_calls = max(0, _integer(authority_value.get("call_count")))
    elif "called" in authority_value:
        authority_calls = int(authority_value.get("called") is True)
    else:
        authority_calls = 1 if authority_stored else 0
    authority_cost = _number(authority_value.get("cost"))
    if not authority_cost:
        authority_cost = _number(_mapping(authority_value.get("raw_response")).get("cost"))
    if recorded_total is not None:
        dataforseo_cost = _number(recorded_total)
        if not collection.get("total_cost_includes_authority"):
            dataforseo_cost += authority_cost
    else:
        dataforseo_cost = discovery_cost + authority_cost
    dataforseo_calls = discovery_calls + authority_calls

    claude_calls: list[dict[str, Any]] = []
    _claude_call(claude_calls, "extraction", extraction, HAIKU_MODEL)
    _claude_call(claude_calls, "plan", plan, HAIKU_MODEL)
    _claude_call(claude_calls, "generation", generation, SONNET_MODEL)
    initial_run = _mapping(_mapping(factual_audit).get("initial"))
    final_run = _mapping(_mapping(factual_audit).get("final"))
    if not initial_run and isinstance(_mapping(factual_audit).get("audit"), Mapping):
        initial_run = _mapping(factual_audit)
    _claude_call(claude_calls, "initial_factual_audit", initial_run, HAIKU_MODEL)
    final_reused = bool(_mapping(factual_audit).get("final_reused_initial") or _mapping(final_run).get("reused_initial"))
    if final_run and not final_reused:
        _claude_call(claude_calls, "final_factual_audit", final_run, HAIKU_MODEL)
    repair_value = _mapping(repair)
    if repair_value.get("repair_called"):
        _claude_call(claude_calls, "repair", repair_value, SONNET_MODEL)

    claude_input = sum(call["input_tokens"] for call in claude_calls if call["called"])
    claude_output = sum(call["output_tokens"] for call in claude_calls if call["called"])
    claude_cost = sum(call["estimated_cost_usd"] for call in claude_calls if call["called"])
    source_fetches = _source_fetch_count(scrape, manifest)
    total_provider_calls = dataforseo_calls + sum(call["called"] for call in claude_calls)
    total_calls = total_provider_calls

    validation_value = _mapping(validation)
    initial_python = _mapping(validation_value.get("initial"))
    final_python = _mapping(validation_value.get("final")) or _mapping(repair_value.get("final_validation"))
    initial_audit = _audit(factual_audit, "initial")
    final_audit = _audit(factual_audit, "final")
    if not final_audit and final_reused:
        final_audit = initial_audit
    if not final_python:
        final_python = initial_python
    repair_required = not (initial_python.get("passed") is True and initial_audit.get("passed") is True)
    # Prefer the deterministic validator's Markdown-aware count. A plain
    # ``split`` count includes link destinations and Markdown syntax, so it can
    # disagree with the publishing gate displayed beside this value.
    final_words = (
        _integer(final_python.get("word_count"))
        or _integer(repair_value.get("word_count"))
        or _integer(_mapping(generation).get("word_count"))
    )
    if not final_words and final_article is not None:
        final_words = len(str(final_article).split())

    summary = {
        "generated_at": datetime_now(),
        "total_calls": total_calls,
        "total_external_api_calls": total_calls,
        "total_provider_calls": total_provider_calls,
        "total_external_operations": total_provider_calls + source_fetches,
        "calls": {
            "dataforseo": dataforseo_calls,
            "anthropic": sum(call["called"] for call in claude_calls),
            "source_fetches": source_fetches,
        },
        "calls_by_provider_stage": {
            "DataForSEO": {
                "discovery": discovery_calls,
                "authority_bulk_ranks": authority_calls,
                "total": dataforseo_calls,
            },
            "Anthropic": {
                "stages": claude_calls,
                "total": sum(call["called"] for call in claude_calls),
            },
            "source_fetches": source_fetches,
        },
        "tokens": {"input": claude_input, "output": claude_output, "total": claude_input + claude_output},
        "total_input_tokens": claude_input,
        "total_output_tokens": claude_output,
        "dataforseo": {"calls": dataforseo_calls, "recorded_cost_usd": round(dataforseo_cost, 6)},
        "validation": {
            "initial_python": _result(initial_python),
            "final_python": _result(final_python),
            "initial_haiku": _result(initial_audit),
            "final_haiku": _result(final_audit),
            "initial_haiku_supported": _integer(initial_audit.get("supported_claim_count")),
            "initial_haiku_audited": _integer(initial_audit.get("audited_claim_count")),
            "final_haiku_supported": _integer(final_audit.get("supported_claim_count")),
            "final_haiku_audited": _integer(final_audit.get("audited_claim_count")),
        },
        "repair": {
            "required": repair_required,
            "called": bool(repair_value.get("repair_called")),
            "status": "called" if repair_value.get("repair_called") else "skipped" if repair_value else "not recorded",
        },
        "final_word_count": final_words,
        "cost": {
            "estimated_claude_standard_api_usd": round(claude_cost, 6),
            "recorded_dataforseo_usd": round(dataforseo_cost, 6),
            "estimated_total_usd": round(claude_cost + dataforseo_cost, 6),
            "pricing_basis": {
                "effective_date": PRICING_EFFECTIVE_DATE,
                "rates_usd_per_million_tokens": CLAUDE_PRICING_USD_PER_MILLION,
            },
        },
        "pricing_basis": f"Anthropic standard API rates dated {PRICING_EFFECTIVE_DATE}; DataForSEO uses recorded task costs.",
        "exclusions": "No live calls are made by summary generation. Estimates exclude hosting, browser rendering, taxes, discounts, and any provider pricing not recorded in the artifacts.",
    }
    # The top-level aliases make the compact card and simple integrations easy,
    # while the nested sections retain enough detail for an audit.
    summary["estimated_total_usd"] = summary["cost"]["estimated_total_usd"]
    summary["estimated_claude_cost_usd"] = summary["cost"]["estimated_claude_standard_api_usd"]
    summary["estimated_total_cost_usd"] = summary["estimated_total_usd"]
    summary["total_api_calls"] = total_provider_calls
    summary["total_claude_calls"] = sum(call["called"] for call in claude_calls)
    summary["source_fetch_count"] = source_fetches
    summary["initial_python_result"] = summary["validation"]["initial_python"]
    summary["final_python_result"] = summary["validation"]["final_python"]
    summary["initial_haiku_result"] = summary["validation"]["initial_haiku"]
    summary["final_haiku_result"] = summary["validation"]["final_haiku"]
    summary["initial_haiku_supported_count"] = summary["validation"]["initial_haiku_supported"]
    summary["initial_haiku_audited_count"] = summary["validation"]["initial_haiku_audited"]
    summary["final_haiku_supported_count"] = summary["validation"]["final_haiku_supported"]
    summary["final_haiku_audited_count"] = summary["validation"]["final_haiku_audited"]
    summary["repair_required"] = repair_required
    summary["repair_called"] = bool(repair_value.get("repair_called"))
    return summary


def datetime_now() -> str:
    # Imported lazily so the pure builder remains straightforward to monkeypatch
    # in deterministic tests.
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def build_summary(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return build_production_summary(*args, **kwargs)


def write_summary(path: Any, summary: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(summary), indent=2), encoding="utf-8")


__all__ = ["build_production_summary", "build_summary", "write_summary"]
