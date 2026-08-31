# Claude SEO Product Review Pipeline

`claude-seo-product-review-pipeline` is a working reference implementation for producing an evidence-grounded SEO product review from live search results and independent source material. It combines DataForSEO discovery, cached web extraction, Claude Haiku and Sonnet, deterministic Python validation, and an inspectable HTML audit trail.

The current demo reviews the **Arzopa Z3FC portable monitor** using five dynamically discovered independent publications. It is intentionally a focused, end-to-end example rather than a generic multi-product service.

- Live demo: [Arzopa Z3FC review and pipeline audit](https://arzopa-z3fc-review-workflow.vercel.app/review.html)
- Release notes: [CHANGELOG.md](CHANGELOG.md)
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

The generated HTML presents the same process as twelve collapsed, human-readable steps:

1. **Discover:** run the exact-product Google organic query through DataForSEO, conditionally add a pros/cons query, and retain raw responses, SERP intent signals, and ranked candidates.
2. **Qualify and scrape:** candidates must pass hard eligibility checks first. Among eligible candidates, choose exactly five unique-domain sources by score, then cache them. The dashboard shows every candidate's score, selection boundary, decision, and expandable score breakdown; there is no preselected publisher list or fixed numeric eligibility cutoff.
3. **Extract:** send exactly those five source bodies in **one combined Haiku call**. Structured output merges equivalent claims while retaining publisher provenance, evidence status, and disagreements.
4. **Normalize:** use Python to clean fields, validate provenance, deduplicate claims, assign stable claim IDs, and preserve conflicts.
5. **Plan:** make one cached Haiku SEO/AIO/CRO planning call from live SERP intent, selected-page headings, qualification signals, and normalized evidence.
6. **Generate:** make **one Sonnet call** with the complete plan and compact evidence; evidence overrides planning text.
7. **Validate:** run deterministic Python checks for structure, length, metadata, approved links, methodology sources, first-hand language, pricing, punctuation, FAQ requirements, and decision support.
8. **Audit:** make **one Haiku call** that separately checks the exhaustive factual-claim matrix, coverage of 13 essential SEO/AIO/CRO plan requirements, every dynamic buyer question (four in this review), and six editorial/commercial buyer-decision standards.
9. **Repair and gate:** if Python, factual grounding, plan coverage, buyer-question coverage, or editorial/commercial decision quality fails, make **at most one Sonnet repair call**, then rerun Python and the combined Haiku audit. Publication requires all five final results to pass; there is no retry loop.
10. **Summarize:** write deterministic call, token, recorded-cost, estimated-cost, validation, repair, and word-count accounting.
11. **Clean:** apply `watermarks-remover` Layer A deterministic Unicode cleanup to the validated Markdown.
12. **Render:** write the cleaned Markdown and standalone HTML with a visible production summary and collapsed trace.

`review.html` is a lightweight standalone HTML dashboard. Sticky `Overview`, `Final review`, and `Pipeline trace` navigation keeps the key results easy to find. It includes a compact API/token/cost/repair summary plus separate Python, Haiku factual, Haiku plan-coverage, and Haiku editorial/commercial decision results. Only one pipeline step is open at a time; raw artifacts remain available inside the trace.

Each trace panel leads with an outcome label such as `PASS · 56/56 SUPPORTED` or `REPAIR RAN · FINAL PASS`, followed by the relevant inputs, prompts, raw responses, parsed records, result, and next step. Artifact content is escaped.

The combined Haiku audit checks factual grounding, whether Sonnet executed the essential content plan, whether every planned buyer question is answered, and whether the article gives useful evidence-constrained buyer guidance. It remains an automated review against a finite evidence set, not formal proof that every statement is true.

## Calls and caching

| Run | DataForSEO and source fetches | Claude calls |
|---|---|---|
| Fresh, validation passes | 1 primary SERP request, a conditional fallback, at most 1 bulk request for missing/stale authority domains, and up to 15 page downloads | 1 Haiku extraction + 1 Haiku plan + 1 Sonnet generation + 1 Haiku audit = **4** |
| Fresh, repair required | Same collection work | Passing-path calls + 1 Sonnet repair + 1 Haiku re-audit = **6** |
| Unchanged cached run | Reuses saved discovery, authority, and pages | Reuses extraction, plan, generation, audits, and repair decision = **0 paid API calls** |

Cache keys include normalized source targets, source content, evidence, SERP/qualification inputs, article, plan, model, prompt-version, and validation-input hashes as appropriate. Authority scores also use the shared human-readable `data/authority-db.json`, keyed by normalized root domain with a 90-day TTL. Each discovery run reuses fresh entries and makes at most one Bulk Ranks request for missing or stale domains; `--refresh` bypasses SERP/page/model caches but does not invalidate fresh authority entries. Summary generation is deterministic.

The verified rerun used **1 DataForSEO discovery call and 0 authority calls** because 16 domains were fresh in the shared 90-day authority cache. Missing or stale domains trigger at most one bulk authority lookup. The production summary's **Total API calls** counts stored provider calls that produced the article, including reused Claude producer artifacts; **Page fetches** is reported separately.

## Inspect the result

The main delivery files are:

- `data/arzopa-z3fc/output/review.md`: cleaned final Markdown.
- `data/arzopa-z3fc/output/review.html`: standalone review plus the twelve-step audit trace and visible production summary.
- `data/arzopa-z3fc/output/draft.md`: initial Sonnet candidate.
- `data/arzopa-z3fc/output/polished.md`: candidate that passed the final Python, factual, plan-coverage, and editorial/commercial gates, repaired if necessary.

Supporting artifacts remain available for reproducibility:

```text
data/
├── authority-db.json
└── arzopa-z3fc/
    ├── discovery/
    │   ├── dataforseo-raw.json
    │   ├── authority-raw.json
    │   └── sources.json
    ├── reviews/
    │   ├── scraped/*.json
    │   ├── manifest.json
    │   ├── qualification.json
    │   └── extractions.json
    ├── evidence/
    │   └── normalized.json
    └── output/
        ├── plan.json
        ├── generation.json
        ├── validation.json
        ├── factual-audit.json
        ├── repair.json
        ├── production-summary.json
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

The test suite covers dynamic source classification, authority and author scoring, unique-domain selection, conditional discovery fallback, manifest-driven extraction, plan evidence-ID/cache rules, plan-aware generation and repair caches, editorial/commercial validation, factual, plan, buyer-question, and decision-audit rules, production accounting, safe summary rendering, the twelve-step trace, and dashboard navigation. **90 tests pass** in the current local version.

The checked-in Arzopa demo records:

- **1,145 visible words** under the deterministic validator's counting rules.
- **Python validation: pass**, with zero final issues.
- **Haiku factual audit: 59/59 factual claims supported**, with zero blocking issues.
- **Haiku plan coverage: 13/13 essential SEO/AIO/CRO requirements covered**, with zero blocking issues.
- **Haiku buyer-question coverage: 4/4 planned buyer questions answered**, with zero blocking issues.
- **Haiku editorial/commercial quality: 6/6 buyer-decision standards met**, covering the recommendation, buyer segmentation, trade-off priority, objection handling, honest value framing, and fit-based next step.
- **Repair skipped:** the initial Sonnet candidate passed Python, factual, plan-coverage, buyer-question, and editorial/commercial validation, so no repair or second Haiku call was required.
- **Production summary:** 5 provider API calls, 74,148 Claude tokens, 11 page fetches, and an estimated total of **$0.191964** for the recorded article build. The shared authority database supplied fresh domain scores with zero additional authority calls on the verified rerun.

The tests also cover compact evidence/provenance handling, deterministic validation, factual, buyer-question, and decision-audit matrix rules, combined repair inputs, safe artifact escaping, and Markdown rendering.
