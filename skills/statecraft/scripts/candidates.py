#!/usr/bin/env python3
"""Rank source files that probably hold implicit state machines.

A language-agnostic heuristic for the statecraft sweep. For each source file it collects:

- lifecycle-like identifiers: busy flags (isLoading, hasFailed), timestamps acting as states
  (processed_at, StartedAt), boolean fields named after a lifecycle (Paid bool, shipped: boolean),
  status fields typed as plain ints or strings, error holders, retry counters, handles to running work;
- status values: a status/state/phase/stage field compared with or assigned a literal, and
  switch/case arms on lifecycle words.

A file's score is its densest 40-line window: 2 x distinct identifiers + distinct status values
(at most 6). It also lists status values that appear in several files, a sign that transitions
are scattered. Declarations (enum members, constants, union types) are not counted: they are what
explicit machines look like.

The output points auditors at places to look. It ranks by names, so it misses clusters with
unusual names and counts some framework-owned state; it is not a list of findings.

Usage:
  python3 -I candidates.py ROOT [--paths P ...] [--top N] [--min-files N] [--window N] [--jobs N]

ROOT is the repository root (or any directory). --paths limits the scan to paths under ROOT;
output paths are always relative to ROOT. Python 3.8+, standard library only.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

C_LIKE = {
    ".go", ".rs", ".java", ".kt", ".kts", ".scala", ".sc", ".cs", ".fs", ".fsx", ".swift", ".m", ".mm",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte",
    ".dart", ".groovy", ".zig", ".php", ".prisma", ".proto",
}
HASH = {".py", ".rb", ".ex", ".exs", ".cr", ".jl", ".nim", ".pl", ".pm", ".graphql", ".r"}
DASH = {".sql", ".lua", ".hs", ".elm"}
OTHER = {".erl": "%", ".clj": ";", ".ml": None, ".mli": None}
SOURCE_SUFFIXES = C_LIKE | HASH | DASH | set(OTHER)
MARKUP = {".tsx", ".jsx", ".vue", ".svelte"}
NO_VALUES = {".proto", ".graphql", ".prisma"}  # "= 2" is a field number there, not a status

# Skipped wherever they appear.
ALWAYS_SKIP = {
    ".git", "node_modules", "vendor", "third_party", "bower_components", ".build", "Pods", "DerivedData",
    ".venv", "venv", "site-packages", "__pycache__", ".next", ".nuxt", ".svelte-kit", ".gradle", ".idea",
    ".terraform", ".tox", ".mypy_cache", ".pytest_cache",
}
TEST_DIRS = {"test", "tests", "__tests__", "testdata", "testing", "testutil", "testutils", "e2e", "mocks", "__mocks__", "__snapshots__"}
# Skipped only as the first path component under ROOT (elsewhere they are often real code).
ROOT_ONLY_SKIP = {"build", "dist", "out", "target", "coverage", "spec", "fixtures"}
TEST_NAME = re.compile(r"[._-](?:test|tests|spec)\.\w+$|^test_\w*\.py$|_test\.go$|Tests?\.(?:swift|kt|java|cs)$")
GENERATED_NAME = re.compile(
    r"\.min\.|\.pb\.|_pb2|\.generated\.|^generated\.|^zz_generated|_generated\.|^generated_|\.g\.dart$|\.d\.ts$|\.designer\.cs$"
)
GENERATED_HEADER = re.compile(
    r"Code generated .* DO NOT EDIT|@generated|auto-?generated|AUTO-?GENERATED|This file (?:was|is) "
    r"(?:automatically )?generated",
    re.IGNORECASE,
)
MAX_BYTES = 1_000_000
MAX_LINE = 2000

WORDS = (
    "loading|loaded|running|pending|processing|processed|failed|failing|done|complete|completed|finished|"
    "busy|active|cancel+ed|cancel+ing|retrying|syncing|synced|sending|sent|ready|started|starting|stopped|"
    "stopping|connected|connecting|reconnecting|locked|paid|shipped|refreshing|fetching|submitting|"
    "submitted|waiting|in_?progress|in_?flight|dirty|stale|expired|verified|confirmed|approved|rejected|"
    "published|scheduled|queued|claimed|delivered|settled|refunded|suspended|blocked|initiali[sz]ed|"
    "archived|deleted|closed|opened|timed_?out|aborted|succeeded|resolved|acknowledged|dispatched"
)
WORD_END = re.compile(rf"(?:{WORDS})$", re.IGNORECASE)
STATE_WORDS = set(
    "queued pending active inactive idle open closed new draft archived error success succeeded failed failure "
    "done complete completed cancelled canceled running processing ready expired paid unpaid shipped delivered "
    "refunded rejected approved blocked suspended scheduled started stopped finished timeout timed_out retrying "
    "waiting in_progress inprogress loading loaded connected disconnected connecting reconnecting locked "
    "unlocked verified unverified confirmed published unpublished claimed settled aborted resolved".split()
)
IGNORE_VALUES = {"string", "number", "boolean", "object", "undefined", "function", "bigint", "symbol", "fulfilled"}

FIELD = r"(\w*(?:status|state|phase|stage))"
IDENTIFIER_PATTERNS = (
    # isLoading, is_running, hasFailed, didFinish, needsRefresh, should_retry
    re.compile(rf"\b(?:is|has|did|was|should|needs)_?(?:{WORDS}|refresh|retry|reload|sync)\b", re.IGNORECASE),
    # processed_at, failedAt, StartedAt, cancelled_on, lockedUntil
    re.compile(rf"\b(?:{WORDS})_?(?:at|on|until)\b", re.IGNORECASE),
    # busy marks, counters and error holders held as members or columns
    re.compile(
        r"\b(?:busy|pending|in_?flight|loading|running|dirty|retries|attempts|failure_?count|retry_?count|"
        r"last_?error|error_?message|pending_?refresh|locked_?by|lease_?(?:expires|until))\b",
        re.IGNORECASE,
    ),
    # a handle to running work held beside the flags
    re.compile(
        r"\b(?:current|running|active|pending|in_?flight|refresh|load|fetch|sync|poll|retry)_?"
        r"(?:task|job|promise|future|timer|request|operation|work|cancellable|cancellation|handle)\b",
        re.IGNORECASE,
    ),
)
BARE_WORDS = 2  # index of the bare-word pattern above
# Boolean declarations; counted when the name ends with a lifecycle word (Paid bool, shipped: boolean).
BOOL_DECLS = (
    re.compile(r"\b([A-Za-z_]\w*)\s*:\s*(?:bool|boolean|Bool|Boolean)\b"),
    re.compile(r"\b([A-Za-z_]\w*)\s+(?:bool|boolean|BOOLEAN|BOOL|TINYINT\(1\))\b"),
    re.compile(r"\b(?:bool|boolean|Boolean|BOOL)\s+([A-Za-z_]\w*)\s*[;=,)]"),
)
# Status fields typed as plain ints or strings: an unconstrained status.
PRIMITIVE = r"(?:u?int\d*|Int\d*|i\d+|u\d+|integer|INTEGER|INT|SMALLINT|TINYINT|number|string|String|str|TEXT|VARCHAR(?:\(\d+\))?|CHAR(?:\(\d+\))?)"
STATUS_DECLS = (
    re.compile(rf"\b{FIELD}\s+{PRIMITIVE}\b", re.IGNORECASE),
    re.compile(rf"\b{FIELD}\s*:\s*{PRIMITIVE}\b", re.IGNORECASE),
    re.compile(rf"\b(?:int|Integer|short|String|string)\s+{FIELD}\s*[;=]", re.IGNORECASE),
)
VALUE = r"(?:['\"`]([A-Za-z][\w-]{0,40})['\"`]|:([a-z]\w{0,40})\b|(\d{1,2})(?![\w.]))"  # 0-99: numbered states; 200 or 404 is an HTTP code
STATUS_CMP = re.compile(
    rf"""(?<![\w.])(typeof\s+)?(?:\w+(?:\?)?\.)*['"]?{FIELD}['"]?\s*(===?|!==?|<>|=(?!>)|:(?!:)|\.equals\(|\bis\b)\s*{VALUE}""",
    re.IGNORECASE,
)
STATUS_IN = re.compile(rf"""\b{FIELD}['"]?\]?\s+(?:NOT\s+)?IN\s*[(\[{{]([^)\]}}]*)[)\]}}]""", re.IGNORECASE)
GET_CMP = re.compile(rf"""\.get\(\s*['"]{FIELD}['"]\s*\)\s*(?:===?|!==?)\s*{VALUE}""", re.IGNORECASE)
STRINGS = re.compile(r"""\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|`[^`]*`""")
REVERSED_EQUALS = re.compile(rf"""['"]([A-Za-z][\w-]{{0,40}})['"]\.equals(?:IgnoreCase)?\(\s*(?:\w+\.)*{FIELD}\b""", re.IGNORECASE)
CASE_ARM = re.compile(r"""(?:\bcase\s+|\bwhen\s+|^\s*)(?:['"`]([A-Za-z][\w-]{1,40})['"`]|:([a-z]\w{1,40}))\s*(?::(?!:)|=>|\bthen\b|,|$)""")
DECLARATION = re.compile(
    r"^\s*(?:export\s+)?(?:const|enum|type|interface|readonly|(?:public|private|protected)?\s*static\s+final)\b"
    r"|^\s*case\s+\w+\s*(?:=|\(|$)|^\s*[A-Z]\w*\s+[A-Z]\w*\s*=\s*\"|^\s*[A-Z][A-Z0-9_]+\s*=\s*['\"]"
)
LITERAL_LIST = re.compile(r"""['"]([A-Za-z][\w-]{0,40})['"]""")


@dataclass
class FileScore:
    path: str
    lines: int = 0
    score: int = 0
    window: Tuple[int, int] = (0, 0)
    names: Dict[str, Tuple[str, int]] = field(default_factory=dict)  # key -> (spelling, first line)
    values: Dict[str, int] = field(default_factory=dict)  # "field=value" -> first line
    skipped: Optional[str] = None


def comment_marker(suffix: str) -> Optional[str]:
    if suffix in C_LIKE:
        return "//"
    if suffix in HASH:
        return "#"
    if suffix in DASH:
        return "--"
    return OTHER.get(suffix)


def strip_comments(lines: List[str], suffix: str) -> List[str]:
    """Blank out comments and docstrings, keeping line numbers. Approximate by design."""
    marker = comment_marker(suffix)
    c_like = suffix in C_LIKE
    out: List[str] = []
    in_block = False
    in_doc = False
    for line in lines:
        text = line
        if in_block:
            end = text.find("*/")
            if end < 0:
                out.append("")
                continue
            text = text[end + 2:]
            in_block = False
        if suffix == ".py":
            quotes = text.count('"""') + text.count("'''")
            if in_doc or (quotes and text.strip().startswith(('"""', "'''", 'r"""', 'f"""'))):
                in_doc = (not in_doc) if quotes % 2 else in_doc
                out.append("")
                continue
        if c_like:
            start = text.find("/*")
            if start >= 0 and "*/" not in text[start:]:
                text, in_block = text[:start], True
            text = re.sub(r"/\*.*?\*/", " ", text)
            if re.match(r"^\s*(?:\*\s|\*/|\*$)", text):
                out.append("")
                continue
        if marker:
            stripped = text.lstrip()
            if stripped.startswith(marker) and not (marker == "#" and stripped.startswith("#!")):
                out.append("")
                continue
            text = re.sub(rf"\s{re.escape(marker)}\s.*$", "", text)
        out.append(text)
    return out


def field_key(name: str) -> str:
    name = re.sub(r"^get(?=[A-Z])", "", name.strip("'\""))
    return name.lower().replace("_", "")


def collect(path: Path, lines: List[str], rel: str, window: int) -> FileScore:
    result = FileScore(rel, lines=len(lines))
    occurrences: List[Tuple[int, str, str]] = []  # (line, kind, key)
    suffix = path.suffix.lower()
    markup = suffix in MARKUP

    def name(key: str, spelling: str, number: int) -> None:
        result.names.setdefault(key, (spelling, number))
        occurrences.append((number, "n", key))

    def value(fld: str, val: str, number: int) -> None:
        if not val or val.lower() in IGNORE_VALUES:
            return
        key = f"{field_key(fld)}={val.lower()}"
        result.values.setdefault(key, number)
        occurrences.append((number, "v", key))

    for number, line in enumerate(strip_comments(lines, suffix), 1):
        if not line.strip():
            continue
        declaration = bool(DECLARATION.search(line))
        code_only = STRINGS.sub('""', line)
        for i, pattern in enumerate(IDENTIFIER_PATTERNS):
            if i == BARE_WORDS and declaration:
                continue  # enum members and constants named after states
            # Bare words (pending, running) count only outside strings; flags and timestamps also
            # count inside strings, where SQL names its columns.
            for m in pattern.finditer(code_only if i == BARE_WORDS else line):
                name(field_key(m.group(0)), m.group(0), number)
        for pattern in BOOL_DECLS:
            for m in pattern.finditer(line):
                ident = m.group(1)
                if WORD_END.search(ident):  # Paid, readInProgress; not RejectFailedCounter
                    name(field_key(ident), f"{ident} bool", number)
        if not declaration:
            for pattern in STATUS_DECLS:
                for m in pattern.finditer(line):
                    name(field_key(m.group(1)) + ":primitive", m.group(0).strip(), number)
        if declaration or suffix in NO_VALUES:
            continue
        for m in STATUS_CMP.finditer(line):
            typeof, fld, op = m.group(1), m.group(2), m.group(3)
            val = m.group(4) or m.group(5) or (f"#{m.group(6)}" if m.group(6) else "")
            if typeof:
                continue
            after = line[m.end():m.end() + 3]
            before = line[max(0, m.start() - 3):m.start()]
            if "|" in after or "|" in before:  # union type: status: 'a' | 'b'
                continue
            if markup and op == "=" and line[m.start(3) - 1:m.start(3)] != " " and line[m.end(3):m.end(3) + 1] in "\"'{":
                continue  # JSX/markup attribute: status="x"
            value(fld, val, number)
        for m in STATUS_IN.finditer(line):
            for v in LITERAL_LIST.findall(m.group(2)):
                value(m.group(1), v, number)
        for m in GET_CMP.finditer(line):
            value(m.group(1), m.group(2) or m.group(3) or (f"#{m.group(4)}" if m.group(4) else ""), number)
        for m in REVERSED_EQUALS.finditer(line):
            value(m.group(2), m.group(1), number)
        for m in CASE_ARM.finditer(line):
            val = m.group(1) or m.group(2)
            if val and val.lower() in STATE_WORDS:
                value("case", val, number)

    if occurrences:
        result.score, result.window = densest_window(occurrences, window)
    return result


def densest_window(occurrences: List[Tuple[int, str, str]], size: int) -> Tuple[int, Tuple[int, int]]:
    occurrences.sort()
    best, best_span = 0, (0, 0)
    counts: Counter = Counter()
    left = 0
    for right, (line, kind, key) in enumerate(occurrences):
        counts[(kind, key)] += 1
        while occurrences[left][0] <= line - size:
            lk = (occurrences[left][1], occurrences[left][2])
            counts[lk] -= 1
            if not counts[lk]:
                del counts[lk]
            left += 1
        names = sum(1 for k in counts if k[0] == "n")
        values = sum(1 for k in counts if k[0] == "v")
        score = 2 * names + min(values, 6)
        if score > best:
            best, best_span = score, (occurrences[left][0], line)
    return best, best_span


def skip_reason(rel_parts: Tuple[str, ...], name: str) -> Optional[str]:
    dirs = rel_parts[:-1]
    if any(p in ALWAYS_SKIP for p in dirs):
        return "vendored or tool directory"
    if any(p in TEST_DIRS for p in dirs) or TEST_NAME.search(name):
        return "test"
    if dirs and dirs[0] in ROOT_ONLY_SKIP:
        return f"top-level {dirs[0]}/"
    if GENERATED_NAME.search(name):
        return "generated (name)"
    return None


def scan(args: Tuple[str, str, int]) -> FileScore:
    absolute, rel, window = args
    path = Path(absolute)
    try:
        if path.stat().st_size > MAX_BYTES:
            return FileScore(rel, skipped="over 1 MB")
        raw = path.read_bytes()
    except OSError:
        return FileScore(rel, skipped="unreadable")
    text = raw.decode("utf-8", "replace")
    lines = [line.rstrip("\r") for line in text.split("\n")]
    if any(len(line) > MAX_LINE for line in lines):
        return FileScore(rel, skipped="minified (long lines)")
    if GENERATED_HEADER.search("\n".join(lines[:8])):
        return FileScore(rel, skipped="generated (header)")
    return collect(path, lines, rel, window)


def git(root: Path, *args: str) -> Optional[List[str]]:
    try:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True, timeout=300).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def list_files(root: Path, paths: List[str]) -> Tuple[List[str], List[str]]:
    """Return (relative paths, notes)."""
    notes: List[str] = []
    tracked = git(root, "ls-files", "-z", "--cached", "--recurse-submodules", "--", *paths)
    if tracked is not None:
        untracked = git(root, "ls-files", "-z", "--others", "--exclude-standard", "--", *paths) or []
        files = list(dict.fromkeys(tracked + untracked))  # a file in conflict is listed once per stage
        if files:
            return files, notes
        notes.append("git lists no files here (ignored by git?); scanned the filesystem instead")
    files = []
    for base in [root / p for p in paths] or [root]:
        if base.is_file():
            files.append(base.relative_to(root).as_posix())
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in ALWAYS_SKIP]
            files.extend((Path(dirpath) / f).relative_to(root).as_posix() for f in filenames)
    return files, notes


def main() -> int:
    if sys.version_info < (3, 8):
        print("candidates.py needs Python 3.8 or later", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", nargs="?", default=".", help="repository root or directory")
    parser.add_argument("--paths", nargs="*", default=[], help="limit the scan to these paths under ROOT")
    parser.add_argument("--top", type=int, default=40, help="files to list; 0 lists none (default 40)")
    parser.add_argument("--min-files", type=int, default=3, help="files a status value must appear in (default 3)")
    parser.add_argument("--window", type=int, default=40, help="lines per scoring window (default 40)")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1, help="parallel processes")
    args = parser.parse_args()
    window = max(1, args.window)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    for p in args.paths:
        if not (root / p).exists():
            print(f"not found under {root}: {p}", file=sys.stderr)
            return 2

    files, notes = list_files(root, args.paths)
    skipped: Counter = Counter()
    jobs: List[Tuple[str, str, int]] = []
    for rel in files:
        parts = tuple(Path(rel).parts)
        if Path(rel).suffix.lower() not in SOURCE_SUFFIXES:
            continue
        reason = skip_reason(parts, parts[-1])
        if reason:
            skipped[reason] += 1
            continue
        jobs.append((str(root / rel), Path(rel).as_posix(), window))

    results: List[FileScore]
    if args.jobs > 1 and len(jobs) > 200:
        try:
            with ProcessPoolExecutor(max_workers=args.jobs) as pool:
                results = list(pool.map(scan, jobs, chunksize=64))
        except (OSError, RuntimeError):
            results = [scan(j) for j in jobs]
    else:
        results = [scan(j) for j in jobs]

    for r in results:
        if r.skipped:
            skipped[r.skipped] += 1
    scored = [r for r in results if not r.skipped and (r.names or r.values)]
    scored.sort(key=lambda r: (-r.score, -(len(r.names) * 2 + len(r.values)), r.path))

    print(f"# statecraft candidates in {root}" + (f" (paths: {' '.join(args.paths)})" if args.paths else ""))
    print(f"# scanned {len(jobs) - sum(1 for r in results if r.skipped)} source files; "
          f"{len(scored)} with lifecycle signals")
    if skipped:
        print("# skipped: " + ", ".join(f"{n} {reason}" for reason, n in skipped.most_common()))
    for note in notes:
        print(f"# note: {note}")
    if args.top:
        print(f"# score = densest {window}-line window: 2 x distinct identifiers + distinct status values (at most 6)\n")
    for r in scored[: args.top]:
        lo, hi = r.window
        in_window = sorted((v for v in r.names.values() if lo <= v[1] <= hi), key=lambda v: v[1])
        others = sorted((v for v in r.names.values() if not lo <= v[1] <= hi), key=lambda v: v[1])
        names = ", ".join(f"{n}:{line}" for n, line in (in_window + others)[:12])
        vals = ", ".join(f"{k}:{line}" for k, line in sorted(r.values.items(), key=lambda kv: kv[1])[:8])
        print(f"{r.score:4d}  {r.path}  (hotspot lines {lo}-{hi} of {r.lines})")
        if names:
            print(f"      identifiers: {names}")
        if vals:
            print(f"      status values: {vals}")

    spread: Dict[str, List[str]] = defaultdict(list)
    for r in scored:
        for key in r.values:
            spread[key].append(r.path)
    scattered = sorted(
        ((k, ps) for k, ps in spread.items() if len(ps) >= args.min_files), key=lambda kv: (-len(kv[1]), kv[0])
    )
    print(f"\n# status values compared or assigned in {args.min_files}+ files (scattered transitions)")
    for key, paths in scattered[:25]:
        shown = ", ".join(paths[:5]) + (f", +{len(paths) - 5} more" if len(paths) > 5 else "")
        print(f"{len(paths):4d}  {key}: {shown}")
    if not scattered:
        print("      none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
