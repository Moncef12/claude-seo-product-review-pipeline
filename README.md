# Claude SEO Product Review Pipeline

`claude-seo-product-review-pipeline` is a working reference implementation for producing an evidence-grounded SEO product review from live search results and independent source material. It combines DataForSEO discovery, cached web extraction, Claude Haiku and Sonnet, deterministic Python validation, and an inspectable HTML audit trail.

The current demo reviews the **Arzopa Z3FC portable monitor** using five independent publications. It is intentionally a focused, end-to-end example rather than a generic multi-product service.

- Live demo: [Arzopa Z3FC review and pipeline audit](https://arzopa-z3fc-review-workflow.vercel.app/review.html)
- GitHub repository: [Moncef12/claude-seo-product-review-pipeline](https://github.com/Moncef12/claude-seo-product-review-pipeline)

## Quick start

Requirements: Python 3.10+, Git, an Anthropic API key, and DataForSEO credentials.

```bash
git clone https://github.com/Moncef12/claude-seo-product-review-pipeline.git
cd claude-seo-product-review-pipeline
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
cp .env.example .env
```

Set both values in `.env`:

```dotenv
ANTHROPIC_API_KEY=your-anthropic-api-key
DATAFORSEO_AUTH_BASE64=base64-encoded-login-colon-password
```

Run the complete pipeline and write the files without opening a browser:

```bash
python3 pipeline.py all --no-serve
```

Or run it interactively:

```bash
python3 pipeline.py all
```

The interactive command starts a local server, opens the generated review in Chrome, and remains active until `Ctrl+C`. It normally serves `http://127.0.0.1:8000/review.html`; if port 8000 is occupied, it selects another port.

Collection and generation can also run separately:

```bash
python3 pipeline.py collect
python3 pipeline.py generate
python3 pipeline.py generate --no-serve
python3 pipeline.py generate --port 8080
```

Use `--refresh` after a command to bypass its relevant caches. This can repeat paid API work:

```bash
python3 pipeline.py all --refresh --no-serve
```

On the first generation run, the setup stage clones the pinned `watermarks-remover` v0.6.0 dependency into `vendor/`. Later runs reuse it.

## What happens

The generated HTML presents the same process as ten collapsed, human-readable steps:

1. **Discover:** run four US English Google organic searches through DataForSEO and retain the raw response and ranked candidates.
2. **Scrape:** fetch and cache the five selected independent reviews, then remove page chrome and normalize article text.
3. **Extract:** send all five source bodies in **one combined Haiku call**. Structured output merges equivalent claims while retaining publisher provenance, evidence status, and disagreements.
4. **Normalize:** use Python to clean fields, validate provenance, deduplicate claims, assign stable claim IDs, and preserve conflicts.
5. **Generate:** make **one Sonnet call** to produce the Markdown candidate from the compact evidence only.
6. **Validate:** run deterministic Python checks for structure, length, metadata, approved links, methodology sources, first-hand language, pricing, punctuation, and FAQ requirements.
7. **Audit:** make **one Haiku call** for an exhaustive factual-claim matrix. Every distinct factual assertion must receive a supported or blocking verdict against the normalized evidence.
8. **Repair and gate:** if either validator fails, make **at most one Sonnet repair call**, then rerun Python validation and the Haiku audit. Publication requires both final gates to pass; there is no retry loop.
9. **Clean:** apply `watermarks-remover` Layer A deterministic Unicode cleanup to the validated Markdown.
10. **Render:** write the cleaned Markdown and a standalone HTML page.

Each HTML panel leads with an outcome label such as `PASS · 53/53 SUPPORTED` or `REPAIR SKIPPED · FINAL PASS`, followed by the relevant inputs, prompts, raw responses, parsed records, result, and next step. Artifact content is escaped and the panels are collapsed by default.

The Haiku claim audit materially reduces factual-grounding risk, but it is an automated review against a finite evidence set, not formal proof that every statement is true.

## Calls and caching

| Run | DataForSEO and source fetches | Claude calls |
|---|---|---|
| Fresh, validation passes | 4 live DataForSEO requests and 5 page downloads | 1 Haiku extraction + 1 Sonnet generation + 1 Haiku audit = **3** |
| Fresh, repair required | Same collection work | Passing-path calls + 1 Sonnet repair + 1 Haiku re-audit = **5** |
| Unchanged cached run | Reuses saved discovery and pages | Reuses extraction, generation, audits, and repair decision = **0 paid API calls** |

Cache keys include source-content, evidence, article, model, prompt-version, and validation-input hashes as appropriate. `--refresh` bypasses relevant caches and should be used deliberately.

## Inspect the result

The main delivery files are:

- `data/arzopa-z3fc/output/review.md`: cleaned final Markdown.
- `data/arzopa-z3fc/output/review.html`: standalone review plus the ten-step audit trace.
- `data/arzopa-z3fc/output/draft.md`: initial Sonnet candidate.
- `data/arzopa-z3fc/output/polished.md`: candidate that passed the final dual gate, repaired if necessary.

Supporting artifacts remain available for reproducibility:

```text
data/arzopa-z3fc/
├── discovery/
│   ├── dataforseo-raw.json
│   └── sources.json
├── reviews/
│   ├── scraped/*.json
│   ├── manifest.json
│   └── extractions.json
├── evidence/
│   └── normalized.json
└── output/
    ├── generation.json
    ├── validation.json
    ├── factual-audit.json
    ├── repair.json
    ├── watermark-cleanup.json
    ├── draft.md
    ├── polished.md
    ├── review.md
    └── review.html
```

## Tests and verified demo status

Run the local test suite with:

```bash
python3 -m unittest discover -s tests -v
```

The current repository state was verified with **28 passing tests**. The checked-in Arzopa demo records:

- **1,109 visible words** under the deterministic validator's counting rules.
- **Python validation: pass**, with zero final issues.
- **Haiku factual audit: 53/53 claims supported**, with zero blocking issues.
- **Repair skipped:** the initial candidate passed both gates, so the repair stage made zero Sonnet calls and reused the initial Haiku pass as the final audit.

The tests cover compact evidence/provenance handling, deterministic validation, factual-audit matrix rules, combined repair inputs, safe artifact escaping, Markdown rendering, and the ten-step result labels.

## Deploy the demo to Vercel

`review.html` is static, so the generated output directory can be deployed directly:

```bash
npx vercel data/arzopa-z3fc/output --prod
```

Complete Vercel's login/project prompts on the first run, then use the resulting URL with `/review.html`.

The HTML is an audit and debugging deliverable, not a sanitized production template. It embeds source material, prompts, raw model responses, and parsed artifacts in collapsed markup. Review its contents and redistribution rights before publishing it outside a controlled demo.

## Security and limitations

- `.env` and the vendored cleaner are ignored by Git. Never commit API keys, DataForSEO credentials, or other secrets. If a credential has appeared in source, logs, screenshots, or Git history, rotate it immediately; deleting it later is not sufficient.
- Product identity, queries, sources, prompts, and output paths are currently hardcoded for the Arzopa Z3FC. Supporting another product requires configuration and prompt changes.
- Scraping depends on third-party page structure and availability. Cached inputs improve repeatability but can become stale.
- The local audit HTML deliberately embeds source and prompt content. It is suitable for evaluation and debugging, not automatically safe for public production use.
- Layer A cleanup removes invisible Unicode characters and normalizes exotic spaces. It does not paraphrase text, detect statistical signals, or claim to remove every possible model watermark.
- Automated factual review reduces risk; it does not replace human editorial, legal, compliance, or hands-on product review.
