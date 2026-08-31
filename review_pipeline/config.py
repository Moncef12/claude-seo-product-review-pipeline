from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PRODUCT_SLUG = "arzopa-z3fc"

PRODUCT = {
    "brand": "Arzopa",
    "model": "Z3FC",
    "colour": "Grey",
    "manufacturer": "Shenzhen G-world Technology Incorporated Company",
}

SEARCH_QUERIES = (
    '"Arzopa Z3FC" review',
)

# Discovery is deliberately narrow.  The second query is only used when the
# primary SERP does not provide enough independent candidates or useful intent
# signals.  Keeping the query strings in configuration makes cache identity and
# the HTML trace inspectable without coupling scraping to a publisher list.
PRIMARY_SERP_QUERY = SEARCH_QUERIES[0]
FALLBACK_SERP_QUERY = '"Arzopa Z3FC" pros cons'
DISCOVERY_DEPTH = 20
MIN_INDEPENDENT_CANDIDATES = 8
PRELIMINARY_CANDIDATE_LIMIT = 15
SELECTED_REVIEW_COUNT = 5

# These names are kept as small, descriptive aliases for integrations that used
# the earlier SEARCH_QUERIES terminology.  Runtime code never contains a fixed
# review URL list.
SERP_PRIMARY_QUERY = PRIMARY_SERP_QUERY
SERP_FALLBACK_QUERY = FALLBACK_SERP_QUERY
SEARCH_QUERY_PRIMARY = PRIMARY_SERP_QUERY
SEARCH_QUERY_FALLBACK = FALLBACK_SERP_QUERY

PRODUCT_DATA_DIR = PROJECT_ROOT / "data" / PRODUCT_SLUG
SHARED_DATA_DIR = PROJECT_ROOT / "data"
DISCOVERY_DIR = PRODUCT_DATA_DIR / "discovery"
REVIEWS_DIR = PRODUCT_DATA_DIR / "reviews"
SCRAPED_REVIEWS_DIR = REVIEWS_DIR / "scraped"
EXTRACTED_REVIEWS_DIR = REVIEWS_DIR / "extracted"
EVIDENCE_DIR = PRODUCT_DATA_DIR / "evidence"
OUTPUT_DIR = PRODUCT_DATA_DIR / "output"

SERP_RAW_PATH = DISCOVERY_DIR / "dataforseo-raw.json"
AUTHORITY_RAW_PATH = DISCOVERY_DIR / "authority-raw.json"
AUTHORITY_PATH = AUTHORITY_RAW_PATH
AUTHORITY_RAW_ARTIFACT_PATH = AUTHORITY_RAW_PATH
# Authority scores are shared by all product runs.  The database is deliberately
# separate from each product's discovery directory and contains one bounded entry
# per normalized root domain.
AUTHORITY_DB_PATH = SHARED_DATA_DIR / "authority-db.json"
AUTHORITY_DATABASE_PATH = AUTHORITY_DB_PATH
AUTHORITY_CACHE_PATH = AUTHORITY_DB_PATH
AUTHORITY_CACHE_TTL_DAYS = 90
AUTHORITY_DB_TTL_DAYS = AUTHORITY_CACHE_TTL_DAYS
AUTHORITY_TTL_DAYS = AUTHORITY_CACHE_TTL_DAYS
SOURCES_PATH = DISCOVERY_DIR / "sources.json"
REVIEW_MANIFEST_PATH = REVIEWS_DIR / "manifest.json"
QUALIFICATION_PATH = REVIEWS_DIR / "qualification.json"
EXTRACTIONS_PATH = REVIEWS_DIR / "extractions.json"
NORMALIZED_EVIDENCE_PATH = EVIDENCE_DIR / "normalized.json"
PLAN_PATH = OUTPUT_DIR / "plan.json"
GENERATION_PATH = OUTPUT_DIR / "generation.json"
VALIDATION_PATH = OUTPUT_DIR / "validation.json"
FACTUAL_AUDIT_PATH = OUTPUT_DIR / "factual-audit.json"
REPAIR_PATH = OUTPUT_DIR / "repair.json"
PRODUCTION_SUMMARY_PATH = OUTPUT_DIR / "production-summary.json"
PRODUCTION_SUMMARY_ARTIFACT = PRODUCTION_SUMMARY_PATH

ARTICLE_MIN_WORDS = 800
ARTICLE_MAX_WORDS = 1200
REQUIRED_HEADINGS = (
    "Quick Verdict",
    "Review Snapshot",
    "Pros and Cons",
    "Specifications",
    "Design and Portability",
    "Display Quality",
    "Gaming Performance",
    "Connectivity and Everyday Use",
    "How It Compares",
    "Who Should Buy It and Who Should Not",
    "Final Verdict",
    "Frequently Asked Questions",
    "How We Researched This Review",
)

DATAFORSEO_ENDPOINT = (
    "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"
)
DATAFORSEO_BULK_RANKS_ENDPOINT = (
    "https://api.dataforseo.com/v3/backlinks/bulk_ranks/live"
)
DATAFORSEO_AUTHORITY_ENDPOINT = DATAFORSEO_BULK_RANKS_ENDPOINT
HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-6"
EXTRACTION_PROMPT_VERSION = "z3fc-compact-evidence-v3-structured"
PLAN_PROMPT_VERSION = "z3fc-seo-aio-cro-plan-v6-layout-aware"
GENERATION_PROMPT_VERSION = "z3fc-final-review-v9-section-budgets"

# Standard Anthropic API rates used only for transparent estimates in the
# production summary.  They are intentionally dated and represented per
# million tokens so a reader can reproduce the arithmetic.
PRICING_EFFECTIVE_DATE = "2026-08-31"
CLAUDE_PRICING_USD_PER_MILLION = {
    HAIKU_MODEL: {"input": 1.0, "output": 5.0},
    SONNET_MODEL: {"input": 3.0, "output": 15.0},
}


def ensure_data_directories() -> None:
    for directory in (
        DISCOVERY_DIR,
        SCRAPED_REVIEWS_DIR,
        EXTRACTED_REVIEWS_DIR,
        EVIDENCE_DIR,
        OUTPUT_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)
