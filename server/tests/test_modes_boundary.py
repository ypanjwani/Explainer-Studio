"""Reader and Explorer (explainer/modes/, new pages and v3 follow-ups) never depend on another mode's code, so every other
mode can be deleted without touching them.

1. Every import under modes/ points at modes/ itself or a shared tool on ALLOWED (read from the source).
2. In a fresh Python, with every other explainer module blocked, both modes import, make a page, and understand and run a
   v3 follow-up (catches a shared tool that quietly imports another mode, e.g. feedback.py importing codelab).
3. Every prompt, format, constant and menu the two modes and their shared tools look up exists.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from explainer import data

SERVER = Path(__file__).resolve().parents[1]
PKG = SERVER / "explainer"
MODES = PKG / "modes"

# Shared tools: no mode logic. Anything else under explainer/ belongs to a mode (or to routes/follow-ups) and is off limits.
ALLOWED = {
    "explainer", "explainer.data", "explainer.llm", "explainer.jev", "explainer.pricing", "explainer.js", "explainer.json_util",
    "explainer.brief", "explainer.design", "explainer.clarity", "explainer.factcheck", "explainer.notes", "explainer.filter",
    "explainer.paper_cache", "explainer.db", "explainer.followup",
    "explainer.versions",  # the version store (follow-ups v5): code only, any follow-up version may read it
    "explainer.core", "explainer.core.compact", "explainer.core.edit", "explainer.core.examples", "explainer.core.expr",
    "explainer.core.factcheck", "explainer.core.followup", "explainer.core.references", "explainer.core.scene", "explainer.core.schema",
    "explainer.core.spec", "explainer.core.validate", "explainer.core.views", "explainer.core.color",
}
# Shared tools the modes' code reaches (scanned for prompt/format lookups in test 3).
SHARED_SOURCES = sorted(PKG / f"{m.split('.')[-1]}.py" for m in ALLOWED if m.count(".") == 1 and m != "explainer.core") + sorted(p for p in (PKG / "core").glob("*.py") if p.name != "lab.py")


def module_of(path: Path) -> str:
    rel = path.relative_to(SERVER).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imports_of(path: Path) -> set[str]:
    """Every explainer module the file imports, resolved (relative imports included)."""
    mod = module_of(path)
    package = mod if path.name == "__init__.py" else mod.rsplit(".", 1)[0]
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names if a.name.startswith("explainer")}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[:len(base) - (node.level - 1)]
                target = ".".join(base + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            if not target.startswith("explainer"):
                continue
            out.add(target)
            # "from . import data" imports a module, not a name
            for a in node.names:
                if (PKG.parent / Path(*f"{target}.{a.name}".split("."))).with_suffix(".py").exists():
                    out.add(f"{target}.{a.name}")
    return out


def allowed(name: str) -> bool:
    return name in ALLOWED or name == "explainer.modes" or name.startswith("explainer.modes.")


def test_modes_import_only_shared_tools():
    bad = {}
    for path in MODES.rglob("*.py"):
        wrong = sorted(m for m in imports_of(path) if not allowed(m))
        if wrong:
            bad[str(path.relative_to(SERVER))] = wrong
    assert not bad, f"modes/ must not import another mode's code: {bad}"


def test_shared_tools_import_only_shared_tools():
    """A shared tool may not pull in a mode either (that would load it under the modes' feet)."""
    bad = {}
    for path in SHARED_SOURCES:
        wrong = sorted(m for m in imports_of(path) if not allowed(m))
        if wrong:
            bad[str(path.relative_to(SERVER))] = wrong
    assert not bad, f"shared tools must not import a mode's code: {bad}"


ISOLATED = r'''
import importlib.abc, json, sys
ALLOWED = set(json.loads(sys.argv[1]))
RUN_EXPLORER = sys.argv[2] == "1"

class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.startswith("explainer") and not (name in ALLOWED or name == "explainer.modes" or name.startswith("explainer.modes.")):
            raise ImportError(f"blocked: {name} (another mode's code)")
        return None

sys.meta_path.insert(0, Block())
import explainer.jev
import modes_fakes as F
from explainer import modes
from explainer.modes.explorer import playground
explainer.jev.httpx.post = F.jev_post
playground.snap_widget = lambda html: None

base = {"mode": "concept", "text": "Explain Gini impurity in decision trees.", "interactivity": "balanced", "level": "auto"}
r = F.run(modes.generate, {**base, "quality": "lite-plus"})
assert r["out"]["spec"]["stages"], "Reader made no page"
if RUN_EXPLORER:
    r = F.run(modes.generate, {**base, "quality": "playground", "effort": "medium", "planThinking": True})
    assert any(b["type"] == "widget" for st in r["out"]["spec"]["stages"] for b in st["blocks"]), "Explorer made no playground"
# A v3 follow-up on each mode: /api/followup/plan's handler, then the run.
from explainer.core.validate import default_design
from explainer.followup import parse_follow_up
from explainer.modes.followup import entry
import followup_v3_data as D
for quality, page in (("lite-plus", {"kind": "revise", "outline": json.dumps(D.OLD)}), ("playground", D.FOLLOW)):
    if quality == "playground" and not RUN_EXPLORER:
        continue
    body = {"text": "make the tokens section clearer", "quality": quality, "followUp": {**page, "design": default_design(D.OLD)}}
    plan_req = entry.parse_plan_request(body)
    assert isinstance(plan_req, dict), plan_req
    plan = entry.handle(F.FakeClient(), D.Store(None), plan_req)
    assert plan["kind"] == "change", plan
    follow = parse_follow_up({**body["followUp"], "plan": {"sections": plan["sections"], "steps": plan["steps"]}})
    req = {"mode": "concept", "text": body["text"], "quality": quality, "extras": [], "followUp": follow}
    assert modes.handles(req), req
    out = modes.generate(F.FakeClient(), req, lambda e: None, jev_key="k", page_index=D.Store(None))
    assert "Clearer: " in json.dumps(out["spec"]), f"{quality}: the follow-up changed nothing"
loaded = sorted(m for m in sys.modules if m.startswith("explainer"))
print(json.dumps(loaded))
'''


def test_modes_run_with_every_other_mode_blocked():
    """A fresh Python where importing any other mode's module fails: both modes still load, make a page and change it."""
    node = shutil.which("node") is not None
    proc = subprocess.run([sys.executable, "-c", ISOLATED, json.dumps(sorted(ALLOWED)), "1" if node else "0"],
                          cwd=Path(__file__).parent, capture_output=True, text=True, timeout=300,
                          env={**__import__("os").environ, "FOLLOWUP_V5": "0",  # this run checks the v3 follow-up path
                               "PYTHONPATH": f"{SERVER}{__import__('os').pathsep}{Path(__file__).parent}"})
    assert proc.returncode == 0, proc.stderr[-3000:]
    loaded = json.loads(proc.stdout.strip().splitlines()[-1])
    assert all(allowed(m) for m in loaded), loaded


LOOKUPS = {"prompt": "prompt", "_prompt": "prompt", "formats": "formats", "constants": "constants", "menu": "menu", "limits": "limits"}


def lookups(path: Path) -> set[tuple[str, str]]:
    """(kind, name) for every literal lookup: data.prompt("X"), _prompt("X"), data.formats()["x"], data.constants()["X"], data.menu("X")."""
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            if name in ("prompt", "_prompt", "menu") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    out.add((LOOKUPS[name], arg.value))
                elif isinstance(arg, ast.IfExp):  # data.prompt("A" if x else "B")
                    out |= {(LOOKUPS[name], v.value) for v in (arg.body, arg.orelse) if isinstance(v, ast.Constant)}
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Call):
            f = node.value.func
            name = f.attr if isinstance(f, ast.Attribute) else ""
            if name in ("formats", "constants", "limits") and isinstance(node.slice, ast.Constant):
                out.add((LOOKUPS[name], node.slice.value))
    return out


def test_every_prompt_and_format_the_modes_use_exists():
    found = set()
    for path in [*MODES.rglob("*.py"), *SHARED_SOURCES]:
        found |= lookups(path)
    have = {"prompt": set(data._load("prompts")), "formats": set(data.formats()), "constants": set(data.constants()),
            "menu": set(data.menus()), "limits": set(data.limits())}
    missing = sorted(f"{k}:{n}" for k, n in found if n not in have[k])
    assert not missing, f"the modes need these, which no longer exist: {missing}"
    # the two planners and the playground builder are looked up, so this test really covers them
    assert {("prompt", "LITE_SYSTEM"), ("prompt", "OUTLINE_SYSTEM"), ("prompt", "PLAYGROUND_SYSTEM"), ("prompt", "PLAYGROUND_EDIT"),
            ("prompt", "UNDERSTAND_SYSTEM"), ("prompt", "INDEX_SYSTEM"), ("prompt", "SECTION_EDIT_NOTE"), ("prompt", "LITE_REVISE_NOTE"),
            ("prompt", "OUTLINE_CHART_SYSTEM"), ("prompt", "MAIN_FILE_SYSTEM"), ("prompt", "FOLLOWUP_V5_AGENT_SYSTEM"), ("prompt", "FOLLOWUP_V5_AGENT_LITE_SYSTEM"),
            ("prompt", "FOLLOWUP_V5_REVIEW_SYSTEM"), ("prompt", "FOLLOWUP_V5_REPLY_SYSTEM")} <= found


# ---------- follow-ups v3 and v4 stay apart, so v3/ can be deleted without touching v4 ----------

FOLLOWUP = MODES / "followup"
V3, V4, V5 = "explainer.modes.followup.v3", "explainer.modes.followup.v4", "explainer.modes.followup.v5"
# The only files that may know both versions: they pick one, importing v3 only inside its branch.
DISPATCH = {"entry.py", "run.py", "__init__.py"}


def followup_imports(path: Path) -> set[str]:
    """imports_of, plus packages named in "from x import pkg" (imports_of only counts .py modules)."""
    out = imports_of(path)
    mod = module_of(path)
    package = mod if path.name == "__init__.py" else mod.rsplit(".", 1)[0]
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.level:
            base = package.split(".")[:len(package.split(".")) - (node.level - 1)]
            target = ".".join(base + ([node.module] if node.module else []))
            out |= {f"{target}.{a.name}" for a in node.names if (PKG.parent / Path(*f"{target}.{a.name}".split("."))).is_dir()}
    return out


def _uses(path: Path, version: str) -> list[str]:
    return sorted(m for m in followup_imports(path) if m == version or m.startswith(version + "."))


def test_followup_versions_import_only_shared_follow_up_tools():
    bad = {}
    for path in (FOLLOWUP / "v4").rglob("*.py"):
        if _uses(path, V3):
            bad[str(path.relative_to(SERVER))] = _uses(path, V3)
    for path in (FOLLOWUP / "v3").rglob("*.py"):
        if _uses(path, V4):
            bad[str(path.relative_to(SERVER))] = _uses(path, V4)
    for path in FOLLOWUP.glob("*.py"):  # the shared follow-up tools (shared.py, check.py, playground_edit.py, ...)
        if path.name not in DISPATCH and (_uses(path, V3) or _uses(path, V4)):
            bad[str(path.relative_to(SERVER))] = _uses(path, V3) + _uses(path, V4)
    for path in (FOLLOWUP / "v5").rglob("*.py"):  # v5 stands alone: deleting v3/ or v4/ never touches it
        if _uses(path, V3) or _uses(path, V4):
            bad[str(path.relative_to(SERVER))] = _uses(path, V3) + _uses(path, V4)
    for path in [*(FOLLOWUP / "v3").rglob("*.py"), *(FOLLOWUP / "v4").rglob("*.py")]:
        if _uses(path, V5):
            bad[str(path.relative_to(SERVER))] = _uses(path, V5)
    for path in FOLLOWUP.glob("*.py"):
        if path.name not in DISPATCH and _uses(path, V5):
            bad[str(path.relative_to(SERVER))] = _uses(path, V5)
    assert not bad, f"no follow-up version may import another (nor a shared tool any of them), so each can be deleted alone: {bad}"


def test_v3_is_imported_only_by_the_dispatchers():
    """Outside followup/v3/ itself, only entry.py and run.py name v3 (each inside its v3 branch)."""
    users = set()
    for path in PKG.rglob("*.py"):
        if FOLLOWUP / "v3" in path.parents:
            continue
        if _uses(path, V3):
            users.add(str(path.relative_to(FOLLOWUP)) if FOLLOWUP in path.parents else str(path.relative_to(SERVER)))
    assert users <= {"entry.py", "run.py"}, users


def test_v4_and_v5_are_imported_only_by_the_dispatchers():
    """Outside their own folders, only entry.py and run.py name v4 or v5 (each inside its branch)."""
    for version in (V4, V5):
        folder = FOLLOWUP / version.rsplit(".", 1)[-1]
        users = set()
        for path in PKG.rglob("*.py"):
            if folder in path.parents:
                continue
            if _uses(path, version):
                users.add(str(path.relative_to(FOLLOWUP)) if FOLLOWUP in path.parents else str(path.relative_to(SERVER)))
        assert users <= {"entry.py", "run.py"}, (version, users)


V4_WITHOUT_V3 = r'''
import importlib.abc, sys

class NoV3(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "explainer.modes.followup.v3" or name.startswith("explainer.modes.followup.v3."):
            raise ImportError(f"blocked: {name} (follow-ups v3 is treated as deleted)")
        return None

sys.meta_path.insert(0, NoV3())
import pytest
code = pytest.main(["test_followup_v4.py", "-q", "-p", "no:cacheprovider"])
loaded = [m for m in sys.modules if m.startswith("explainer.modes.followup.v3")]
assert not loaded, loaded
sys.exit(int(code))
'''


def test_v4_runs_with_v3_deleted():
    """The whole v4 test suite (talk, editor, tools, reply, through run.py and the plan route) in a fresh Python where any
    import of followup/v3 fails: proof that deleting v3/ doesn't break v4."""
    proc = subprocess.run([sys.executable, "-c", V4_WITHOUT_V3], cwd=Path(__file__).parent, capture_output=True, text=True, timeout=300,
                          env={**__import__("os").environ, "PYTHONPATH": f"{SERVER}{__import__('os').pathsep}{Path(__file__).parent}"})
    assert proc.returncode == 0, (proc.stdout[-3000:], proc.stderr[-3000:])


V5_WITHOUT_V3_V4 = r"""
import importlib.abc, sys

class NoOld(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        for old in ("explainer.modes.followup.v3", "explainer.modes.followup.v4"):
            if name == old or name.startswith(old + "."):
                raise ImportError(f"blocked: {name} (follow-ups v3 and v4 are treated as deleted)")
        return None

sys.meta_path.insert(0, NoOld())
import pytest
code = pytest.main(["test_followup_v5.py", "-q", "-p", "no:cacheprovider"])
loaded = [m for m in sys.modules if m.startswith(("explainer.modes.followup.v3", "explainer.modes.followup.v4"))]
assert not loaded, loaded
sys.exit(int(code))
"""


def test_v5_runs_with_v3_and_v4_deleted():
    """The whole v5 test suite (talk, editor, tools, review, the base page, the plan route) in a fresh Python where any
    import of followup/v3 or followup/v4 fails: proof that deleting either doesn't break v5."""
    proc = subprocess.run([sys.executable, "-c", V5_WITHOUT_V3_V4], cwd=Path(__file__).parent, capture_output=True, text=True, timeout=300,
                          env={**__import__("os").environ, "PYTHONPATH": f"{SERVER}{__import__('os').pathsep}{Path(__file__).parent}"})
    assert proc.returncode == 0, (proc.stdout[-3000:], proc.stderr[-3000:])
