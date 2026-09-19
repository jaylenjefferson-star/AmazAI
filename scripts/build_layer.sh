#!/usr/bin/env bash
# Build the boto3 Lambda layer.
#
# Lambda's bundled boto3 lags the AgentCore harness APIs. Without this layer,
# create_harness fails with an unhelpful ParamValidationError.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT/layer/python"

rm -rf "$TARGET"
mkdir -p "$TARGET"

python3 -m pip install \
  --upgrade \
  --target "$TARGET" \
  --platform manylinux2014_aarch64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  boto3 botocore

# Trim what Lambda does not need, to stay well under the layer size limit.
find "$TARGET" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$TARGET" -type d -name 'tests' -prune -exec rm -rf {} +
find "$TARGET" -type d -name '*.dist-info' -prune -exec rm -rf {} +

echo "Layer built at $TARGET"
du -sh "$TARGET"
