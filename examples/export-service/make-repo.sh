#!/bin/sh
# Builds a small service to try statecraft on, with a local "origin" remote like a real clone:
#   main                   an exports table (pushed to origin)
#   feature/export-worker  adds a worker whose lifecycle lives in flags and timestamps (not pushed)
# Usage: examples/export-service/make-repo.sh [target-dir]   (default: ./export-service)
# The remote is created next to the target, as <target>-origin.git.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
target=${1:-export-service}
origin="$target-origin.git"
for path in "$target" "$origin"; do
  [ -e "$path" ] && { echo "$path already exists" >&2; exit 1; }
done
cleanup() { [ "$?" -eq 0 ] || rm -rf "$target" "$origin"; }
trap cleanup EXIT

g() { git -c user.name=example -c user.email=example@example.com -c commit.gpgsign=false \
        -c core.hooksPath=/dev/null "$@"; }

git init -q --bare "$origin"
origin_abs=$(cd "$origin" && pwd)
mkdir -p "$target"
cd "$target"
git init -q
git symbolic-ref HEAD refs/heads/main   # works on every git version, unlike init -b
cp -R "$here/base/." .
g add -A
g commit -qm "Exports table"
git remote add origin "$origin_abs"
g push -q -u origin main
g checkout -qb feature/export-worker
rm -rf app tests AGENTS.md
cp -R "$here/branch/." .
g add -A
g commit -qm "Export worker"
echo "Ready: cd $target && claude, then ask: statecraft this branch   (or: statecraft: fix this branch)"
