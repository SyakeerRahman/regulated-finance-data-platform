"""Build the Lambda zip.

    uv run python scripts/build_lambda.py

The package carries no third-party code. `lambda_fn/scorer.py` walks the exported trees and
`finplat/features.py` builds the features, and neither imports numpy or pandas. Run
`finplat.export` first, so `lambda_fn/model.json` holds the live model.
"""

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
ARCHIVE = BUILD / "scorer.zip"

# Every file the handler imports, and nothing else. A wildcard here is how a zip grows a copy of
# the test suite and the whole lake.
CONTENTS = [
    "finplat/__init__.py",
    "finplat/domain.py",
    "finplat/features.py",
    "lambda_fn/__init__.py",
    "lambda_fn/scorer.py",
    "lambda_fn/handler.py",
    "lambda_fn/model.json",
]


def build() -> Path:
    missing = [name for name in CONTENTS if not (ROOT / name).exists()]
    if missing:
        raise SystemExit(f"missing {', '.join(missing)}. Run: uv run python -m finplat.export lambda_fn/model.json")

    BUILD.mkdir(exist_ok=True)
    with zipfile.ZipFile(ARCHIVE, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in CONTENTS:
            archive.write(ROOT / name, name)
    return ARCHIVE


if __name__ == "__main__":
    archive = build()
    print(f"{archive.relative_to(ROOT)}  {archive.stat().st_size / 1024:.0f} KB")
    print("Lambda allows 50 MB zipped on a direct upload and 250 MB unzipped.")
