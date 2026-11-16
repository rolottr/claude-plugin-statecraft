#!/usr/bin/env python3
"""Rank source files that probably hold implicit state machines.

A language-agnostic heuristic for the statecraft sweep. Each file is lexed (comments removed, strings
kept apart) and searched for:

- lifecycle-like identifiers: busy flags (isLoading, hasFailed), timestamps acting as states
  (processed_at, StartedAt), boolean fields named after a lifecycle (Paid bool, is_paid BOOLEAN,
  models.BooleanField, t.boolean :paid), status fields typed as plain ints or strings, error holders,
  retry counters, handles to running work, useState loading/error/data clusters;
- status values: a status/state/phase/stage field compared with or assigned a literal.

A file's score is its densest 40-line window: 2 x distinct identifiers + distinct status values
(at most 6). It also lists status values that appear in several files, a sign that transitions are
scattered. Declarations (enum members, constants, union types) and switch arms are not scored: they
are what explicit machines look like. Switch arms on lifecycle words are shown for context.

The output points auditors at places to look. It ranks by names, so it misses clusters with unusual
names, counts some framework-owned state, and cannot tell a well-owned status column from a scattered
one. It is not a list of findings.

Usage:
  python3 -I candidates.py ROOT [--paths P ...] [--top N] [--full] [--min-files N] [--window N] [--jobs N]

ROOT is the repository root (or any directory). --paths limits the scan to paths under ROOT; output
paths are always relative to ROOT. Python 3.8+, standard library only.
"""

import sys

if sys.version_info < (3, 8):
    sys.stderr.write("candidates.py needs Python 3.8 or later\n")
    sys.exit(2)

import argparse  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402
from concurrent.futures import ProcessPoolExecutor  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Dict, List, Optional, Tuple  # noqa: E402

# Lexical families: (line comment, block open, block close, quotes, quotes that may span lines)
C_FAMILY = ("//", "/*", "*/", ('"', "'", "`"), ("`",))
SPECS = {}
for _s in (".go .rs .java .kt .kts .scala .sc .cs .fs .fsx .swift .m .mm .c .cc .cpp .cxx .h .hpp .ts .tsx .js "
           ".jsx .mjs .cjs .mts .cts .vue .svelte .dart .groovy .zig .php .prisma .proto").split():
    SPECS[_s] = C_FAMILY
for _s in ".py .pyi".split():
    SPECS[_s] = ("#", None, None, ('"""', "'''", '"', "'"), ('"""', "'''"))
for _s in ".ex .exs".split():
    SPECS[_s] = ("#", None, None, ('"""', '"', "'"), ('"""',))
for _s in ".rb .cr .jl .nim .pl .pm .graphql .r".split():
    SPECS[_s] = ("#", None, None, ('"', "'"), ())
SPECS[".sql"] = ("--", "/*", "*/", ("'", '"'), ())
for _s in ".lua .hs .elm".split():
    SPECS[_s] = ("--", None, None, ('"',), ())
SPECS[".erl"] = ("%", None, None, ('"',), ())
SPECS[".clj"] = (";", None, None, ('"',), ())
for _s in ".ml .mli".split():
    SPECS[_s] = (None, "(*", "*)", ('"',), ())
SOURCE_SUFFIXES = set(SPECS)
MARKUP = {".tsx", ".jsx", ".vue", ".svelte"}
TS_FAMILY = {".ts", ".tsx", ".mts", ".cts"}
GO_NAMED_TYPE = re.compile(r"^\s*type\s+\w+\s+(?:string|u?int\d*)\b")  # type OrderState string: explicit
TYPE_BLOCK_START = re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?(?:interface|type)\b")
NO_VALUES = {".proto", ".graphql", ".prisma"}  # "= 2" is a field number there, not a status

# Skipped wherever they appear.
ALWAYS_SKIP = {
    ".git", "node_modules", "vendor", "third_party", "bower_components", ".build", "Pods", "DerivedData",
    ".venv", "venv", "site-packages", "__pycache__", ".next", ".nuxt", ".svelte-kit", ".gradle", ".idea",
    ".terraform", ".tox", ".mypy_cache", ".pytest_cache", ".claude",
}
TEST_DIRS = {
    "test", "tests", "__tests__", "testdata", "testing", "testutil", "testutils", "unittest", "unittests", "e2e",
    "mocks", "__mocks__", "__snapshots__",
}
# Skipped only as the first path component under ROOT (elsewhere they are often real code), and not when
# --paths names them.
ROOT_ONLY_SKIP = {"build", "dist", "out", "target", "coverage", "spec", "fixtures"}
TEST_NAME = re.compile(
    r"[._-](?:test|tests|spec)\.\w+$|^test_\w*\.py$|_test\.go$|Tests?\.(?:swift|kt|java|cs)$"
    r"|(?:Test|Tests|_test|_unittest)\.(?:cpp|cc|cxx|c|h|hpp|m|mm)$"
)
GENERATED_NAME = re.compile(
    r"\.min\.|\.pb\.|_pb2|\.generated\.|^generated\.|^zz_generated|_generated\.|^generated_|\.g\.dart$|\.d\.ts$"
    r"|\.designer\.cs$"
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
    "archived|deleted|closed|opened|timed_?out|aborted|succeeded|resolved|acknowledged|dispatched|"
    "uploading|uploaded|downloading|downloaded|saving|saved|building|built|deploying|deployed|importing|"
    "imported|exporting|exported"
)
WORD_END = re.compile(rf"(?:{WORDS})$", re.IGNORECASE)
STATE_WORDS = set(
    "queued pending active inactive idle open closed new draft archived error success succeeded failed failure "
    "done complete completed cancelled canceled running processing ready expired paid unpaid shipped delivered "
    "refunded rejected approved blocked suspended scheduled started stopped finished timeout timed_out retrying "
    "waiting in_progress inprogress loading loaded connected disconnected connecting reconnecting locked "
    "unlocked verified unverified confirmed published unpublished claimed settled aborted resolved".split()
)
REQUEST_STATE_NAMES = {"error", "err", "data", "result", "response", "status", "state", "phase", "stage"}
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
BARE_WORDS = 2  # index of the bare-word pattern above: counted only outside strings and declarations
# Boolean declarations; counted when the name ends with a lifecycle word (Paid bool, shipped: boolean).
BOOL_DECLS = (
    re.compile(r"\b([A-Za-z_]\w*)\s*:\s*(?:bool|boolean|Bool|Boolean)\b"),
    re.compile(r"\b([A-Za-z_]\w*)\s+(?:bool|boolean|BOOLEAN|BOOL|TINYINT\(1\))\b"),
    re.compile(r"\b(?:bool|boolean|Boolean|BOOL)\??\s+([A-Za-z_]\w*)\s*[;=,){]"),
    re.compile(r"\b([A-Za-z_]\w*)\s*=\s*(?:models\.)?(?:Null)?BooleanField\b"),  # Django
    re.compile(r"\b([A-Za-z_]\w*)\s*(?::[^=]*)?=\s*(?:\w+\.)?(?:mapped_column|Column)\(\s*(?:\w+\.)?Boolean\b"),
    re.compile(r"\bt\.boolean\s+:(\w+)"),  # Rails migration
    re.compile(r"\badd_column\s+:\w+,\s*:(\w+),\s*:boolean\b"),
    # uploading = false, self.done = False, var failed = true
    re.compile(r"\b([A-Za-z_]\w*)\s*(?::\s*\w+\s*)?(?<![=!<>])=(?!=)\s*(?:false|true|False|True|NO|YES)\b"),
)
# Status fields typed as plain ints or strings: an unconstrained status.
PRIMITIVE = (
    r"(?:u?int\d*|Int\d*|i\d+|u\d+|integer|INTEGER|INT|SMALLINT|TINYINT|number|string|String|str|TEXT"
    r"|VARCHAR(?:\(\d+\))?|CHAR(?:\(\d+\))?)"
)
STATUS_DECLS = (
    re.compile(rf"\b{FIELD}\s+{PRIMITIVE}\b", re.IGNORECASE),
    re.compile(rf"\b{FIELD}\s*:\s*{PRIMITIVE}\b", re.IGNORECASE),
    re.compile(rf"\b(?:int|Integer|short|String|string)\??\s+{FIELD}\s*[;={{]", re.IGNORECASE),
    re.compile(
        rf"\b{FIELD}\s*=\s*(?:models\.)?(?:Char|Text|Integer|SmallInteger|PositiveSmallInteger|PositiveInteger)Field\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{FIELD}\s*(?::[^=]*)?=\s*(?:\w+\.)?(?:mapped_column|Column)\(\s*(?:\w+\.)?(?:String|Unicode|Text|Integer|SmallInteger)\b",
        re.IGNORECASE,
    ),
    re.compile(rf"\bt\.(?:string|integer|text)\s+:{FIELD}\b", re.IGNORECASE),
    re.compile(rf"\badd_column\s+:\w+,\s*:{FIELD},\s*:(?:string|integer|text)\b", re.IGNORECASE),
)
USE_STATE = re.compile(r"\[\s*(\w+)\s*,\s*set\w+\s*\]\s*=\s*(?:React\.)?useState\b")
VALUE = r"""(?:['"`]([A-Za-z][\w-]{0,40})['"`]|:([a-z]\w{0,40})\b|(\d{1,2})(?![\w.]))"""  # 0-99; 200 is an HTTP code
STATUS_CMP = re.compile(
    rf"""(?<![\w.])(typeof\s+)?(?:\w+(?:\?)?\.)*['"]?{FIELD}['"]?\s*(===?|!==?|<>|=(?!>)|:(?!:)|\.equals\(|\bis\b)\s*{VALUE}""",
    re.IGNORECASE,
)
STATUS_IN = re.compile(rf"""\b{FIELD}['"]?\]?\s+(?:NOT\s+)?IN\s*[(\[{{]([^)\]}}]*)[)\]}}]""", re.IGNORECASE)
GET_CMP = re.compile(rf"""\.get\(\s*['"]{FIELD}['"]\s*\)\s*(?:===?|!==?)\s*{VALUE}""", re.IGNORECASE)
REVERSED_EQUALS = re.compile(
    rf"""['"]([A-Za-z][\w-]{{0,40}})['"]\.equals(?:IgnoreCase)?\(\s*(?:\w+\.)*{FIELD}\b""", re.IGNORECASE
)
CASE_ARM = re.compile(
    r"""(?:\bcase\s+|\bwhen\s+|^\s*)(?:['"`]([A-Za-z][\w-]{1,40})['"`]|:([a-z]\w{1,40}))\s*(?::(?!:)|=>|\bthen\b|,|$)"""
)
DECLARATION = re.compile(
    r"^\s*(?:export\s+)?(?:enum|type|interface)\b"
    r"|^\s*(?:public|private|protected|internal)?\s*static\s+final\b"
    r"|^\s*case\s+\w+\s*(?:=|\(|$)"
    r"|^\s*[A-Z]\w*\s+[A-Z]\w*\s*=\s*\""  # Go typed constant in a const block
    r"|^\s*(?:export\s+)?(?:(?:const|let|var|val|static|final|public|private|readonly)\s+)*[A-Z][A-Z0-9_]*"
    r"\s*(?::\s*\w+\s*)?=\s*['\"]"  # STATUS_PAID = 'paid'
)
CONSTANT_BEFORE = re.compile(r"(?:\bconst|\breadonly|\bfinal|\bstatic)\s+(?:[\w<>\[\],?]+\s+)?$")
LITERAL_LIST = re.compile(r"""['"]([A-Za-z][\w-]{0,40})['"]""")


@dataclass
class FileScore:
    path: str
    lines: int = 0
    score: int = 0
    window: Tuple[int, int] = (0, 0)
    names: Dict[str, Tuple[str, int]] = field(default_factory=dict)  # key -> (spelling, first line)
    values: Dict[str, int] = field(default_factory=dict)  # "field=value" -> first line
    arms: Dict[str, int] = field(default_factory=dict)  # switch arms on lifecycle words -> first line
    skipped: Optional[str] = None


def lex(lines: List[str], suffix: str) -> List[Tuple[str, str]]:
    """Return (text without comments, text without comments or string contents) per line."""
    line_comment, block_open, block_close, quotes, multiline = SPECS[suffix]
    tokens = [t for t in (line_comment, block_open) if t] + list(quotes)
    opener = re.compile("|".join(re.escape(t) for t in tokens))
    closers = {q: re.compile(r"\\.|" + re.escape(q), re.S) for q in quotes}
    rust = suffix == ".rs"
    out: List[Tuple[str, str]] = []
    state: Optional[str] = None  # None, "block", or the open quote
    for line in lines:
        keep: List[str] = []
        code: List[str] = []
        i, n = 0, len(line)
        while i < n:
            if state == "block":
                j = line.find(block_close, i)
                if j < 0:
                    i = n
                    break
                i, state = j + len(block_close), None
                keep.append(" ")
                code.append(" ")
                continue
            if state is not None:
                closer = closers[state]
                j = i
                while True:
                    m = closer.search(line, j)
                    if m is None or m.group(0) == state:
                        break
                    j = m.end()
                if m is None:
                    keep.append(line[i:])
                    if state not in multiline:
                        state = None  # an unterminated one-line string ends with its line
                    i = n
                    break
                keep.append(line[i:m.end()])
                code.append(state)
                i, state = m.end(), None
                continue
            m = opener.search(line, i)
            if m is None:
                keep.append(line[i:])
                code.append(line[i:])
                break
            keep.append(line[i:m.start()])
            code.append(line[i:m.start()])
            token = m.group(0)
            if token == line_comment:
                break
            if token == block_open:
                state, i = "block", m.end()
                continue
            if rust and token == "'" and not (line[m.end():m.end() + 2].endswith("'") or line[m.end():m.end() + 1] == "\\"):
                keep.append("'")  # a lifetime such as 'a, not a char literal
                code.append("'")
                i = m.end()
                continue
            keep.append(token)
            code.append(token)
            state, i = token, m.end()
        out.append(("".join(keep), "".join(code)))
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

    type_depth = 0  # inside a TypeScript interface or type literal: literal types, not values
    for number, (line, code_only) in enumerate(lex(lines, suffix), 1):
        if not line.strip():
            continue
        declaration = bool(DECLARATION.search(line))
        if suffix in TS_FAMILY:
            braces = code_only.count("{") - code_only.count("}")
            if type_depth > 0:
                declaration, type_depth = True, max(0, type_depth + braces)
            elif TYPE_BLOCK_START.match(line):
                declaration, type_depth = True, max(0, braces)
            if line.lstrip().startswith("|"):
                declaration = True  # a member of a union type
        for i, pattern in enumerate(IDENTIFIER_PATTERNS):
            if i == BARE_WORDS and declaration:
                continue
            # Bare words (pending, running) count only outside strings; flags and timestamps also count
            # inside strings, where SQL names its columns.
            for m in pattern.finditer(code_only if i == BARE_WORDS else line):
                name(field_key(m.group(0)), m.group(0), number)
        for pattern in BOOL_DECLS:
            for m in pattern.finditer(line):
                ident = m.group(1)
                if WORD_END.search(ident):  # Paid, readInProgress; not RejectFailedCounter
                    name(field_key(ident), f"{ident} bool", number)
        for m in USE_STATE.finditer(line):
            ident = m.group(1)
            if WORD_END.search(ident) or ident.lower() in REQUEST_STATE_NAMES:
                name(field_key(ident), f"[{ident}] useState", number)
        for pattern in () if GO_NAMED_TYPE.match(line) else STATUS_DECLS:
            for m in pattern.finditer(line):
                name(field_key(m.group(1)) + ":primitive", m.group(0).strip(), number)
        if declaration or suffix in NO_VALUES:
            continue
        for m in STATUS_CMP.finditer(line):
            typeof, fld, op = m.group(1), m.group(2), m.group(3)
            val = m.group(4) or m.group(5) or (f"#{m.group(6)}" if m.group(6) else "")
            if typeof or CONSTANT_BEFORE.search(line[:m.start()]):
                continue  # typeof x.status === 'string'; const STATUS = 'x'
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
                result.arms.setdefault(val.lower(), number)

    if occurrences:
        result.score, result.window = densest_window(occurrences, window)
    return result


def densest_window(occurrences: List[Tuple[int, str, str]], size: int) -> Tuple[int, Tuple[int, int]]:
    occurrences.sort()
    best, best_span = 0, (0, 0)
    counts: Counter = Counter()
    left = 0
    for line, kind, key in occurrences:
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


def skip_reason(parts: Tuple[str, ...], allowed_roots: set) -> Optional[str]:
    dirs, name = parts[:-1], parts[-1]
    if any(p in ALWAYS_SKIP for p in dirs):
        return "vendored or tool directory"
    if any(p in TEST_DIRS for p in dirs) or TEST_NAME.search(name):
        return "test"
    if dirs and dirs[0] in ROOT_ONLY_SKIP and dirs[0] not in allowed_roots:
        return f"top-level {dirs[0]}/"
    if GENERATED_NAME.search(name):
        return "generated (name)"
    return None


def scan(job: Tuple[str, str, int]) -> FileScore:
    absolute, rel, window = job
    path = Path(absolute)
    try:
        if path.stat().st_size > MAX_BYTES:
            return FileScore(rel, skipped="over 1 MB")
        raw = path.read_bytes()
    except OSError:
        return FileScore(rel, skipped="unreadable")
    text = raw.decode("utf-8", "replace")
    lines = [line.rstrip("\r") for line in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()  # the text ended with a newline
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


def walk(root: Path, target: Path) -> List[str]:
    if target.is_file():
        return [target.relative_to(root).as_posix()]
    found = []
    for dirpath, dirnames, filenames in os.walk(target):
        dirnames[:] = [d for d in dirnames if d not in ALWAYS_SKIP]
        found.extend((Path(dirpath) / f).relative_to(root).as_posix() for f in filenames)
    return found


def list_files(root: Path, paths: List[str]) -> Tuple[List[str], List[str]]:
    """Return (relative paths, notes). Each --paths entry is listed on its own."""
    notes: List[str] = []
    in_git = git(root, "rev-parse", "--is-inside-work-tree") is not None
    files: List[str] = []
    for p in paths or ["."]:
        if in_git:
            tracked = git(root, "ls-files", "-z", "--cached", "--recurse-submodules", "--", p) or []
            untracked = git(root, "ls-files", "-z", "--others", "--exclude-standard", "--", p) or []
            if tracked or untracked:
                files += tracked + untracked
                continue
            notes.append(f"{p}: git lists no files here (ignored by git?); scanned the filesystem instead")
        files += walk(root, (root / p).resolve())
    return list(dict.fromkeys(files)), notes  # a file in merge conflict is listed once per stage


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", nargs="?", default=".", help="repository root or directory")
    parser.add_argument("--paths", nargs="*", default=[], help="limit the scan to these paths under ROOT")
    parser.add_argument("--top", type=int, default=40, help="files to list; 0 lists none (default 40)")
    parser.add_argument("--full", action="store_true", help="list every path for scattered status values")
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
    allowed_roots = {Path(p).parts[0] for p in args.paths if Path(p).parts}

    files, notes = list_files(root, args.paths)
    skipped: Counter = Counter()
    other_types: Counter = Counter()
    jobs: List[Tuple[str, str, int]] = []
    for rel in files:
        parts = Path(rel).parts
        suffix = Path(rel).suffix.lower()
        if suffix not in SOURCE_SUFFIXES:
            other_types[suffix or "(none)"] += 1
            continue
        reason = skip_reason(parts, allowed_roots)
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
    if other_types:
        top = ", ".join(f"{ext} {n}" for ext, n in other_types.most_common(6))
        print(f"# not source (not scanned): {sum(other_types.values())} files ({top})")
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
        arms = ", ".join(f"{k}:{line}" for k, line in sorted(r.arms.items(), key=lambda kv: kv[1])[:8])
        print(f"{r.score:4d}  {r.path}  (hotspot lines {lo}-{hi} of {r.lines})")
        if names:
            print(f"      identifiers: {names}")
        if vals:
            print(f"      status values: {vals}")
        if arms:
            print(f"      switch arms (not scored): {arms}")

    spread: Dict[str, List[str]] = defaultdict(list)
    for r in scored:
        for key in r.values:
            spread[key].append(r.path)
    scattered = sorted(
        ((k, ps) for k, ps in spread.items() if len(ps) >= args.min_files), key=lambda kv: (-len(kv[1]), kv[0])
    )
    print(f"\n# status values compared or assigned in {args.min_files}+ files (scattered transitions)")
    for key, paths in scattered if args.full else scattered[:25]:
        shown = ", ".join(paths if args.full else paths[:5])
        if not args.full and len(paths) > 5:
            shown += f", +{len(paths) - 5} more (--full lists all)"
        print(f"{len(paths):4d}  {key}: {shown}")
    if not scattered:
        print("      none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
