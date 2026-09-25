#!/usr/bin/env python3
"""End-to-end tests for skillci: lint, package, check — the full pipeline
on a realistic skill bundle, plus CLI smoke tests.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "modules"))
sys.path.insert(0, str(HERE))

import lint, pack, check  # noqa: E402
import skillci  # noqa: E402

GOOD_SKILL_MD = """---
name: pdf-extract
description: Extract tables and text from PDF files into structured output
version: 1.2.0
---

# PDF Extract

Runs the extractor and returns markdown tables.

## Example

```sh
python scripts/extract.py sample.pdf
```
"""

BROKEN_SKILL_MD = """---
name: broken
description: does stuff
---

Run `scripts/helper.py` then read `references/notes.md`.
"""


@pytest.fixture
def good_bundle(tmp_path):
    d = tmp_path / "pdf-extract"
    (d / "scripts").mkdir(parents=True)
    (d / "examples").mkdir(parents=True)
    (d / "SKILL.md").write_text(GOOD_SKILL_MD)
    (d / "scripts" / "extract.py").write_text(
        "import sys\nprint(sys.argv[1])\n")
    (d / "examples" / "sample.pdf").write_text("%PDF-1.4 fake")
    return d


def test_lint_passes_good_skill(good_bundle):
    out = lint.run(skillci.load_bundle(str(good_bundle)))
    assert out["status"] == "ok", out
    assert out.get("findings", []) == []


def test_lint_catches_broken_skill(tmp_path):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "SKILL.md").write_text(BROKEN_SKILL_MD)
    out = lint.run(skillci.load_bundle(str(d)))
    rules = {f["code"] for f in out["findings"]}
    assert "SK003" in rules  # dangling references
    assert "SK004" in rules  # no examples


def test_package_creates_checksummed_artifact(good_bundle, tmp_path):
    out = pack.run(skillci.load_bundle(str(good_bundle)))
    assert out["status"] == "ok"
    assert out["artifact"] == "pdf-extract-1.2.0.skill"
    man = out["manifest"]
    assert man["version"] == "1.2.0"
    by_path = {f["path"]: f["sha256"] for f in man["files"]}
    assert len(by_path["SKILL.md"]) == 64  # hex sha256
    assert "#!/bin/sh" in out["install_script"]


def test_package_deterministic(good_bundle):
    a = pack.run(skillci.load_bundle(str(good_bundle)))
    b = pack.run(skillci.load_bundle(str(good_bundle)))
    assert a["manifest"] == b["manifest"]
    assert a["install_script"] == b["install_script"]


def test_check_validates_plugin_set():
    data = {
        "harness_version": "2.3.1",
        "plugins": [
            {"name": "alpha", "version": "1.0.0", "requires": ">=2.0.0",
             "deps": {"beta": ">=0.5.0"}},
            {"name": "beta", "version": "0.6.1", "requires": ">=2.1.0",
             "deps": {}},
        ],
    }
    out = check.run(data)
    assert out["status"] == "ok", out


def test_check_catches_harness_violation():
    data = {"harness_version": "1.0.0",
            "plugins": [{"name": "alpha", "version": "1.0.0",
                         "requires": ">=2.0.0", "deps": {}}]}
    out = check.run(data)
    assert out["status"] != "ok"


def test_cli_lint(good_bundle):
    r = subprocess.run(
        [sys.executable, str(HERE / "skillci.py"), "lint", str(good_bundle)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout
    assert json.loads(r.stdout)["status"] == "ok"


def test_cli_package(good_bundle, tmp_path):
    r = subprocess.run(
        [sys.executable, str(HERE / "skillci.py"), "package",
         str(good_bundle), "--out", str(tmp_path)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "pdf-extract-1.2.0.skill").exists()
    assert (tmp_path / "install.sh").exists()


def test_cli_check(tmp_path):
    data = {"harness_version": "2.3.1",
            "plugins": [{"name": "alpha", "version": "1.0.0",
                         "requires": ">=2.0.0", "deps": {}}]}
    f = tmp_path / "set.json"
    f.write_text(json.dumps(data))
    r = subprocess.run(
        [sys.executable, str(HERE / "skillci.py"), "check", str(f)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["status"] == "ok"
