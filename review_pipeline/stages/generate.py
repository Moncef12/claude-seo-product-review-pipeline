import argparse
import hashlib
import json
from datetime import datetime, timezone

import anthropic
from dotenv import load_dotenv

from review_pipeline.config import (
    NORMALIZED_EVIDENCE_PATH,
    OUTPUT_DIR,
    PRODUCT,
    PROJECT_ROOT,
    ensure_data_directories,
)


MODEL = "claude-sonnet-4-6"
PROMPT_VERSION = "z3fc-final-review-v3-grounded"
DRAFT_PATH = OUTPUT_DIR / "draft.md"
GENERATION_PATH = OUTPUT_DIR / "generation.json"

SYSTEM_PROMPT = """You are a rigorous product-review editor.
Write only from the supplied normalized evidence. Never imply first-hand testing.
Distinguish manufacturer claims, observations, measurements, and reviewer opinion.
Disclose conflicts conservatively. Use precise natural prose without filler or
keyword stuffing. Do not use em dashes or double hyphens; rewrite with standard
punctuation or separate sentences. Return publish-ready Markdown only."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the final review with one Claude Sonnet call"
    )
    parser.add_argument("--refresh", action="store_true")
    return parser.parse_args()


def evidence_hash(evidence: dict) -> str:
    serialized = json.dumps(evidence, sort_keys=True).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def generation_prompt(evidence: dict) -> str:
    return f"""Write the final product review for:

{json.dumps(PRODUCT, indent=2)}

Hard requirements:
- Write 950 to 1,050 visible words. This is a strict limit, not a suggestion.
- Be selective. Do not repeat every evidence claim or restate the same fact in
  tables, bullets, and prose.
- Return Markdown only, with no horizontal rules.
- Use "Arzopa Z3FC review" naturally in the H1 and Final Verdict.
- Start with one H1 of about 50 to 60 characters. Never put "Tested" or a
  first-hand claim in the title.
- Follow the H1 with this exact 149-character line:
  `**Meta description:** Arzopa Z3FC review covering its 2.5K 180Hz screen, portable design, gaming performance, connectivity, measured results, limitations, and ideal users.`
- Use these H2 headings exactly once and in this exact order:
  1. Quick Verdict
  2. Review Snapshot
  3. Pros and Cons
  4. Specifications
  5. Design and Portability
  6. Display Quality
  7. Gaming Performance
  8. Connectivity and Everyday Use
  9. How It Compares
  10. Who Should Buy It and Who Should Not
  11. Final Verdict
  12. Frequently Asked Questions
  13. How We Researched This Review
- Quick Verdict: 45 to 60 words answering whether it is worth buying, for whom,
  and the largest compromise.
- Review Snapshot: a five-row table for Overall assessment, Best for, Avoid if,
  Standout feature, and Biggest compromise. Use no invented numeric rating.
- Pros and Cons: exactly three concise bullets in each list.
- Specifications: no more than eight rows. Label manufacturer claims and link the
  measuring publisher beside important independent results.
- Keep each prose H2 section to one compact paragraph of 55 to 85 words, except
  Frequently Asked Questions and How We Researched This Review.
- Body sections: prioritize decision-useful evidence and avoid repeating bullets.
- How It Compares: use only supported category-level comparisons, no invented rival.
- Frequently Asked Questions: exactly three bold questions ending in `?`, followed
  by direct, self-contained answers of no more than 35 words each.
- How We Researched This Review: explicitly say this is an evidence-based synthesis,
  not a hands-on test, then list all five supplied publishers as Markdown links.
  Keep the disclosure before the source list to no more than 35 words.
- Use no more than seven inline source links before the methodology list. Every link
  must use one of the supplied source URLs.
- Do not mention a current selling price or use currency figures.
- Do not write "we tested", "our testing", "hands-on", or equivalent language.
- Do not claim that all reviewers agree unless every supplied source supports it.
- When attributing an assessment to a publisher, preserve the exact meaning and
  evidence status. Do not change "strong at its price point" into "uncommon at this
  size," and do not change `observed` or `manufacturer_claim` into independently
  tested or confirmed.
- Avoid words such as "rare" or "uncommon" unless that exact comparison is supported
  by the normalized evidence. Prefer a neutral description such as "a strong
  specification combination for a portable monitor."
- Weight must remain approximately 1.6 to 1.72 lb, never under 400 g.
- Mini HDMI carries audio and video but does not power the monitor.
- State that AMD FreeSync is supported without assigning an unverified tier or
  connection-specific guarantee.
- Resolve nothing beyond the evidence. If evidence conflicts, state the conflict.
- Before returning, remove repetition and ensure the complete article remains
  inside 950 to 1,050 visible words while retaining all 13 required H2 headings.

COMPACT NORMALIZED EVIDENCE
{json.dumps(evidence, indent=2)}
"""


def current_generation(content_hash: str) -> dict | None:
    if not GENERATION_PATH.exists():
        return None
    cached = json.loads(GENERATION_PATH.read_text(encoding="utf-8"))
    expected = (content_hash, MODEL, PROMPT_VERSION)
    actual = (
        cached.get("evidence_sha256"),
        cached.get("model"),
        cached.get("prompt_version"),
    )
    return cached if actual == expected else None


def call_sonnet(client: anthropic.Anthropic, evidence: dict):
    return client.messages.create(
        model=MODEL,
        max_tokens=2600,
        temperature=0,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": generation_prompt(evidence)}],
    )


def response_text(message) -> str:
    return "".join(
        block.text for block in message.content if block.type == "text"
    ).strip()


def save_generation(
    article: str,
    content_hash: str,
    message,
    prompt: str,
) -> dict:
    record = {
        "model": MODEL,
        "prompt_version": PROMPT_VERSION,
        "evidence_sha256": content_hash,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "word_count": len(article.split()),
        "usage": {
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
        },
        "system_prompt": SYSTEM_PROMPT,
        "prompt": prompt,
        "article": article,
    }
    DRAFT_PATH.write_text(f"{article}\n", encoding="utf-8")
    GENERATION_PATH.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def main() -> None:
    args = parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    ensure_data_directories()
    evidence = json.loads(NORMALIZED_EVIDENCE_PATH.read_text(encoding="utf-8"))
    content_hash = evidence_hash(evidence)
    cached = None if args.refresh else current_generation(content_hash)
    if cached:
        DRAFT_PATH.write_text(f"{cached['article']}\n", encoding="utf-8")
        print(f"CACHED final Sonnet candidate: {cached['word_count']} words")
        return

    prompt = generation_prompt(evidence)
    message = call_sonnet(anthropic.Anthropic(), evidence)
    if message.stop_reason != "end_turn":
        raise RuntimeError(f"Sonnet generation stopped early: {message.stop_reason}")
    record = save_generation(
        response_text(message),
        content_hash,
        message,
        prompt,
    )
    print(
        f"GENERATED final Sonnet candidate: {record['word_count']} words, "
        f"{record['usage']['input_tokens']} input / "
        f"{record['usage']['output_tokens']} output tokens"
    )
    print(f"Saved candidate to {DRAFT_PATH}")


if __name__ == "__main__":
    main()
