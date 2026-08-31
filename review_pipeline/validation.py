"""Deterministic validation for evidence-based product-review Markdown.

The validator deliberately does not ask an LLM to judge formatting or policy
requirements.  It returns plain dictionaries so callers can persist the report
as JSON or use it to decide whether a single repair call is warranted.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urldefrag, urlsplit, urlunsplit


DEFAULT_WORD_MIN = 800
DEFAULT_WORD_MAX = 1_200
DEFAULT_META_MIN = 145
DEFAULT_META_MAX = 160

_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?!#)[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_MARKDOWN_LINK_RE = re.compile(r"!?(?:\[[^\]]*\])\((https?://[^)\s]+)\)")
_HTML_LINK_RE = re.compile(r"\bhref\s*=\s*[\"'](https?://[^\"']+)", re.IGNORECASE)
_AUTOLINK_RE = re.compile(r"<((?:https?://)[^>]+)>")
_URL_RE = re.compile(r"https?://[^\s)\]>\"']+", re.IGNORECASE)
_WORD_RE = re.compile(r"\b[\w]+(?:['’][\w]+)?\b", re.UNICODE)

_FIRST_HAND_PATTERNS = (
    re.compile(r"\b(?:i|we)\s+(?:personally\s+)?(?:test(?:ed|ing)?|use(?:d|ing)?|measur(?:ed|ing)|review(?:ed|ing)|evaluat(?:ed|ing)|tri(?:ed|ed)|found)\b", re.IGNORECASE),
    re.compile(r"\b(?:our|my)\s+(?:hands?[- ]on|test(?:ing)?|measurements?|review)\b", re.IGNORECASE),
    re.compile(r"\b(?:hands?[- ]on|first[- ]hand|in[- ]house)\s+(?:test(?:ing)?|experience|review)\b", re.IGNORECASE),
    re.compile(r"\b(?:i|we)\s+(?:ran|put|connected|played|spent)\b", re.IGNORECASE),
)
_CURRENCY_RE = re.compile(
    r"(?:[$€£¥]\s*\d|\b\d[\d,]*(?:\.\d+)?\s*(?:USD|EUR|GBP|CAD|AUD|dollars?|euros?|pounds?\s+sterling)\b)",
    re.IGNORECASE,
)
_CURRENT_PRICE_RE = re.compile(
    r"\b(?:current(?:ly)?|today(?:'s)?|now)\s+(?:price|pricing|cost)\b"
    r"|\b(?:priced|sells?|costs)\s+(?:at|for)\b",
    re.IGNORECASE,
)


def _normalize_heading(value: str) -> str:
    """Normalize an ATX heading for comparison."""

    value = re.sub(r"^\s*#{1,6}\s*", "", str(value))
    value = re.sub(r"\s+#+\s*$", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip().casefold()


def _headings(markdown_text: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for match in _HEADING_RE.finditer(markdown_text):
        level = len(match.group(1))
        title = re.sub(r"\s+#+\s*$", "", match.group(2)).strip()
        found.append({"level": level, "title": title, "normalized": _normalize_heading(title), "start": match.start(), "end": match.end()})
    return found


def _strip_for_word_count(markdown_text: str) -> str:
    # Fenced examples are not article prose.  Link destinations and HTML tags
    # are also not visible words, while link labels remain countable.
    text = re.sub(r"```.*?```|~~~.*?~~~", " ", markdown_text, flags=re.DOTALL)
    text = _MARKDOWN_LINK_RE.sub(lambda m: m.group(0).split("](", 1)[0].lstrip("![]"), text)
    text = _AUTOLINK_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return text


def _word_count(markdown_text: str) -> int:
    return len(_WORD_RE.findall(_strip_for_word_count(markdown_text)))


def _normalize_url(url: str) -> str:
    url = urldefrag(url.strip())[0]
    parts = urlsplit(url)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), path, parts.query, ""))


def _source_parts(source: Any) -> tuple[str, str]:
    if isinstance(source, Mapping):
        return str(source.get("publisher", "")), str(source.get("url", ""))
    if isinstance(source, Sequence) and not isinstance(source, (str, bytes)) and len(source) >= 2:
        return str(source[0]), str(source[1])
    raise TypeError("expected_sources entries must be mappings or (publisher, url) pairs")


def _methodology_section(markdown_text: str, headings: list[dict[str, Any]], requested: str | None) -> tuple[str | None, str | None]:
    candidates = []
    requested_normalized = _normalize_heading(requested) if requested else None
    for index, heading in enumerate(headings):
        if heading["level"] != 2:
            continue
        if requested_normalized:
            matches = heading["normalized"] == requested_normalized
        else:
            matches = "methodolog" in heading["normalized"] or "research" in heading["normalized"]
        if matches:
            next_h2 = next((item for item in headings[index + 1 :] if item["level"] <= 2), None)
            end = next_h2["start"] if next_h2 else len(markdown_text)
            return markdown_text[heading["end"] : end], heading["title"]
        candidates.append(heading)
    return None, None


def _faq_count(markdown_text: str, headings: list[dict[str, Any]]) -> tuple[int, str | None]:
    faq_heading = next(
        (heading for heading in headings if heading["level"] == 2 and "faq" in heading["normalized"] or heading["level"] == 2 and "frequently asked question" in heading["normalized"]),
        None,
    )
    if faq_heading is None:
        return 0, None
    heading_index = headings.index(faq_heading)
    next_h2 = next((item for item in headings[heading_index + 1 :] if item["level"] <= 2), None)
    section = markdown_text[faq_heading["end"] : next_h2["start"] if next_h2 else len(markdown_text)]
    questions = 0
    for line in section.splitlines():
        line = re.sub(r"^\s*[-*+]\s+", "", line).strip()
        if not line:
            continue
        # FAQ prompts are conventionally standalone bold paragraphs.  Accept
        # plain standalone question lines too, which keeps this check portable.
        plain = re.sub(r"[*_`]", "", line).strip()
        if plain.endswith("?"):
            questions += 1
    return questions, faq_heading["title"]


def _issue(code: str, message: str, **details: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"code": code, "message": message}
    if details:
        result["details"] = details
    return result


def _section_text(
    markdown_text: str,
    headings: list[dict[str, Any]],
    requested: str,
) -> str:
    requested_normalized = _normalize_heading(requested)
    for index, heading in enumerate(headings):
        if heading["level"] != 2 or heading["normalized"] != requested_normalized:
            continue
        next_h2 = next((item for item in headings[index + 1 :] if item["level"] <= 2), None)
        return markdown_text[heading["end"] : next_h2["start"] if next_h2 else len(markdown_text)]
    return ""


def validate_editorial_contract(
    markdown_text: str,
    headings: list[dict[str, Any]],
    editorial_plan: Mapping[str, Any] | None = None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Check decision support without requiring one exact generated sentence.

    The optional plan flag intentionally gates these checks so the long-standing
    ``validate_markdown`` callers remain backward compatible.  The checks look
    for reader-facing decisions and labels, not fragile wording copied from a
    prompt or planning model.
    """

    if editorial_plan is None:
        return {}, []
    checks: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, Any]] = []
    quick = _section_text(markdown_text, headings, "Quick Verdict")
    quick_passed = bool(
        quick
        and re.search(
            r"\b(?:recommend(?:ed|ation)?|worth|buy(?:ing)?|choose|skip|avoid|good fit|not for|suits?|strong\s+(?:choice|option|fit|pick|value|portable\s+monitor))\b",
            quick,
            re.IGNORECASE,
        )
    )
    checks["quick_verdict_decision"] = {"passed": quick_passed, "section_present": bool(quick)}
    if not quick_passed:
        issues.append(_issue("quick_verdict_decision", "Quick Verdict must contain a clear recommendation or conditional buying decision."))

    snapshot = _section_text(markdown_text, headings, "Review Snapshot")
    snapshot_labels = {
        "best_for": bool(re.search(r"\bbest\s+for\b", snapshot, re.IGNORECASE)),
        "avoid_if": bool(re.search(r"\bavoid\s+if\b", snapshot, re.IGNORECASE)),
        "biggest_compromise": bool(re.search(r"\bbiggest\s+compromise\b", snapshot, re.IGNORECASE)),
    }
    snapshot_passed = bool(snapshot) and all(snapshot_labels.values())
    checks["review_snapshot_decision_labels"] = {"passed": snapshot_passed, **snapshot_labels, "section_present": bool(snapshot)}
    if not snapshot_passed:
        issues.append(_issue("review_snapshot_decision_labels", "Review Snapshot must label Best for, Avoid if, and Biggest compromise."))

    audience = _section_text(markdown_text, headings, "Who Should Buy It and Who Should Not")
    audience_passed = bool(
        audience
        and re.search(r"\b(?:should\s+buy|buy\s+it|best\s+for|ideal\s+for|suits?)\b", audience, re.IGNORECASE)
        and re.search(r"\b(?:should\s+not|do\s+not\s+buy|avoid|not\s+for|skip)\b", audience, re.IGNORECASE)
    )
    checks["buyer_fit_guidance"] = {"passed": audience_passed, "section_present": bool(audience)}
    if not audience_passed:
        issues.append(_issue("buyer_fit_guidance", "The article must explain who should buy and who should avoid the product."))

    final_verdict = _section_text(markdown_text, headings, "Final Verdict")
    visible_final = re.sub(r"[*_`\[\]()]", " ", final_verdict).strip()
    final_sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", visible_final)
        if sentence.strip()
    ]
    final_sentence = final_sentences[-1] if final_sentences else ""
    conversion_passed = bool(
        final_sentence
        and re.search(
            r"\b(?:next\s+step|next\s+move|consider|choose|check|buy|purchase|look\s+for|see\s+whether|go\s+ahead)\b",
            final_sentence,
            re.IGNORECASE,
        )
    )
    checks["final_conversion_cue"] = {
        "passed": conversion_passed,
        "section_present": bool(final_verdict),
        "final_sentence": final_sentence,
    }
    if not conversion_passed:
        issues.append(_issue("final_conversion_cue", "Final Verdict must end with a natural next-step or conversion cue."))
    return checks, issues


def _first_hand_matches(markdown_text: str) -> list[str]:
    """Find first-person testing claims while allowing a methodology disclaimer."""

    matches: list[str] = []
    for pattern in _FIRST_HAND_PATTERNS:
        for match in pattern.finditer(markdown_text):
            before = markdown_text[max(0, match.start() - 24) : match.start()].casefold()
            # Reader-facing FAQ questions such as “Can I use this for photo
            # editing?” are not author claims of product use.
            if re.search(r"\b(?:can|could|should|would|may|might|do)\s+$", before):
                continue
            # Negative disclosures such as “not a hands-on test” are evidence
            # methodology, not a claim that this publication tested the product.
            if re.search(
                r"\b(?:no|not|without)\s+(?:(?:an?|any|original|our|my|actual)\s+){0,3}$",
                before,
            ):
                continue
            matches.append(match.group(0))
    return matches


def validate_markdown(
    markdown_text: str,
    product: Mapping[str, Any] | None = None,
    required_headings: Sequence[str] = (),
    expected_sources: Sequence[Any] = (),
    *,
    brand: str | None = None,
    model: str | None = None,
    methodology_heading: str | None = None,
    min_words: int = DEFAULT_WORD_MIN,
    max_words: int = DEFAULT_WORD_MAX,
    meta_min: int = DEFAULT_META_MIN,
    meta_max: int = DEFAULT_META_MAX,
    editorial_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a review and return a JSON-serializable report.

    ``product`` may contain ``brand`` and ``model``; explicit ``brand`` and
    ``model`` keyword arguments take precedence.  Sources may be mappings with
    ``publisher``/``url`` keys or two-item pairs.
    """

    if product:
        brand = brand or str(product.get("brand", ""))
        model = model or str(product.get("model", ""))
    brand = str(brand or "").strip()
    model = str(model or "").strip()
    headings = _headings(markdown_text)
    h1s = [heading for heading in headings if heading["level"] == 1]
    h2s = [heading for heading in headings if heading["level"] == 2]
    issues: list[dict[str, Any]] = []
    checks: dict[str, dict[str, Any]] = {}

    words = _word_count(markdown_text)
    checks["word_count"] = {"passed": min_words <= words <= max_words, "value": words, "minimum": min_words, "maximum": max_words}
    if not checks["word_count"]["passed"]:
        issues.append(_issue("word_count", f"Article has {words} words; expected {min_words}-{max_words}.", value=words, minimum=min_words, maximum=max_words))

    product_tokens = [token.casefold() for token in (brand, model) if token]
    valid_h1 = len(h1s) == 1 and all(token in h1s[0]["title"].casefold() for token in product_tokens) and bool(re.search(r"\breview\b", h1s[0]["title"], re.IGNORECASE))
    checks["h1"] = {"passed": valid_h1, "count": len(h1s), "title": h1s[0]["title"] if h1s else None}
    if not valid_h1:
        issues.append(_issue("h1", "The article must have exactly one H1 containing the brand, model, and the word 'review'.", count=len(h1s)))

    if h1s and re.search(r"\btested\b", h1s[0]["title"], re.IGNORECASE):
        checks["h1_tested"] = {"passed": False}
        issues.append(_issue("h1_tested", "The H1 must not imply first-hand testing."))
    else:
        checks["h1_tested"] = {"passed": True}

    required = [_normalize_heading(value) for value in required_headings]
    h2_names = [heading["normalized"] for heading in h2s]
    positions: list[int | None] = []
    cursor = 0
    for expected in required:
        try:
            position = h2_names.index(expected, cursor)
        except ValueError:
            position = None
        positions.append(position)
        if position is not None:
            cursor = position + 1
    required_passed = all(position is not None for position in positions)
    checks["required_headings"] = {"passed": required_passed, "expected": list(required_headings), "positions": positions}
    if not required_passed:
        issues.append(_issue("required_headings", "Required H2 headings are missing or out of order.", expected=list(required_headings), positions=positions))

    duplicates = sorted({name for name in h2_names if h2_names.count(name) > 1})
    checks["duplicate_h2"] = {"passed": not duplicates, "duplicates": duplicates}
    if duplicates:
        issues.append(_issue("duplicate_h2", "H2 headings must not be duplicated.", duplicates=duplicates))

    meta_match = re.search(r"(?:^|\n)\s*(?:\*\*)?meta description(?:\*\*)?\s*:\s*(.+?)\s*(?=\n|$)", markdown_text, re.IGNORECASE)
    meta = meta_match.group(1).strip() if meta_match else None
    meta_length = len(meta) if meta is not None else 0
    checks["meta_description"] = {"passed": meta is not None and meta_min <= meta_length <= meta_max, "present": meta is not None, "length": meta_length, "minimum": meta_min, "maximum": meta_max}
    if not checks["meta_description"]["passed"]:
        issues.append(_issue("meta_description", f"Meta description must be present and {meta_min}-{meta_max} characters.", length=meta_length))

    first_hand_matches = _first_hand_matches(markdown_text)
    checks["first_hand_testing"] = {"passed": not first_hand_matches, "matches": first_hand_matches}
    if first_hand_matches:
        issues.append(_issue("first_hand_testing", "The article contains first-hand testing language.", matches=first_hand_matches))

    checks["em_dash"] = {"passed": "—" not in markdown_text}
    if not checks["em_dash"]["passed"]:
        issues.append(_issue("em_dash", "Em dash characters are not allowed."))

    no_urls = _URL_RE.sub(" ", markdown_text)
    double_hyphens = [match.group(0) for match in re.finditer(r"(?<!-)(?<!\w)--(?!-)", no_urls)]
    checks["double_hyphen"] = {"passed": not double_hyphens, "matches": double_hyphens}
    if double_hyphens:
        issues.append(_issue("double_hyphen", "Standalone double hyphens are not allowed.", matches=double_hyphens))

    methodology, methodology_title = _methodology_section(markdown_text, headings, methodology_heading)
    parsed_sources = [_source_parts(source) for source in expected_sources]
    source_results = []
    for publisher, url in parsed_sources:
        in_methodology = methodology is not None and publisher.casefold() in methodology.casefold() and _normalize_url(url) in {_normalize_url(found) for found in _URL_RE.findall(methodology)}
        source_results.append({"publisher": publisher, "url": url, "present": in_methodology})
    sources_passed = all(result["present"] for result in source_results)
    checks["methodology_sources"] = {"passed": sources_passed, "heading": methodology_title, "sources": source_results}
    if not sources_passed:
        issues.append(_issue("methodology_sources", "Every expected publisher and URL must appear in the methodology section.", missing=[result for result in source_results if not result["present"]]))

    allowed_urls = {_normalize_url(url) for _, url in parsed_sources}
    article_links = _MARKDOWN_LINK_RE.findall(markdown_text) + _HTML_LINK_RE.findall(markdown_text) + _AUTOLINK_RE.findall(markdown_text)
    unauthorized = sorted(
        {
            _normalize_url(url)
            for url in article_links
            if _normalize_url(url) not in allowed_urls
        }
    )
    checks["article_links"] = {"passed": not unauthorized, "links": article_links, "unauthorized": unauthorized}
    if unauthorized:
        issues.append(_issue("article_links", "Article links must use only expected source URLs.", unauthorized=unauthorized))

    currency_matches = sorted(set(match.group(0) for match in _CURRENCY_RE.finditer(markdown_text)))
    price_matches = sorted(set(match.group(0) for match in _CURRENT_PRICE_RE.finditer(markdown_text)))
    checks["currency_price_claims"] = {"passed": not currency_matches and not price_matches, "currency_matches": currency_matches, "price_matches": price_matches}
    if not checks["currency_price_claims"]["passed"]:
        issues.append(_issue("currency_price_claims", "Currency amounts and current-price claims are not allowed.", currency_matches=currency_matches, price_matches=price_matches))

    faq_count, faq_title = _faq_count(markdown_text, headings)
    checks["faq_questions"] = {"passed": faq_count == 3, "count": faq_count, "heading": faq_title, "expected": 3}
    if not checks["faq_questions"]["passed"]:
        issues.append(_issue("faq_questions", "The article must contain exactly three FAQ questions.", count=faq_count))

    editorial_checks, editorial_issues = validate_editorial_contract(
        markdown_text,
        headings,
        editorial_plan,
    )
    checks.update(editorial_checks)
    issues.extend(editorial_issues)

    report = {"passed": not issues, "word_count": words, "checks": checks, "issues": issues}
    return report


# A descriptive alias for callers that prefer article-oriented terminology.
validate_review = validate_markdown


validate_article = validate_markdown


__all__ = [
    "validate_markdown",
    "validate_review",
    "validate_article",
    "validate_editorial_contract",
]
