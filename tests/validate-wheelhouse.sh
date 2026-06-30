#!/bin/bash
set -eux

# 1. Create clean, temporary constraint files
ENV_CONSTRAINTS=$(mktemp)
BUILD_CONSTRAINTS=$(mktemp)

# 2. FIX FOR PYTHON 3.10 (Legacy Pip Bootstrap)
# Strip out strict matching pins for pip/setuptools so the bootstrap doesn't crash.
# Instead, append a loose upper bound (<82) so old pip enforces it during builds.
grep -E -v "^(setuptools|pip)" src/wheelhouse.txt > "$ENV_CONSTRAINTS"
echo "setuptools<82" >> "$ENV_CONSTRAINTS"
export PIP_CONSTRAINT="$ENV_CONSTRAINTS"

# 3. FIX FOR PYTHON 3.12 (Modern Pip Build Isolation)
# Force modern pip isolated sandboxes to pick an older setuptools that still 
# contains 'pkg_resources'. This allows pbr==6.1.0 to compile safely.
echo "setuptools<82" > "$BUILD_CONSTRAINTS"
export PIP_BUILD_CONSTRAINT="$BUILD_CONSTRAINTS"

build_dir="$(mktemp -d --tmpdir=${TOX_ENV_DIR}/tmp)"
charm="$(egrep '^name\S*:' ./metadata.yaml | awk '{ print $2 }')"
function cleanup { rm -rf "$build_dir"; }
trap cleanup EXIT

charm-build src --build-dir "$build_dir" --debug
pip install -f "$build_dir/$charm/wheelhouse" --no-index --no-cache-dir "$build_dir"/$charm/wheelhouse/*
