import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone

from review_pipeline.config import OUTPUT_DIR, PROJECT_ROOT, ensure_data_directories


TOOL_VERSION = "v0.6.0"
CLEANER_PATH = (
    PROJECT_ROOT
    / "vendor"
    / "watermarks-remover"
    / "service"
    / "scripts"
    / "clean_text.py"
)
POLISHED_PATH = OUTPUT_DIR / "polished.md"
REVIEW_PATH = OUTPUT_DIR / "review.md"
CLEANUP_REPORT_PATH = OUTPUT_DIR / "watermark-cleanup.json"


def file_hash(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_cleaner() -> dict:
    command = [
        sys.executable,
        str(CLEANER_PATH),
        str(POLISHED_PATH),
        "--output",
        str(REVIEW_PATH),
        "--stats",
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stderr)


def save_report(stats: dict, before_hash: str, after_hash: str) -> None:
    report = {
        "tool": "guillaumemeyer/watermarks-remover",
        "tool_version": TOOL_VERSION,
        "mode": "deterministic Layer A text cleanup",
        "cleaned_at": datetime.now(timezone.utc).isoformat(),
        "input_sha256": before_hash,
        "output_sha256": after_hash,
        "changed": before_hash != after_hash,
        "stats": stats,
    }
    CLEANUP_REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")


def main() -> None:
    ensure_data_directories()
    if not CLEANER_PATH.exists():
        raise SystemExit(f"Missing watermark cleaner: {CLEANER_PATH}")
    before_hash = file_hash(POLISHED_PATH)
    stats = run_cleaner()
    after_hash = file_hash(REVIEW_PATH)
    save_report(stats, before_hash, after_hash)
    print(
        f"Cleaned final review: removed={stats['removed_count']} "
        f"replaced={stats['replaced_count']}"
    )
    print(f"Saved cleaned review to {REVIEW_PATH}")


if __name__ == "__main__":
    main()
