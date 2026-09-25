"""Plugin dependency/version compatibility checker for DeepSeek Harness (DSH)
plugins — the harness README promises breaking changes between releases, so
plugins declare a `requires` range against the harness version and `deps`
ranges against other plugins. This tool validates a plugin set against a
harness version and against each other: harness-range violations, missing
dependencies, version conflicts, duplicate registrations, unparseable
versions, and dependency cycles.

Upgraded from template by the R&D shift (deterministic, no GLM needed).
See GAP.md — anchor: deepseek-ai/deepseek-harness.
"""
import json
import re

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")
_CONST_RE = re.compile(
    r"^(==|!=|>=|<=|>|<|~=)\s*v?(\d+(?:\.\d+){0,2})(?:-([0-9A-Za-z.-]+))?$")


# ---------------------------------------------------------------- versions

def parse_version(v):
    """'1.4.0' -> (1, 4, 0, ''); '1.4.0-rc.1' -> (1, 4, 0, 'rc.1'); else None."""
    if v is None:
        return None
    m = _VERSION_RE.match(str(v).strip().lstrip("v"))
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4) or "")


def _key(vtup):
    """Sort key: pre-releases order before the final release of same triple."""
    major, minor, patch, pre = vtup
    return (major, minor, patch, 1 if not pre else 0, pre)


def _pad(parts):
    return tuple(int(p) for p in parts) + (0,) * (3 - len(parts))


def satisfies(version, spec):
    """True if `version` satisfies a comma-separated constraint `spec`.

    Supports ==, !=, >=, <=, >, <, ~= (compatible release) and the any-spec
    '*' / ''. Partial versions in constraints ('1.4') are zero-padded;
    '==' / '!=' with a partial version do prefix matching.
    """
    vtup = parse_version(version)
    if vtup is None:
        return False
    spec = (spec or "*").strip()
    if spec in ("*", ""):
        return True
    for clause in spec.split(","):
        clause = clause.strip()
        if not clause or clause == "*":
            continue
        m = _CONST_RE.match(clause)
        if not m:
            return False  # unparseable clause fails closed
        op, vparts = m.group(1), m.group(2).split(".")
        pre = m.group(3) or ""
        if op == "~=":
            # ~=1.4.2 -> >=1.4.2,<1.5.0 ; ~=1.4 -> >=1.4,<2.0
            if len(vparts) < 2:
                ok = False
            else:
                lo = _pad(vparts) + (pre,)
                hi_parts = list(vparts[:len(vparts) - 1])
                hi_parts[-1] = str(int(hi_parts[-1]) + 1)
                hi = _pad(hi_parts)
                ok = _key(vtup) >= _key(lo) and _key(vtup) < _key(hi + ("",))
        elif op in ("==", "!=") and len(vparts) < 3 and not pre:
            triple = _pad(vparts)
            ok = vtup[:len(vparts)] == triple[:len(vparts)]
        else:
            target = _pad(vparts) + (pre,)
            cmp_key, tgt_key = _key(vtup), _key(target)
            ok = {"==": cmp_key == tgt_key, "!=": cmp_key != tgt_key,
                  ">=": cmp_key >= tgt_key, "<=": cmp_key <= tgt_key,
                  ">": cmp_key > tgt_key, "<": cmp_key < tgt_key}[op]
        if op == "!=":
            ok = not ok
        if not ok:
            return False
    return True


# ------------------------------------------------------------ graph checks

def _find_cycles(index):
    """Return one cyclic path (list of names) if the dep graph has a cycle."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {name: WHITE for name in index}
    stack = []

    def dfs(node):
        color[node] = GRAY
        stack.append(node)
        for dep in sorted(index[node].get("deps", {}) or {}):
            if dep not in index:
                continue
            if color[dep] == GRAY:
                return stack[stack.index(dep):] + [dep]
            if color[dep] == WHITE:
                found = dfs(dep)
                if found:
                    return found
        stack.pop()
        color[node] = BLACK
        return None

    for name in sorted(index):
        if color[name] == WHITE:
            found = dfs(name)
            if found:
                return found
    return None


def check_compatibility(harness_version, plugins):
    """Validate a plugin set. Returns a report dict; status is 'ok' when the
    plugin set can load together on the given harness version."""
    problems = []
    index = {}

    for p in plugins or []:
        name = str(p.get("name", "")).strip()
        if not name:
            problems.append({"plugin": None, "type": "invalid",
                             "detail": "plugin entry without a name"})
            continue
        if name in index:
            problems.append({"plugin": name, "type": "duplicate",
                             "detail": f"registered twice ({index[name]['version']} and {p.get('version')})"})
            continue
        vtup = parse_version(p.get("version"))
        if vtup is None:
            problems.append({"plugin": name, "type": "bad_version",
                             "detail": f"unparseable own version {p.get('version')!r}"})
        index[name] = p

    hv = parse_version(harness_version)
    if hv is None:
        problems.append({"plugin": None, "type": "bad_version",
                         "detail": f"unparseable harness version {harness_version!r}"})

    compatible = []
    for name, p in sorted(index.items()):
        ok = True
        req = p.get("requires")
        if req and not satisfies(harness_version, req):
            problems.append({"plugin": name, "type": "harness_range",
                             "detail": f"requires dsh {req}, harness is {harness_version}"})
            ok = False
        for dep, dspec in sorted((p.get("deps") or {}).items()):
            if dep not in index:
                problems.append({"plugin": name, "type": "missing_dep",
                                 "detail": f"depends on {dep} {dspec or '*'}, not in the set"})
                ok = False
            elif not satisfies(index[dep].get("version"), dspec):
                problems.append({"plugin": name, "type": "version_conflict",
                                 "detail": f"needs {dep} {dspec}, set provides {index[dep].get('version')}"})
                ok = False
        if ok and parse_version(p.get("version")) is not None:
            compatible.append(name)

    cycle = _find_cycles(index)
    if cycle:
        problems.append({"plugin": cycle[0], "type": "cycle",
                         "detail": "dependency cycle: " + " -> ".join(cycle)})

    return {"status": "ok" if not problems else "incompatible",
            "harness_version": harness_version,
            "plugins_checked": len(index),
            "compatible": compatible,
            "problems": problems}


def run(data=None):
    """Entrypoint. `data` may be a dict (or JSON string) with
    harness_version + plugins; with no input it runs a built-in demo set."""
    if data is None:
        data = {
            "harness_version": "1.4.2",
            "plugins": [
                {"name": "code-indexer", "version": "0.3.1",
                 "requires": ">=1.2,<1.5", "deps": {"tree-sitter-dsh": ">=2.1"}},
                {"name": "tree-sitter-dsh", "version": "2.3.0",
                 "requires": ">=1.0"},
            ],
        }
    if isinstance(data, str):
        data = json.loads(data)
    return check_compatibility(data.get("harness_version", "0.0.0"),
                               data.get("plugins", []))
