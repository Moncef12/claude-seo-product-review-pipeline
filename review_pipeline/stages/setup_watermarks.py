import subprocess
from pathlib import Path

from review_pipeline.config import PROJECT_ROOT


REPOSITORY = "https://github.com/guillaumemeyer/watermarks-remover.git"
VERSION = "v0.6.0"
TARGET = PROJECT_ROOT / "vendor" / "watermarks-remover"
CLEANER = TARGET / "service" / "scripts" / "clean_text.py"


def main() -> None:
    if CLEANER.exists():
        print(f"Watermark cleaner already available at {TARGET}")
        return
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            VERSION,
            REPOSITORY,
            str(TARGET),
        ],
        check=True,
    )
    print(f"Installed watermarks-remover {VERSION} at {TARGET}")


if __name__ == "__main__":
    main()
