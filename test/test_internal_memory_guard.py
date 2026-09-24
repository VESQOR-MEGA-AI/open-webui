#!/usr/bin/env python3
"""VQA-305-T2 — negative-controlled tests for scripts/check_internal_memory.py

WHAT MAKES THIS A TEST AND NOT AN ASSERTION
-------------------------------------------
A guard that always passes is indistinguishable from no guard. Every positive
case below is paired with a NEGATIVE CONTROL that must go RED: the test builds a
mutated copy of the guard with the enforcement removed and requires it to accept
the very file the real guard rejects. If a mutation fails to produce a RED
result, the suite fails — so a silently broken guard cannot ship as "green".

Runs with the standard library only (`python3 test/test_internal_memory_guard.py`)
so CI needs no setup and it costs seconds.

Exit 0 = all cases pass. Exit 1 = at least one case failed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
GUARD = REPO / "scripts" / "check_internal_memory.py"

# The real material that leaked into this public repository (VQA-305-T1).
LEAKED = [
    "memory/PROFILE.md",
    "memory/FACTS.md",
    "memory/DECISIONS.md",
    "memory/weeks/2026-W37/CHRONICLE.md",
    "memory/repo-wiki/module-answer-compare.md",
]
# Paths that look like the forbidden prefixes but are NOT under them.
SAFE = [
    "src/app.html",
    "test/test_vesqor_signup_gates.py",
    "docs/git-memory-management.md",
    "backend/open_webui/memory_utils.py",
]

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    results.append((ok, label))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail and not ok else ""))


def run(args: list[str], cwd: Path) -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(cwd / "scripts" / "check_internal_memory.py"), *args],
                       capture_output=True, text=True, cwd=cwd)
    return p.returncode, (p.stdout + p.stderr)


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd)


def fresh_repo(tmp: Path, guard_src: Path) -> Path:
    """A throwaway git repo carrying a copy of the guard under test."""
    repo = tmp / "sandbox"
    (repo / "scripts").mkdir(parents=True)
    (repo / "src").mkdir(parents=True)
    shutil.copy(guard_src, repo / "scripts" / "check_internal_memory.py")
    (repo / "src" / "app.html").write_text("<html></html>\n")
    (repo / ".gitignore").write_text("memory/\n")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@vesqor.local")
    git(repo, "config", "user.name", "VQA-305-T2 harness")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    return repo


def stage_leak(repo: Path, path: str) -> None:
    """Force-add the material, exactly how it got in the first time."""
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("# internal working material\n")
    # -f defeats .gitignore: this is the bypass the real guard must survive.
    git(repo, "add", "-f", path)


def main() -> int:
    if not GUARD.exists():
        print(f"FATAL: guard not found at {GUARD}")
        return 1

    with tempfile.TemporaryDirectory(prefix="vqa305t2-") as td:
        tmp = Path(td)

        # ---------------------------------------------------------------- P1
        print("\nP1. clean repository must pass (exit 0)")
        repo = fresh_repo(tmp / "p1", GUARD)
        rc, out = run([], repo)
        check(rc == 0, "clean tree -> exit 0", f"rc={rc} out={out[:120]}")
        check("no internal working material" in out, "clean tree prints OK")

        # ---------------------------------------------------------------- P2
        print("\nP2. each leaked path must be REJECTED (exit 1)")
        for i, path in enumerate(LEAKED):
            r = fresh_repo(tmp / f"p2_{i}", GUARD)
            stage_leak(r, path)
            rc, out = run([], r)
            ok = rc == 1 and path in out
            check(ok, f"rejects {path}", f"rc={rc}")

        # ------------------------------------------------------------ NEG-CTL
        print("\nNEG-CTL. the controls themselves must be able to go RED")
        # Mutate the guard: remove ONE prefix from the enforcement list.
        mutant = tmp / "mutant_prefix.py"
        src = GUARD.read_text().replace('"memory/",\n', "")
        assert src != GUARD.read_text(), "mutation had no effect - control is void"
        mutant.write_text(src)
        r = fresh_repo(tmp / "nc1", mutant)
        stage_leak(r, "memory/PROFILE.md")
        rc, out = run([], r)
        check(rc == 0, "mutant (memory/ prefix removed) ACCEPTS the leak -> control is live",
              f"rc={rc} (expected 0; guard has teeth)")

        # Mutate harder: make is_forbidden always return False.
        mutant2 = tmp / "mutant_always.py"
        src2 = GUARD.read_text().replace(
            "    return any(\n        norm == p.rstrip(\"/\") or norm.startswith(p) for p in FORBIDDEN_PREFIXES\n    )",
            "    return False")
        assert src2 != GUARD.read_text(), "mutation 2 had no effect - control is void"
        mutant2.write_text(src2)
        r = fresh_repo(tmp / "nc2", mutant2)
        stage_leak(r, "memory/DECISIONS.md")
        rc, out = run([], r)
        check(rc == 0, "mutant (enforcement disabled) ACCEPTS the leak -> control is live",
              f"rc={rc} (expected 0)")

        # ---------------------------------------------------------------- P3
        print("\nP3. lookalike paths must NOT be rejected (no false positives)")
        r = fresh_repo(tmp / "p3", GUARD)
        for p in SAFE:
            f = r / p
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("product code\n")
        git(r, "add", "-A")
        rc, out = run([], r)
        check(rc == 0, "product paths under lookalike names -> exit 0", f"rc={rc} out={out[:200]}")

        # ---------------------------------------------------------------- P4
        print("\nP4. a git failure must NOT read as clean (exit 2)")
        r = fresh_repo(tmp / "p4", GUARD)
        # Break git for the child by pointing it at a bogus dir as cwd-relative
        # repo: simplest reliable breakage is an unusable GIT_DIR.
        p = subprocess.run([sys.executable, str(r / "scripts" / "check_internal_memory.py")],
                           capture_output=True, text=True, cwd=r,
                           env={**os.environ, "GIT_DIR": str(tmp / "nonexistent-gitdir")})
        check(p.returncode == 2, "broken git -> exit 2, never 0",
              f"rc={p.returncode} out={(p.stdout+p.stderr)[:150]}")

        # ---------------------------------------------------------------- P5
        print("\nP5. --staged-only catches a staged leak")
        r = fresh_repo(tmp / "p5", GUARD)
        stage_leak(r, "memory/INSIGHTS.md")
        rc, out = run(["--staged-only"], r)
        check(rc == 1, "--staged-only rejects staged leak", f"rc={rc}")

        # ---------------------------------------------------------------- P6
        print("\nP6. --history is advisory (exit 3) and finds the committed leak")
        r = fresh_repo(tmp / "p6", GUARD)
        stage_leak(r, "memory/PROFILE.md")
        git(r, "commit", "-q", "-m", "leak")
        # remove from the tree so tree mode is clean, history is not
        git(r, "rm", "-q", "--cached", "memory/PROFILE.md")
        git(r, "commit", "-q", "-m", "untrack (the 0ba20ad non-fix)")
        rc_tree, _ = run([], r)
        check(rc_tree == 0, "tree mode clean after untracking (the original false comfort)")
        rc, out = run(["--history"], r)
        check(rc == 3, "--history exits 3 (advisory, not a gate)", f"rc={rc}")
        check("PRE-EXISTING exposure" in out, "--history labels it pre-existing")

        # ---------------------------------------------------------------- P7
        print("\nP7. --json is machine-readable")
        r = fresh_repo(tmp / "p7", GUARD)
        stage_leak(r, "memory/FACTS.md")
        rc, out = run(["--json"], r)
        import json as _json
        try:
            doc = _json.loads(out)
            check(rc == 1 and doc["clean"] is False and "memory/FACTS.md" in doc["offenders"],
                  "--json reports the offender", out[:150])
        except Exception as exc:  # noqa: BLE001
            check(False, "--json is valid JSON", str(exc))

        # ---------------------------------------------------------------- P8
        print("\nP8. the REAL repository tree is clean (the actual state, measured)")
        rc, out = run([], REPO)
        check(rc == 0, "real /root/open-webui tree -> exit 0", f"rc={rc} out={out[:200]}")

    passed = sum(1 for ok, _ in results if ok)
    total = len(results)
    print(f"\n{'=' * 68}")
    print(f"RESULT {passed}/{total} passed")
    if passed != total:
        for ok, label in results:
            if not ok:
                print(f"  FAILED: {label}")
        return 1
    print("ALL CASES PASS (positive cases + live negative controls)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
