"""AmazAI routine handler.

Stub. Phase 1 provisions infrastructure only; the routine implementation lands in
the phase named in docs/architecture/12-roadmap.md. Returning 501 keeps a
deployed stack honest about what is and is not wired up yet.
"""

import json
import os

TABLE_NAME = os.environ["TABLE_NAME"]


def handler(event, context):  # noqa: ARG001
    return {
        "statusCode": 501,
        "headers": {"content-type": "application/json"},
        "body": json.dumps({"error": "not_implemented", "handler": "routine"}),
    }
