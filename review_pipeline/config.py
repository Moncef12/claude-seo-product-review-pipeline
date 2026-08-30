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
    'site:arzopa.com "Z3FC"',
    '"Arzopa Z3FC" specifications manual',
    '"Arzopa Z3FC" review test',
    '"Arzopa Z3FC" problems limitations',
)

REVIEW_SOURCES = (
    {
        "publisher": "ServeTheHome",
        "url": "https://www.servethehome.com/arzopa-z3fc-16-1in-180hz-2-5k-portable-monitor/",
    },
    {
        "publisher": "TechRadar",
        "url": "https://www.techradar.com/pro/arzopa-z3fc-portable-monitor-review",
    },
    {
        "publisher": "Tom's Hardware",
        "url": "https://www.tomshardware.com/monitors/portable-monitors/arzopa-z3fc-16-1-inch-portable-monitor-review",
    },
    {
        "publisher": "How-To Geek",
        "url": "https://www.howtogeek.com/arzopa-z3fc-161-inch-portable-monitor-review/",
    },
    {
        "publisher": "PCWorld",
        "url": "https://www.pcworld.com/article/2915268/arzopa-z3fc-review-a-portable-monitor-with-a-180hz-refresh-rate.html",
    },
)

PRODUCT_DATA_DIR = PROJECT_ROOT / "data" / PRODUCT_SLUG
DISCOVERY_DIR = PRODUCT_DATA_DIR / "discovery"
REVIEWS_DIR = PRODUCT_DATA_DIR / "reviews"
SCRAPED_REVIEWS_DIR = REVIEWS_DIR / "scraped"
EXTRACTED_REVIEWS_DIR = REVIEWS_DIR / "extracted"
EVIDENCE_DIR = PRODUCT_DATA_DIR / "evidence"
OUTPUT_DIR = PRODUCT_DATA_DIR / "output"

SERP_RAW_PATH = DISCOVERY_DIR / "dataforseo-raw.json"
SOURCES_PATH = DISCOVERY_DIR / "sources.json"
REVIEW_MANIFEST_PATH = REVIEWS_DIR / "manifest.json"
EXTRACTIONS_PATH = REVIEWS_DIR / "extractions.json"
NORMALIZED_EVIDENCE_PATH = EVIDENCE_DIR / "normalized.json"
VALIDATION_PATH = OUTPUT_DIR / "validation.json"
FACTUAL_AUDIT_PATH = OUTPUT_DIR / "factual-audit.json"
REPAIR_PATH = OUTPUT_DIR / "repair.json"

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
HAIKU_MODEL = "claude-haiku-4-5-20251001"
EXTRACTION_PROMPT_VERSION = "z3fc-compact-evidence-v3-structured"


def ensure_data_directories() -> None:
    for directory in (
        DISCOVERY_DIR,
        SCRAPED_REVIEWS_DIR,
        EXTRACTED_REVIEWS_DIR,
        EVIDENCE_DIR,
        OUTPUT_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)
