#!/usr/bin/env python3
"""VQA-305-T2 — reject internal agent working material in this PUBLIC repository.

WHY THIS EXISTS
---------------
`memory/` (project profile, technical facts, decision records, weekly work
reports) was briefly tracked in this public repository. Untracking it in
`0ba20ad` did NOT remove it: the blobs stay in history and are still served
anonymously by the raw-content API and by a third-party CDN. See VQA-305-T1's
exposure report for the measured surface.

Untracking is not a guard either. `.gitignore` carries a `memory/` rule, but a
rule is not enforcement: `git add -f memory/PROFILE.md` succeeds today. That
flag is exactly how the material got in the first time. Nothing in CI today
stops the next agent session from re-adding it.

This script is that missing enforcement. It is deliberately FAIL-CLOSED and
independent of `.gitignore`, because `.gitignore` is advisory and this must be
authoritative.

MODES
-----
  (default)        tree mode  — the index/tracked tree of the current ref.
                   Exit 0 clean, 1 material present. THIS IS THE CI GATE.
  --staged-only    index mode — only staged files (fast pre-commit use).
  --history        history mode — every reachable commit in every ref.
                   Exit 0 clean, 3 material found in history.
                   This is an ADVISORY report, not a gate: the pre-existing
                   history exposure is a known owner decision (rewrite vs
                   recorded acceptance). It must not block unrelated work.
  --json           machine-readable output on stdout.

Exit codes: 0 = clean, 1 = material in the tree (gate failure),
            3 = material in history (advisory), 2 = the guard could not run.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

# Any path under these prefixes is internal working material, never product.
FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "memory/",
    "repo-wiki/",
    ".agent-memory/",
    "weeks/",
)

# Paths that are allowed even though they sit under a forbidden prefix.
# Keep empty unless a real PRODUCT path needs an entry: an allow-list is a
# hole by construction.
ALLOWED_EXACT: frozenset[str] = frozenset()

GATE_FAILED = 1
GUARD_ERROR = 2
HISTORY_FOUND = 3


def _git(args: list[str]) -> tuple[int, str]:
    """Run git in the CURRENT repository. Never raises on a non-zero exit."""
    try:
        p = subprocess.run(
            ["git", *args], capture_output=True, text=True, check=False
        )
    except OSError as exc:  # git missing entirely
        print(f"GUARD ERROR: cannot execute git: {exc}", file=sys.stderr)
        return GUARD_ERROR, ""
    return p.returncode, p.stdout


def is_forbidden(path: str) -> bool:
    norm = path.strip().lstrip("./")
    if not norm:
        return False
    if norm in ALLOWED_EXACT:
        return False
    return any(
        norm == p.rstrip("/") or norm.startswith(p) for p in FORBIDDEN_PREFIXES
    )


def tracked_files(staged_only: bool) -> list[str] | None:
    """Return tracked paths, or None if git failed (never silently 'clean')."""
    if staged_only:
        rc, out = _git(["diff", "--cached", "--name-only", "--diff-filter=ACMR"])
    else:
        rc, out = _git(["ls-files"])
    if rc != 0:
        return None
    return [line for line in out.splitlines() if line.strip()]


def history_paths() -> list[dict[str, str]] | None:
    """Commits, across ALL refs, that touch a forbidden path."""
    # --all covers every ref the clone knows. --no-renames keeps it simple.
    rc, out = _git(
        ["log", "--all", "--no-renames", "--name-only", "--pretty=format:%H"]
    )
    if rc != 0:
        return None
    findings: list[dict[str, str]] = []
    commit = ""
    for line in out.splitlines():
        s = line.strip()
        if not s:
            continue
        if len(s) == 40 and all(c in "0123456789abcdef" for c in s):
            commit = s
            continue
        if is_forbidden(s):
            findings.append({"commit": commit, "path": s})
    return findings


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def _render_tree(offenders: list[str], total: int, as_json: bool) -> int:
    if as_json:
        print(json.dumps(
            {"mode": "tree", "clean": not offenders, "checked": total,
             "offenders": offenders}, indent=1))
    if offenders:
        if not as_json:
            print("REFUSED: internal agent working material is tracked in this "
                  "PUBLIC repository.")
            print("These paths hold internal context (architecture, decisions,")
            print("file:line detail) and must never be public:")
            for f in offenders:
                print(f"  - {f}")
            print()
            print("Remove them from the index:")
            roots = sorted({o.split("/")[0] + "/" for o in offenders})
            print("  git rm --cached -r " + " ".join(roots))
            print()
            print("This material belongs outside a public repository. The")
            print(".gitignore rule does not stop `git add -f`, which is how it")
            print("got in the first time.")
        return GATE_FAILED
    if not as_json:
        print(f"OK: no internal working material tracked ({total} files checked).")
    return 0


def _render_history(findings: list[dict[str, str]], as_json: bool) -> int:
    if as_json:
        print(json.dumps(
            {"mode": "history", "clean": not findings, "findings": findings},
            indent=1))
    if not findings:
        if not as_json:
            print("OK: no commit in any reachable ref touches internal "
                  "working material.")
        return 0
    if not as_json:
        commits = sorted({f["commit"][:10] for f in findings})
        print("ADVISORY: internal working material is reachable from history "
              "in this PUBLIC repository.")
        print(f"  offending paths : {len(findings)}")
        print(f"  distinct commits: {len(commits)} -> {', '.join(commits)}")
        print()
        print("This is the PRE-EXISTING exposure (VQA-305), not a new")
        print("regression. It cannot be closed from CI: it needs an owner")
        print("decision on history rewrite vs. recorded risk acceptance.")
        print("Advisory exit code 3 so it never blocks unrelated work.")
    return HISTORY_FOUND


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--staged-only", action="store_true",
                    help="check only staged files (pre-commit)")
    ap.add_argument("--history", action="store_true",
                    help="advisory scan of every reachable ref (exit 3)")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ns = ap.parse_args(argv)

    if ns.history:
        findings = history_paths()
        if findings is None:
            print("GUARD ERROR: git log failed", file=sys.stderr)
            return GUARD_ERROR
        return _render_history(findings, ns.as_json)

    files = tracked_files(ns.staged_only)
    if files is None:
        # A git failure must NEVER read as "clean".
        print("GUARD ERROR: git failed; refusing to report clean", file=sys.stderr)
        return GUARD_ERROR
    offenders = sorted({f for f in files if is_forbidden(f)})
    return _render_tree(offenders, len(files), ns.as_json)


if __name__ == "__main__":
    raise SystemExit(main())
