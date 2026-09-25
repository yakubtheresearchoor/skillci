"""Skill packager for the anthropics/skills format.

Bundles a skill directory (SKILL.md + assets) into a versioned, checksummed
.skill artifact: a manifest.json with per-file sha256 checksums plus a
self-verifying POSIX install script that refuses to install on checksum
mismatch. Deterministic: same input files + version => byte-identical
manifest and install script (no timestamps unless explicitly provided).

Input (run(data)):
  {"files": {"SKILL.md": "...", "scripts/x.py": "..."},
   "version": "1.2.0",          # optional: explicit > frontmatter > 0.1.0
   "created": "2026-09-24"}     # optional provenance, omitted for reproducibility

Output:
  {"status": "ok", "artifact": "<slug>-<version>.skill",
   "manifest": {...}, "install_script": "...", "files": [...]}
  {"status": "error", "errors": [{"code": "SP0xx", ...}]} otherwise.

Codes:
  SP001 NO_SKILL_MD       bundle has no SKILL.md
  SP002 MISSING_METADATA  SKILL.md frontmatter lacks name or description
  SP003 PATH_ESCAPE       a file path is absolute or contains '..'
  SP04 BAD_VERSION        version (explicit or frontmatter) is not x.y.z
  SP005 EMPTY_BUNDLE      no files at all
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

import base64

DEFAULT_VERSION = "0.1.0"
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
INSTALL_TEMPLATE = """#!/bin/sh
# installer for {artifact} — self-extracting, verifies every file against manifest.json
set -eu
DEST="${{1:-${{SKILL_DEST:-.skills/{name}}}}}"
umask 022
mkdir -p "$DEST"
cd "$DEST"
cat > manifest.json <<'{delim}'
{manifest_json}
{delim}
python3 - <<'PYEOF'
import base64, hashlib, json, os, sys
try:
    manifest = json.load(open("manifest.json"))
except Exception as e:
    sys.exit("cannot read manifest.json: %s" % e)
bad = []
for entry in manifest["files"]:
    path = entry["path"]
    if ".." in path.replace("\\\\", "/").split("/") or path.startswith(("/", "~")):
        sys.exit("unsafe path in manifest: %s" % path)
    try:
        raw = base64.b64decode(entry["content_b64"])
    except Exception as e:
        sys.exit("%s: cannot decode payload: %s" % (path, e))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(raw)
    got = hashlib.sha256(raw).hexdigest()
    if got != entry["sha256"]:
        bad.append("%s: sha256 %s != %s" % (path, got, entry["sha256"]))
if bad:
    sys.exit("checksum verification FAILED:\\n  " + "\\n  ".join(bad))
print("ok: %d files extracted+verified for %s" % (len(manifest["files"]), manifest["name"]))
PYEOF
echo "installed {artifact} to $DEST"
"""


def _parse_frontmatter(text: str) -> Dict[str, str]:
    m = FRONTMATTER_RE.match(text)
    meta: Dict[str, str] = {}
    if not m:
        return meta
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip().strip("'\"")
    return meta


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "skill"


def _check_version(v: str) -> Optional[str]:
    return v if VERSION_RE.match(v) else None


def build_manifest(files: Dict[str, str], version: str,
                   created: Optional[str] = None) -> Dict[str, Any]:
    """Build the checksummed manifest for a validated file set."""
    entries = []
    for path in sorted(files):
        content = files[path]
        raw = content.encode("utf-8")
        entries.append({
            "path": path,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
            "content_b64": base64.b64encode(raw).decode("ascii"),
        })
    manifest: Dict[str, Any] = {
        "spec": "anthropic-skills/1",
        "version": version,
        "files": entries,
        "total_size": sum(e["size"] for e in entries),
    }
    if created:
        manifest["created"] = created
    return manifest


def verify_manifest(files: Dict[str, str], manifest: Dict[str, Any]) -> List[str]:
    """Verify a file set against a manifest; returns list of mismatches."""
    problems: List[str] = []
    by_path = {e["path"]: e for e in manifest.get("files", [])}
    for path, want in by_path.items():
        if path not in files:
            problems.append("%s: missing from bundle" % path)
            continue
        got = hashlib.sha256(files[path].encode("utf-8")).hexdigest()
        if got != want["sha256"]:
            problems.append("%s: sha256 mismatch" % path)
    extra = [p for p in files if p not in by_path]
    for p in sorted(extra):
        problems.append("%s: in bundle but not in manifest" % p)
    return problems


def build_install_script(name: str, version: str,
                         manifest: Dict[str, Any]) -> str:
    """Generate a self-extracting POSIX install script (payload embedded)."""
    artifact = "%s-%s.skill" % (_slug(name), version)
    manifest_json = json.dumps(manifest, indent=2, sort_keys=True)
    # the heredoc terminator must never collide with the JSON body
    delim = "MANIFEST_EOF"
    n = 0
    while delim in manifest_json:
        n += 1
        delim = "MANIFEST_EOF_%d" % n
    return INSTALL_TEMPLATE.format(
        artifact=artifact,
        name=_slug(name),
        delim=delim,
        manifest_json=manifest_json,
    )


def run(data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = data or {}
    raw_files: Dict[str, str] = data.get("files") or {}
    errors: List[Dict[str, str]] = []

    if not raw_files:
        return {"status": "error",
                "errors": [{"code": "SP005", "message": "empty bundle: no files"}]}

    # SP003 — path safety first
    norm: Dict[str, str] = {}
    for path, content in raw_files.items():
        parts = path.replace("\\", "/").split("/")
        if path.startswith(("/", "~")) or ".." in parts:
            errors.append({"code": "SP003", "path": path,
                           "message": "absolute path or '..' escape"})
            continue
        norm[path.lstrip("./")] = content
    if errors:
        return {"status": "error", "errors": errors}

    # SP001 — SKILL.md must exist (any depth, like the reference repo layout)
    skill_md_key = next(
        (p for p in sorted(norm) if p.lower().endswith("skill.md")), None)
    if skill_md_key is None:
        return {"status": "error", "errors": [
            {"code": "SP001", "message": "no SKILL.md in bundle"}]}

    meta = _parse_frontmatter(norm[skill_md_key])
    name = meta.get("name") or ""
    description = meta.get("description") or ""
    if not name or not description:
        return {"status": "error", "errors": [{
            "code": "SP002",
            "message": "SKILL.md frontmatter needs both name and description",
            "found": [k for k in ("name", "description") if not meta.get(k)],
        }]}

    # version precedence: explicit > frontmatter > default
    version = data.get("version") or meta.get("version") or DEFAULT_VERSION
    if not _check_version(version):
        return {"status": "error", "errors": [{
            "code": "SP004", "version": version,
            "message": "version must be semver x.y.z"}]}

    manifest = build_manifest(norm, version, created=data.get("created"))
    manifest["name"] = name
    manifest["description"] = description
    artifact = "%s-%s.skill" % (_slug(name), version)
    install_script = build_install_script(name, version, manifest)

    return {
        "status": "ok",
        "artifact": artifact,
        "manifest": manifest,
        "install_script": install_script,
        "files": [e["path"] for e in manifest["files"]],
    }
