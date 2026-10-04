#!/usr/bin/env python3
"""
Shadow Rail — code audit.

Finds, in both stacks, the three things that quietly rot a codebase:

  dead     — imports, locals and module-level names nothing uses
  orphan   — files nothing imports, exports nothing references
  doubled  — duplicated function bodies and copy-pasted blocks

Backend dead imports/locals are read from pyflakes when it is available;
everything else is derived from the source itself, so the audit needs no
service running.

    python scripts/audit.py            # report
    python scripts/audit.py --strict   # non-zero exit when anything is found
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "dist", "data", "web", ".venv"}
SKIP_SUFFIX = {".min.js"}

# names that exist for the framework / runtime, never referenced in our source
RUNTIME_ALLOW = {
    "main", "app", "router", "handler", "configure", "setup", "run",
    "lifespan", "startup", "shutdown", "SCHEMA", "DB_PATH", "STORE",
    "REGISTRY", "ENGINE", "GOVERNOR", "MODEL", "JOURNAL", "BUS",
    "REPO_DIR", "BACKEND_DIR", "DATA_DIR", "CONFIG_PATH", "KEY_PATH",
    "WEB_DIR", "INDEX_HTML", "page", "children",
}


def sources(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    out = []
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix not in suffixes:
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if any(p.name.endswith(s) for s in SKIP_SUFFIX):
            continue
        out.append(p)
    return sorted(out)


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


# ───────────────────────────── pyflakes ────────────────────────────────────

def pyflakes_findings() -> list[str]:
    try:
        r = subprocess.run([sys.executable, "-m", "pyflakes", str(BACKEND)],
                           capture_output=True, text=True, timeout=180)
    except Exception as exc:                                   # pragma: no cover
        return [f"! pyflakes unavailable: {exc}"]
    return [ln for ln in (r.stdout + r.stderr).splitlines() if ln.strip()]


# ───────────────────── python: unused module-level names ───────────────────

def python_top_level(p: Path) -> list[tuple[str, int]]:
    """Public top-level defs/assignments that nothing outside this file uses."""
    tree = ast.parse(read(p))
    names: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # pytest collects test_* / fixtures by name — that is not dead code
            if node.name.startswith("test_") or p.name == "conftest.py":
                continue
            # a decorated function is registered with a framework (FastAPI routes,
            # websocket handlers, middleware) and is called by it, not by us
            if getattr(node, "decorator_list", None):
                continue
            names.append((node.name, node.lineno))
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.append((t.id, node.lineno))
    return names


def unused_module_names(repo_text: str) -> list[str]:
    skip = {"if", "for", "while", "True", "False", "None"}
    out = []
    for p in sources(BACKEND, (".py",)):
        if p.name == "__init__.py" or p.parent.name == "tests":
            continue
        for name, line in python_top_level(p):
            if name.startswith("_") or name in RUNTIME_ALLOW or name in skip:
                continue
            if len(name) < 3:
                continue
            uses = len(re.findall(rf"\b{re.escape(name)}\b", repo_text))
            if uses <= 1:                       # only its own definition
                out.append(f"{p.relative_to(ROOT)}:{line}: '{name}' defined but never used")
    return out


# ─────────────────────── duplicates (python bodies) ────────────────────────

def normalize(text: str) -> str:
    """Source with identifiers and literals blanked — catches copy-paste."""
    text = re.sub(r"#.*", "", text)
    text = re.sub(r'"""[\s\S]*?"""', 'S', text)
    text = re.sub(r'"[^"\n]*"|\'[^\'\n]*\'', "S", text)
    text = re.sub(r"\b\d+(\.\d+)?\b", "N", text)
    text = re.sub(r"\b\w+\b", "I", text)
    return re.sub(r"\s+", " ", text).strip()


def python_duplicate_bodies() -> list[str]:
    seen: dict[str, list[str]] = defaultdict(list)
    for p in sources(BACKEND, (".py",)):
        tree = ast.parse(read(p))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = ast.get_source_segment(read(p), node) or ""
                if body.count("\n") < 8:                   # ignore small helpers
                    continue
                key = hashlib.sha1(normalize(body).encode()).hexdigest()[:12]
                seen[key].append(f"{p.relative_to(ROOT)}:{node.lineno}:{node.name}")
    return [f"duplicate function body: {', '.join(v)}"
            for v in seen.values() if len(v) > 1]


def normalize_tokens(text: str) -> str:
    """Comments and literals blanked, identifiers *kept* — a genuine copy-paste
    still matches, while two unrelated field lists no longer collide."""
    text = re.sub(r"#.*", "", text)
    text = re.sub(r'"""[\s\S]*?"""', "S", text)
    text = re.sub(r'"[^"\n]*"', "S", text)
    text = re.sub(r"'[^'\n]*'", "S", text)
    text = re.sub(r"\b\d+(\.\d+)?\b", "N", text)
    return re.sub(r"\s+", " ", text).strip()


def preamble_trimmed(p: Path) -> list[str]:
    """Source lines with the module docstring / import block removed.

    Two files of the same family legitimately start with the same header, so a
    shared preamble is not copy-paste — the body is what we want to compare.
    """
    lines = read(p).splitlines()
    if p.suffix == ".py":
        try:
            tree = ast.parse("\n".join(lines))
        except SyntaxError:
            return lines
        end = 0
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                end = max(end, node.end_lineno or node.lineno)
            elif (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                  and isinstance(node.value.value, str) and node.lineno <= end + 1):
                end = max(end, node.end_lineno or node.lineno)
            elif end:                       # first real statement after the imports
                break
            else:
                break
        return lines[end:]
    # TS/TSX: drop leading comments and complete import statements
    i, in_import = 0, False
    for raw in lines:
        s = raw.strip()
        if in_import:
            i += 1
            if s.startswith("from ") or s.endswith(";"):
                in_import = False
            continue
        if not s or s.startswith(("//", "/*", "*")):
            i += 1
            continue
        if s.startswith("import ") or s == "import {":
            i += 1
            in_import = s.rstrip(";").endswith(("{", ",")) or " from " not in s
            if s.endswith(";"):
                in_import = False
            continue
        break
    return lines[i:]


def duplicate_blocks(files: list[Path], window: int = 14) -> list[str]:
    """Copy-pasted runs of lines, ignoring blank/comment/brace-only lines."""
    seen: dict[str, list[str]] = defaultdict(list)
    for p in files:
        lines = [ln.strip() for ln in preamble_trimmed(p)]
        keep = [ln for ln in lines if ln and not ln.startswith(("//", "#", "*", "/*"))]
        for i in range(len(keep) - window):
            chunk = keep[i:i + window]
            if sum(len(c) for c in chunk) < 220:            # too trivial
                continue
            # a copy-pasted function usually only differs in its own name —
            # drop declaration lines so the clone still matches
            body = [c for c in chunk if not re.match(
                r"^(async\s+)?def\s|^(export\s+)?(default\s+)?(async\s+)?function\s|^class\s",
                c)]
            key = hashlib.sha1(normalize_tokens("\n".join(body)).encode()).hexdigest()[:14]
            seen[key].append(f"{p.relative_to(ROOT)}")
    groups: dict[tuple[str, ...], int] = defaultdict(int)
    for where in seen.values():
        uniq = tuple(sorted(set(where)))
        if len(uniq) > 1:
            groups[uniq] += 1
    return [f"duplicated {window}-line block x{n} shared by: {', '.join(f)}"
            for f, n in sorted(groups.items(), key=lambda kv: -kv[1])]


# ───────────────────────── orphan files + exports ──────────────────────────

def python_orphans(repo_text: str) -> list[str]:
    out = []
    for p in sources(BACKEND, (".py",)):
        # main.py is the entry point and tests are discovered by pytest
        if p.name in {"__init__.py", "main.py"} or p.parent.name == "tests":
            continue
        module = p.stem
        if not re.search(rf"\b{re.escape(module)}\b", repo_text.replace(p.name, "")):
            out.append(f"{p.relative_to(ROOT)}: module is never imported")
    return out


def frontend_orphans(repo_text: str) -> list[str]:
    """Files under src/ that no other file imports, plus exports nothing uses."""
    out = []
    files = sources(FRONTEND / "src", (".ts", ".tsx"))
    for p in files:
        if p.name in {"main.tsx", "App.tsx", "vite-env.d.ts"} or p.name.endswith(".d.ts"):
            continue
        stem = p.stem
        if not re.search(rf"['\"][^'\"]*{re.escape(stem)}['\"]", repo_text):
            out.append(f"{p.relative_to(ROOT)}: file is never imported")

    export_re = re.compile(
        r"^export\s+(?:default\s+)?(?:const|function|class|interface|type|enum)\s+(\w+)",
        re.M)
    for p in files:
        src = read(p)
        for name in export_re.findall(src):
            if name in RUNTIME_ALLOW or name.startswith("_"):
                continue
            if len(name) < 3:
                continue
            uses = len(re.findall(rf"\b{re.escape(name)}\b", repo_text))
            if uses <= 1:
                out.append(f"{p.relative_to(ROOT)}: export '{name}' is never used")
    return out


# ─────────────────────────── leftover markers ──────────────────────────────

MARKERS = ("TODO", "FIXME", "XXX", "HACK", "console.log", "print(", "debugger",
           "eslint-disable", "type: ignore", "pytest.mark.skip")


def leftovers(files: list[Path]) -> list[str]:
    out = []
    for p in files:
        in_main = False
        for i, line in enumerate(read(p).splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith('if __name__ == "__main__"'):
                in_main = True
            if stripped.startswith(("#", "//", "*")):
                continue
            for marker in MARKERS:
                if marker not in line:
                    continue
                # an intentional CLI entry point, or a diagnostic print inside a
                # test, is not a leftover — everything else is
                if marker == "print(" and (in_main or p.parent.name == "tests"):
                    continue
                out.append(f"{p.relative_to(ROOT)}:{i}: {marker} → {stripped[:90]}")
                break
    return out


# ──────────────────────────────── report ───────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero when anything is reported")
    args = ap.parse_args()

    py_files = sources(BACKEND, (".py",))
    fe_files = sources(FRONTEND / "src", (".ts", ".tsx"))
    repo_text = "\n".join(read(p) for p in py_files + fe_files) + read(ROOT / "README.md")

    sections = [
        ("backend: dead imports / locals (pyflakes)", pyflakes_findings()),
        ("backend: module-level names nothing uses", unused_module_names(repo_text)),
        ("backend: orphan modules", python_orphans(repo_text)),
        ("frontend: orphan files / unused exports", frontend_orphans(repo_text)),
        ("duplicated python function bodies", python_duplicate_bodies()),
        ("duplicated 14-line blocks", duplicate_blocks(py_files + fe_files)),
        ("leftover markers", leftovers(py_files + fe_files)),
    ]

    total = 0
    for title, rows in sections:
        rows = [r for r in rows if r.strip()]
        total += len(rows)
        print(f"\n── {title} ({len(rows)})")
        for row in rows[:60]:
            print(f"   {row}")
        if len(rows) > 60:
            print(f"   … {len(rows) - 60} more")

    print(f"\n{'=' * 62}\nAUDIT: {total} finding(s)")
    return 1 if (args.strict and total) else 0


if __name__ == "__main__":
    raise SystemExit(main())
