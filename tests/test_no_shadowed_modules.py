"""A function must not assign to a name that the module imports.

Python decides a name is *local* for a whole function if it is assigned to
anywhere in it. So `threads = store.query_index(...)` in one branch of
`handlers.api._route` turned the imported `threads` module into an unbound local
for every other branch -- and a later `threads.event(...)` in a different branch
raised `UnboundLocalError`, which the handler reports as a 500. Nothing in the
branch that *assigned* was wrong, and nothing in the branch that *failed* was
either, which is what makes it worth a test rather than a comment.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "services"


def imported_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("amazai"):
            names |= {(a.asname or a.name) for a in node.names}
        elif isinstance(node, ast.Import):
            names |= {(a.asname or a.name).split(".")[0] for a in node.names}
    return names


def local_assignments(fn):
    out = set()
    for node in ast.walk(fn):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            targets = [node.target]
        elif isinstance(node, ast.comprehension):
            targets = [node.target]
        for t in targets:
            for n in ast.walk(t):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    out.add(n.id)
    return out


def test_no_function_shadows_a_module_it_imports():
    offenders = []
    for path in sorted(ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text())
        modules = imported_names(tree)
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                clash = local_assignments(fn) & modules
                # A function's own parameters may legitimately share a name; only
                # flag assignments that are not parameters.
                params = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
                clash -= params
                if clash:
                    offenders.append(f"{path.relative_to(ROOT)}::{fn.name} shadows {sorted(clash)}")
    assert offenders == [], "\n".join(offenders)
