import sys
from pathlib import Path

# The CDK packages `services/` as the Lambda asset root, so handlers import
# `amazai.*`. Mirror that here so tests exercise the real import path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))
