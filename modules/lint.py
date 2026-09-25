"""Offline skill/plugin quality linter.

Catches the 5 most common failure classes in agent skill bundles,
deterministically, with zero network access:

  SK001 MISSING_METADATA  — no SKILL.md/manifest with name+description
  SK002 VAGUE_DESCRIPTION — description too short/generic to be retrievable
  SK003 DANGLING_REFERENCES — files/commands referenced in SKILL.md absent
                            from the bundle (the classic broken-publish bug)
  SK004 NO_EXAMPLES       — no example inputs/outputs anywhere in the bundle
  SK005 CONTEXT_BLOAT     — SKILL.md body exceeds a token budget, or the
                            bundle ships files the skill never references

Input: {"files": {"SKILL.md": "...", "scripts/x.py": "..."}} or a dict of
path->content directly. Output: {"status": "ok"|"findings", "findings": [...]}
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

MAX_BODY_TOKENS = 2000          # SKILL.md body budget (~4 chars/token)
MIN_DESC_WORDS = 8              # a usable description needs at least this
GENERIC_WORDS = {"skill", "tool", "helper", "assistant", "utility", "various",
                 "stuff", "things", "etc", "agent", "task", "tasks"}

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
REF_RE = re.compile(r"`([^`\s]+\.[A-Za-z0-9]{1,6})`|\[([^\]]+)\]\(([^)\s]+\.[A-Za-z0-9]{1,6})\)")

FINDING_CODES = ("SK001", "SK002", "SK003", "SK004", "SK005")


def _parse_frontmatter(text: str) -> Dict[str, str]:
    """Parse simple YAML frontmatter key: value pairs (no nested structures)."""
    m = FRONTMATTER_RE.match(text)
    meta: Dict[str, str] = {}
    if not m:
        return meta
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip().strip("'\"")
    return meta


def _body(text: str) -> str:
    return FRONTMATTER_RE.sub("", text)


def _tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4) if text else 0


def _is_generic(description: str) -> bool:
    words = [w.strip(".,!?:;").lower() for w in description.split()]
    meaningful = [w for w in words if w not in GENERIC_WORDS]
    return len(meaningful) < MIN_DESC_WORDS // 2 or len(words) < MIN_DESC_WORDS


def _referenced_files(body: str) -> List[str]:
    refs: List[str] = []
    for m in REF_RE.finditer(body):
        ref = m.group(1) or m.group(3)
        if ref and not ref.startswith(("http://", "https://")):
            refs.append(ref.lstrip("./"))
    return refs


def lint_skill(files: Dict[str, str]) -> List[Dict[str, Any]]:
    """Lint a skill bundle (path->content). Returns findings list."""
    findings: List[Dict[str, Any]] = []
    norm = {p.lstrip("./"): c for p, c in files.items()}
    skill_md = norm.get("SKILL.md") or next(
        (c for p, c in norm.items() if p.lower().endswith("skill.md")), None)

    # SK001 — metadata present with name+description
    meta = _parse_frontmatter(skill_md) if skill_md else {}
    if not skill_md or not meta.get("name") or not meta.get("description"):
        findings.append({
            "code": "SK001", "severity": "error",
            "message": "missing SKILL.md frontmatter with 'name' and 'description'",
            "fix": "add YAML frontmatter with name and a specific description",
        })
        meta = meta or {}

    # SK002 — description usable for retrieval
    desc = meta.get("description", "")
    if desc and _is_generic(desc):
        findings.append({
            "code": "SK002", "severity": "warn",
            "message": f"description too vague for retrieval: {desc[:60]!r}",
            "fix": "say WHAT it does, on WHAT inputs, e.g. 'extracts tables from PDF invoices into CSV'",
        })

    # SK003 — referenced files exist in the bundle
    if skill_md:
        body = _body(skill_md)
        for ref in _referenced_files(body):
            if ref not in norm:
                findings.append({
                    "code": "SK003", "severity": "error",
                    "message": f"SKILL.md references '{ref}' but it is not in the bundle",
                    "fix": f"ship {ref} or drop the reference",
                })

    # SK004 — at least one example somewhere
    has_example = any(
        re.search(r"example|input:|output:", c, re.IGNORECASE)
        or "examples" in p.lower()
        for p, c in norm.items())
    if not has_example:
        findings.append({
            "code": "SK004", "severity": "warn",
            "message": "no example input/output anywhere in the bundle",
            "fix": "add an Examples section to SKILL.md",
        })

    # SK005 — context bloat: oversized body or unreferenced payload files
    if skill_md:
        tok = _tokens(_body(skill_md))
        if tok > MAX_BODY_TOKENS:
            findings.append({
                "code": "SK005", "severity": "warn",
                "message": f"SKILL.md body ~{tok} tokens exceeds {MAX_BODY_TOKENS}-token budget",
                "fix": "move detail into referenced reference files; keep SKILL.md a router",
            })
    referenced = set(_referenced_files(_body(skill_md))) if skill_md else set()
    orphans = [p for p in norm
               if p != "SKILL.md" and not p.lower().endswith("skill.md")
               and p not in referenced and p.endswith((".md", ".txt", ".json"))]
    if orphans:
        findings.append({
            "code": "SK005", "severity": "info",
            "message": f"bundle ships unreferenced doc files: {', '.join(sorted(orphans)[:5])}",
            "fix": "reference them from SKILL.md or drop them (agents pay for every byte)",
        })

    return findings


def run(data: Any = None) -> Dict[str, Any]:
    """Entrypoint for ship.py. Accepts {'files': {...}} or a bare {path: content}."""
    if isinstance(data, dict) and isinstance(data.get("files"), dict):
        files = data["files"]
    elif isinstance(data, dict) and data:
        files = data
    else:
        return {"status": "error", "message": "expected {'files': {path: content}}"}
    findings = lint_skill(files)
    return {
        "status": "findings" if any(f["severity"] == "error" for f in findings)
        else ("ok" if not findings else "findings"),
        "checked": len(files),
        "findings": findings,
        "codes": FINDING_CODES,
    }
