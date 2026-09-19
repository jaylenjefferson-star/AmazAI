#!/usr/bin/env bash
# Build the boto3 Lambda layer.
#
# Lambda's bundled boto3 lags the AgentCore harness APIs. Without this layer,
# create_harness fails with an unhelpful ParamValidationError.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/_python.sh
. "$ROOT/scripts/_python.sh"
TARGET="$ROOT/layer/python"

# The wheels are selected by the --platform/--python-version flags below, not
# by the interpreter running pip — but macOS's 3.9 ships a pip old enough to
# handle that combination inconsistently, so resolve a modern one first.
if ! resolve_python; then
  echo "$(python_floor_message)" >&2
  exit 1
fi

rm -rf "$TARGET"
mkdir -p "$TARGET"

"$PY" -m pip install \
  --upgrade \
  --target "$TARGET" \
  --platform manylinux2014_aarch64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  boto3 botocore 'pyjwt[crypto]'

# Trim what Lambda does not need, to stay well under the layer size limit.
find "$TARGET" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$TARGET" -type d -name 'tests' -prune -exec rm -rf {} +
find "$TARGET" -type d -name '*.dist-info' -prune -exec rm -rf {} +

echo "Layer built at $TARGET"
du -sh "$TARGET"
