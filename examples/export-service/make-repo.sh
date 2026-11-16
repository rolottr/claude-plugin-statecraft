#!/bin/sh
# Builds a small git repo to try statecraft on: `main` holds an exports table, and the
# `feature/export-worker` branch adds a worker whose lifecycle lives in flags and timestamps.
# Usage: examples/export-service/make-repo.sh [target-dir]   (default: ./export-service)
set -eu
here=$(cd "$(dirname "$0")" && pwd)
target=${1:-export-service}
[ -e "$target" ] && { echo "$target already exists" >&2; exit 1; }
mkdir -p "$target" && cd "$target"
git init -q -b main
cp -R "$here/base/." .
git add -A && git -c user.name=example -c user.email=example@example.com commit -qm "Exports table"
git checkout -qb feature/export-worker
rm -rf app && cp -R "$here/branch/." .
git add -A && git -c user.name=example -c user.email=example@example.com commit -qm "Export worker"
echo "Ready: cd $target && claude, then ask: statecraft this branch"
