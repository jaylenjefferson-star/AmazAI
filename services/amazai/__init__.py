"""AmazAI shared library.

Everything in this package is control-plane logic: it decides what an agent is
allowed to do, tracks what it did, and records proof. The model is never
consulted by any function here.

The split matters. `docs/architecture/01-identity-and-boundaries.md` claims that
an injected instruction can waste a turn but cannot widen access. That claim is
only true because permission, budget, and approval decisions are made by the
deterministic code in this package, which never reads model output.
"""

__all__ = ["cost", "keys", "policy", "redact", "router", "states"]
