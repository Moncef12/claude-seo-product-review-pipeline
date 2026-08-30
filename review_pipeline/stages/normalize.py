import hashlib
import json
import re

from review_pipeline.config import (
    EXTRACTIONS_PATH,
    NORMALIZED_EVIDENCE_PATH,
    PRODUCT,
    ensure_data_directories,
)


ALLOWED_CATEGORIES = {
    "specification",
    "measurement",
    "strength",
    "weakness",
    "compatibility",
    "purchase_advice",
    "uncertainty",
}
ALLOWED_STATUSES = {
    "measured",
    "observed",
    "manufacturer_claim",
    "reviewer_assessment",
    "unclear",
}


def clean_text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def claim_id(category: str, claim: str, sources: list[str]) -> str:
    value = f"{category}|{claim}|{'|'.join(sorted(sources))}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:16]


def normalize_sources(extraction: dict) -> tuple[list[dict], dict[str, str]]:
    sources = []
    urls = {}
    for source in extraction.get("sources", []):
        publisher = clean_text(source.get("publisher"))
        url = clean_text(source.get("url"))
        if not publisher or not url:
            raise ValueError("Evidence source is missing publisher or URL")
        urls[publisher] = url
        sources.append(
            {
                "publisher": publisher,
                "url": url,
                "exact_model_reviewed": bool(source.get("exact_model_reviewed")),
                "review_context": clean_text(source.get("review_context")),
            }
        )
    return sources, urls


def normalize_claims(extraction: dict, source_urls: dict[str, str]) -> list[dict]:
    output = []
    seen = set()
    for item in extraction.get("claims", []):
        claim = clean_text(item.get("claim"))
        category = clean_text(item.get("category")).lower()
        status = clean_text(item.get("status")).lower()
        publishers = sorted(
            {
                clean_text(publisher)
                for publisher in item.get("sources") or []
                if clean_text(publisher)
            }
        )
        if not claim or category not in ALLOWED_CATEGORIES:
            continue
        if status not in ALLOWED_STATUSES:
            status = "unclear"
        if not publishers or any(name not in source_urls for name in publishers):
            raise ValueError(f"Claim has invalid provenance: {claim}")
        key = (category, claim.casefold(), tuple(publishers))
        if key in seen:
            continue
        seen.add(key)
        output.append(
            {
                "id": claim_id(category, claim, publishers),
                "category": category,
                "claim": claim,
                "status": status,
                "sources": publishers,
                "conditions": clean_text(item.get("conditions")) or None,
            }
        )
    return output


def normalize_conflicts(extraction: dict, source_urls: dict[str, str]) -> list[dict]:
    output = []
    for conflict in extraction.get("conflicts", []):
        positions = []
        for position in conflict.get("positions") or []:
            publisher = clean_text(position.get("publisher"))
            claim = clean_text(position.get("claim"))
            if publisher in source_urls and claim:
                positions.append(
                    {
                        "publisher": publisher,
                        "claim": claim,
                    }
                )
        topic = clean_text(conflict.get("topic"))
        if topic and len(positions) >= 2:
            output.append(
                {
                    "topic": topic,
                    "positions": positions,
                    "editorial_guidance": clean_text(
                        conflict.get("editorial_guidance")
                    ),
                }
            )
    return output


def category_counts(claims: list[dict]) -> dict[str, int]:
    counts = {}
    for claim in claims:
        category = claim["category"]
        counts[category] = counts.get(category, 0) + 1
    return dict(sorted(counts.items()))


def normalize(record: dict) -> dict:
    extraction = record.get("extraction") or {}
    sources, source_urls = normalize_sources(extraction)
    claims = normalize_claims(extraction, source_urls)
    return {
        "product": PRODUCT,
        "source_count": len(sources),
        "all_sources_confirm_exact_model": all(
            source["exact_model_reviewed"] for source in sources
        ),
        "category_counts": category_counts(claims),
        "sources": sources,
        "claims": claims,
        "conflicts": normalize_conflicts(extraction, source_urls),
    }


def main() -> None:
    ensure_data_directories()
    record = json.loads(EXTRACTIONS_PATH.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or "extraction" not in record:
        raise SystemExit("Extraction cache uses the legacy format; rerun extraction")
    output = normalize(record)
    NORMALIZED_EVIDENCE_PATH.write_text(
        json.dumps(output, indent=2), encoding="utf-8"
    )
    print(
        f"Normalized {len(output['claims'])} compact claims and "
        f"{len(output['conflicts'])} conflicts from {output['source_count']} sources "
        f"to {NORMALIZED_EVIDENCE_PATH}"
    )


if __name__ == "__main__":
    main()
