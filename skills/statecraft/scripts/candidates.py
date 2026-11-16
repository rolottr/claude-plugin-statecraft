#!/usr/bin/env python3
"""Rank files that probably hold implicit state machines.

A language-agnostic heuristic for the statecraft sweep. It scores each source file by the distinct
lifecycle-like identifiers it declares or uses (busy flags, nullable state timestamps, error holders,
handles to running work, retry counters) and by comparisons or assignments of a status/state field
against literals. It also lists status values compared in several files, a sign that transitions
are scattered.

The output is a starting point for auditors, not a list of findings. Tests, vendored, generated and
minified files are skipped.

Usage: python3 -I candidates.py [path] [--top N] [--min-files N]
Standard library only.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

SOURCE_SUFFIXES = {
    ".go", ".rs", ".py", ".rb", ".php", ".java", ".kt", ".kts", ".scala", ".cs", ".fs", ".swift",
    ".m", ".mm", ".c", ".cc", ".cpp", ".h", ".hpp", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".vue", ".svelte", ".ex", ".exs", ".erl", ".clj", ".dart", ".lua", ".sql", ".prisma", ".graphql",
    ".proto",
}
SKIP_DIRS = {
    ".git", "node_modules", "vendor", "third_party", "build", "dist", "out", "target", ".build",
    "Pods", "DerivedData", ".venv", "venv", "__pycache__", ".next", ".nuxt", "coverage", ".gradle",
    ".idea", ".terraform", "test", "tests", "Tests", "__tests__", "spec", "specs", "testdata",
    "fixtures", "__mocks__", "e2e",
}
SKIP_NAME = re.compile(
    r"\.min\.|\.pb\.|_pb2|\.generated\.|\.g\.dart$|\.d\.ts$"
    r"|[._-](?:test|tests|spec)\.\w+$|^test_\w*\.py$|Tests?\.(?:swift|kt|java|cs)$"
)
COMMENT = re.compile(r"^\s*(?://|#|\*|/\*|--|;)")
MAX_BYTES = 1_000_000
MAX_LINE = 2000  # a longer line means minified or generated code

WORDS = (
    "loading|loaded|running|pending|processing|processed|failed|failing|done|complete|completed|finished|"
    "busy|active|cancel+ed|cancel+ing|retrying|syncing|synced|sending|sent|ready|started|starting|stopped|"
    "stopping|connected|connecting|reconnecting|locked|paid|shipped|refreshing|fetching|submitting|"
    "submitted|waiting|in_?progress|in_?flight|dirty|stale|expired|verified|confirmed|approved|rejected|"
    "published|scheduled|queued|claimed|delivered|settled|refunded|suspended|blocked|initiali[sz]ed"
)
LIFECYCLE_PATTERNS = (
    # isLoading, is_running, hasFailed, didFinish, needsRefresh, should_retry
    re.compile(
        rf"\b(?:is|has|did|was|should|needs)_?(?:{WORDS}|refresh|retry|reload|sync)\b", re.IGNORECASE
    ),
    # processed_at, failedAt, cancelled_on, lockedUntil
    re.compile(rf"\b(?:{WORDS})(?:_at|_on|At|On|_until|Until)\b"),
    # busy marks, counters and error holders held as members or columns
    re.compile(
        r"\b(?:busy|pending|in_?flight|loading|running|dirty|retries|attempts|failure_?count|"
        r"retry_?count|last_?error|error_?message|pending_?refresh|locked_?by|lease_?expires)\b",
        re.IGNORECASE,
    ),
    # a handle to running work held beside the flags
    re.compile(
        r"\b(?:current|running|active|pending|in_?flight|refresh|load|fetch|sync|poll|retry)_?"
        r"(?:task|job|promise|future|timer|request|operation|work|cancellable|cancellation|handle)\b",
        re.IGNORECASE,
    ),
)
# order.status == "paid", job_state = 'running', deploymentStatus !== "x", status IN ('a', ...)
STATUS_CMP = re.compile(
    r"\b(\w*(?:status|state|phase|stage))\s*(?:===?|!==?|<>|=|\.equals\(|\bIN\s*\()\s*"
    r"['\"`]([A-Za-z][\w-]{1,40})['\"`]",
    re.IGNORECASE,
)


@dataclass
class FileScore:
    path: str
    names: dict[str, tuple[str, int]] = field(default_factory=dict)  # key -> (spelling, first line)
    literals: dict[str, int] = field(default_factory=dict)  # "field=value" -> first line

    @property
    def score(self) -> int:
        return len(self.names) * 2 + min(len(self.literals), 6)


def list_files(root: Path) -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True, check=True, timeout=120,
        ).stdout
        files = [root / p for p in out.decode("utf-8", "replace").split("\0") if p]
    except (OSError, subprocess.SubprocessError):
        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            files.extend(Path(dirpath) / f for f in filenames)
    return [
        f for f in files
        if f.suffix in SOURCE_SUFFIXES
        and not SKIP_NAME.search(f.name)
        and not any(part in SKIP_DIRS for part in f.relative_to(root).parts[:-1])
    ]


def field_key(name: str) -> str:
    """`order.status`, `jobState` and `job_state` compare as `status` / `state`."""
    lowered = name.lower()
    for suffix in ("status", "state", "phase", "stage"):
        if lowered.endswith(suffix):
            return suffix
    return lowered


def scan(path: Path, root: Path) -> FileScore | None:
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        lines = path.read_text("utf-8", "replace").splitlines()
    except OSError:
        return None
    if any(len(line) > MAX_LINE for line in lines):
        return None
    result = FileScore(str(path.relative_to(root)))
    for number, line in enumerate(lines, 1):
        if COMMENT.match(line):
            continue
        for pattern in LIFECYCLE_PATTERNS:
            for match in pattern.finditer(line):
                name = match.group(0)
                result.names.setdefault(name.lower().replace("_", ""), (name, number))
        for match in STATUS_CMP.finditer(line):
            result.literals.setdefault(f"{field_key(match.group(1))}={match.group(2).lower()}", number)
    return result if result.names or result.literals else None


def print_ranking(scores: list[FileScore], top: int) -> None:
    print(f"# {len(scores)} files with lifecycle signals; showing top {min(top, len(scores))}")
    print("# score = 2 x distinct lifecycle identifiers + status literals (at most 6)\n")
    for s in scores[:top]:
        names = ", ".join(f"{n}:{line}" for n, line in sorted(s.names.values(), key=lambda v: v[1])[:12])
        lits = ", ".join(f"{k}:{line}" for k, line in sorted(s.literals.items(), key=lambda kv: kv[1])[:8])
        print(f"{s.score:4d}  {s.path}")
        if names:
            print(f"      identifiers: {names}")
        if lits:
            print(f"      status literals: {lits}")


def print_scattered(scores: list[FileScore], min_files: int) -> None:
    spread: dict[str, list[str]] = defaultdict(list)
    for s in scores:
        for literal in s.literals:
            spread[literal].append(s.path)
    scattered = sorted(
        ((lit, paths) for lit, paths in spread.items() if len(paths) >= min_files),
        key=lambda item: (-len(item[1]), item[0]),
    )
    print(f"\n# status values compared or assigned in {min_files}+ files (scattered transitions)")
    for literal, paths in scattered[:25]:
        shown = ", ".join(paths[:5]) + (f", +{len(paths) - 5} more" if len(paths) > 5 else "")
        print(f"{len(paths):4d}  {literal}: {shown}")
    if not scattered:
        print("      none")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", nargs="?", default=".")
    parser.add_argument("--top", type=int, default=40, help="files to list (default 40)")
    parser.add_argument("--min-files", type=int, default=3, help="files a status value must appear in")
    args = parser.parse_args()

    root = Path(args.path).resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    scores = [s for f in list_files(root) if (s := scan(f, root))]
    scores.sort(key=lambda s: (-s.score, s.path))
    print(f"# statecraft candidates in {root}")
    print_ranking(scores, args.top)
    print_scattered(scores, args.min_files)
    return 0


if __name__ == "__main__":
    sys.exit(main())
