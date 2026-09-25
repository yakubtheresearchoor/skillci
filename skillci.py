#!/usr/bin/env python3
"""skillci — package, lint, and compatibility-check agent skills.

One tool for the skill economy's missing QA layer:

  lint     offline quality linter (5 failure classes, zero network)
  package  bundle a skill dir into a versioned, checksummed .skill artifact
  check    validate a plugin/skill set's version constraints (SemVer engine)

Stdlib-only Python 3.9+. Each subcommand reads a skill bundle
({"files": {path: content}}) from a JSON file or directory tree.
"""
import argparse
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE / "modules"))

import lint as _lint      # noqa: E402  (dsh_plugins_skillspector)
import pack as _pack      # noqa: E402  (agent_skills_skills)
import check as _check    # noqa: E402  (dsh_plugins_deepseek_harness)


def load_bundle(path: str) -> dict:
    """JSON file {"files": {...}} OR a directory tree -> {"files": {...}}."""
    p = Path(path)
    if p.is_dir():
        return {"files": {
            str(f.relative_to(p)): f.read_text(encoding="utf-8", errors="replace")
            for f in sorted(p.rglob("*")) if f.is_file() and "__pycache__" not in str(f)
        }}
    data = json.loads(p.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {"files": data}


def cmd_lint(args) -> int:
    out = _lint.run(load_bundle(args.bundle))
    print(json.dumps(out, indent=2))
    return 0 if out.get("status") == "ok" else 1


def cmd_package(args) -> int:
    bundle = load_bundle(args.bundle)
    bundle.setdefault("version", args.version or None)
    out = _pack.run(bundle)
    if out.get("status") != "ok":
        print(json.dumps(out, indent=2))
        return 1
    dest = Path(args.out or ".")
    dest.mkdir(parents=True, exist_ok=True)
    art = dest / out["artifact"]
    payload = {
        "manifest": out["manifest"],
        "files": {f: _read_source(bundle, f) for f in out["files"]},
    }
    art.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    (dest / "install.sh").write_text(out["install_script"], encoding="utf-8")
    print(f"packaged {art} (+ install.sh, self-verifying)")
    return 0


def _read_source(bundle: dict, name: str) -> str:
    return bundle.get("files", {}).get(name, "")


def cmd_check(args) -> int:
    data = json.loads(Path(args.set_file).read_text(encoding="utf-8"))
    out = _check.run(data)
    print(json.dumps(out, indent=2))
    return 0 if out.get("status") in ("ok", "compatible") else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="skillci",
                                 description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("lint", help="offline quality lint of a skill bundle")
    p.add_argument("bundle", help="skill directory or bundle JSON")
    p.set_defaults(fn=cmd_lint)

    p = sub.add_parser("package", help="bundle a skill into a .skill artifact")
    p.add_argument("bundle", help="skill directory or bundle JSON")
    p.add_argument("--version", help="override version (else frontmatter)")
    p.add_argument("--out", help="output directory (default .)")
    p.set_defaults(fn=cmd_package)

    p = sub.add_parser("check", help="validate a plugin set's constraints")
    p.add_argument("set_file", help="JSON: {harness, plugins:[{name,version,"
                                    "requires,deps}]}")
    p.set_defaults(fn=cmd_check)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
