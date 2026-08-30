import argparse
import hashlib
import json
from datetime import datetime, timezone

import anthropic
from dotenv import load_dotenv

from review_pipeline.config import (
    EXTRACTION_PROMPT_VERSION,
    EXTRACTIONS_PATH,
    HAIKU_MODEL,
    PRODUCT,
    PROJECT_ROOT,
    SCRAPED_REVIEWS_DIR,
    ensure_data_directories,
)


SYSTEM_PROMPT = """You are a product-review evidence editor.
Extract and consolidate only claims found in the supplied reviews. Preserve source
provenance, distinguish measurements from manufacturer claims and reviewer opinion,
and expose disagreements instead of resolving them. Paraphrase rather than copying
long passages. Return valid JSON only."""

EVIDENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "publisher": {"type": "string"},
                    "url": {"type": "string"},
                    "exact_model_reviewed": {"type": "boolean"},
                    "review_context": {"type": "string"},
                },
                "required": [
                    "publisher",
                    "url",
                    "exact_model_reviewed",
                    "review_context",
                ],
                "additionalProperties": False,
            },
        },
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": [
                            "specification",
                            "measurement",
                            "strength",
                            "weakness",
                            "compatibility",
                            "purchase_advice",
                            "uncertainty",
                        ],
                    },
                    "claim": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": [
                            "measured",
                            "observed",
                            "manufacturer_claim",
                            "reviewer_assessment",
                            "unclear",
                        ],
                    },
                    "sources": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "conditions": {
                        "anyOf": [{"type": "string"}, {"type": "null"}]
                    },
                },
                "required": [
                    "category",
                    "claim",
                    "status",
                    "sources",
                    "conditions",
                ],
                "additionalProperties": False,
            },
        },
        "conflicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "positions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "publisher": {"type": "string"},
                                "claim": {"type": "string"},
                            },
                            "required": ["publisher", "claim"],
                            "additionalProperties": False,
                        },
                    },
                    "editorial_guidance": {"type": "string"},
                },
                "required": ["topic", "positions", "editorial_guidance"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["sources", "claims", "conflicts"],
    "additionalProperties": False,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create one compact evidence brief with Claude Haiku"
    )
    parser.add_argument("--refresh", action="store_true")
    return parser.parse_args()


def load_reviews() -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SCRAPED_REVIEWS_DIR.glob("*.json"))
    ]


def reviews_hash(reviews: list[dict]) -> str:
    identity = [
        {
            "publisher": review["publisher"],
            "url": review["url"],
            "content_sha256": review["content_sha256"],
        }
        for review in reviews
    ]
    encoded = json.dumps(identity, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def review_documents(reviews: list[dict]) -> str:
    documents = []
    for index, review in enumerate(reviews, start=1):
        documents.append(
            "\n".join(
                (
                    f"<review index=\"{index}\">",
                    f"<publisher>{review['publisher']}</publisher>",
                    f"<url>{review['url']}</url>",
                    f"<text>{review['text']}</text>",
                    "</review>",
                )
            )
        )
    return "\n\n".join(documents)


def extraction_prompt(reviews: list[dict]) -> str:
    return f"""Create a compact, normalized evidence brief for this product:

{json.dumps(PRODUCT, indent=2)}

Return exactly one JSON object matching the provided structured-output schema.

Requirements:
- Include every supplied source once in sources, using its exact publisher and URL.
- Keep only decision-useful evidence for a product review, with at most 45 claims.
- Merge equivalent claims and list every supporting publisher instead of repeating them.
- A measured result must name the measuring publisher and conditions when available.
- Treat a specification repeated by a reviewer as a manufacturer claim unless the
  reviewer independently observed or measured it.
- Record disagreements in conflicts and do not pick the most favorable value.
- Do not invent comparisons, prices, scores, specifications, or personal experience.
- Do not include commentary outside the JSON.

SOURCE REVIEWS
{review_documents(reviews)}
"""


def parse_json(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("Haiku response did not contain a JSON object")
    return json.loads(text[start : end + 1])


def cached_extraction(content_hash: str) -> dict | None:
    if not EXTRACTIONS_PATH.exists():
        return None
    cached = json.loads(EXTRACTIONS_PATH.read_text(encoding="utf-8"))
    if not isinstance(cached, dict):
        return None
    expected = (content_hash, HAIKU_MODEL, EXTRACTION_PROMPT_VERSION)
    actual = (
        cached.get("reviews_sha256"),
        cached.get("model"),
        cached.get("prompt_version"),
    )
    return cached if actual == expected else None


def call_haiku(client: anthropic.Anthropic, reviews: list[dict]):
    return client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=8000,
        temperature=0,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": extraction_prompt(reviews)}],
        extra_body={
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": EVIDENCE_SCHEMA,
                }
            }
        },
    )


def message_text(message) -> str:
    return "".join(
        block.text for block in message.content if block.type == "text"
    ).strip()


def validate_extraction(extraction: dict, reviews: list[dict]) -> None:
    expected = {(review["publisher"], review["url"]) for review in reviews}
    actual = {
        (source.get("publisher"), source.get("url"))
        for source in extraction.get("sources", [])
    }
    if actual != expected:
        raise ValueError("Haiku extraction did not preserve the exact source set")
    if not extraction.get("claims"):
        raise ValueError("Haiku extraction returned no evidence claims")
    allowed_publishers = {publisher for publisher, _ in expected}
    for claim in extraction["claims"]:
        publishers = set(claim.get("sources") or [])
        if not publishers or not publishers <= allowed_publishers:
            raise ValueError(f"Invalid claim provenance: {claim.get('claim')}")


def save_extraction(
    content_hash: str,
    extraction: dict,
    message,
    prompt: str,
    raw_response: str,
) -> dict:
    record = {
        "model": HAIKU_MODEL,
        "prompt_version": EXTRACTION_PROMPT_VERSION,
        "reviews_sha256": content_hash,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "usage": {
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
        },
        "system_prompt": SYSTEM_PROMPT,
        "prompt": prompt,
        "raw_response": raw_response,
        "extraction": extraction,
    }
    EXTRACTIONS_PATH.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def main() -> None:
    args = parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    ensure_data_directories()
    reviews = load_reviews()
    if not reviews:
        raise SystemExit("No scraped reviews found; run the collect workflow first")
    content_hash = reviews_hash(reviews)
    cached = None if args.refresh else cached_extraction(content_hash)
    if cached:
        if not cached.get("prompt"):
            cached["system_prompt"] = SYSTEM_PROMPT
            cached["prompt"] = extraction_prompt(reviews)
            cached["raw_response"] = json.dumps(cached["extraction"], indent=2)
            EXTRACTIONS_PATH.write_text(json.dumps(cached, indent=2), encoding="utf-8")
        print(
            f"CACHED compact Haiku evidence: "
            f"{len(cached['extraction'].get('claims', []))} claims"
        )
        return

    prompt = extraction_prompt(reviews)
    message = call_haiku(anthropic.Anthropic(), reviews)
    if message.stop_reason != "end_turn":
        raise RuntimeError(f"Haiku extraction stopped early: {message.stop_reason}")
    raw_response = message_text(message)
    extraction = parse_json(raw_response)
    validate_extraction(extraction, reviews)
    record = save_extraction(
        content_hash,
        extraction,
        message,
        prompt,
        raw_response,
    )
    print(
        f"EXTRACTED compact Haiku evidence: "
        f"{len(record['extraction']['claims'])} claims, "
        f"{record['usage']['input_tokens']} input / "
        f"{record['usage']['output_tokens']} output tokens"
    )
    print(f"Saved compact extraction to {EXTRACTIONS_PATH}")


if __name__ == "__main__":
    main()
