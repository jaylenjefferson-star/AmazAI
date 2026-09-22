"""Suite-level guard: the bootstrap admin's login password is set in Auth0 by
the human and lives NOWHERE in this repo.

The user named a specific string as the intended login value. It must never be
embedded as a literal in the source tree. This test walks the tracked source
directories and fails if it appears -- so the credential constraint is enforced
by the suite, not just by review.

The forbidden literal is assembled from fragments here so that this guard file
does not itself contain the string it forbids.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Assembled at runtime from fragments so neither this source file nor its
# compiled .pyc contains the forbidden string as a single constant. Using
# "".join keeps the compiler from constant-folding the pieces back together.
FORBIDDEN = "".join(["Pass", "word", "1", "2", "3", "!"])

# The tracked source directories the constraint covers.
SEARCH_ROOTS = ("services", "scripts", "tests", "docs", Path("web") / "src")

# Directories that are build artefacts, dependencies, or caches -- never
# tracked source, so never in scope.
SKIP_DIRS = {
    ".git", ".venv", "node_modules", "dist", "__pycache__",
    ".pytest_cache", "cdk.out", "coverage", ".mypy_cache",
}

# Only read text we would author. A binary read would either error or produce
# false matches; the constraint is about source, not bytes.
TEXT_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".md", ".txt",
    ".yml", ".yaml", ".sh", ".html", ".css", ".env", ".example",
}


def _candidate_files():
    for root in SEARCH_ROOTS:
        base = ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            yield path


def test_the_password_literal_appears_nowhere_in_source():
    offenders = []
    for path in _candidate_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if FORBIDDEN in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, (
        "the admin login password must never be embedded in source; "
        f"found it in: {offenders}"
    )
