# skillci

Package, lint, and compatibility-check agent skills — the QA layer the skill
economy is missing.

The 2026 skill wave ([anthropics/skills](https://github.com/anthropics/skills)
177k★, [obra/superpowers](https://github.com/obra/superpowers) 290k★, hundreds
of skill repos) ships bundles that look fine to the model that wrote them and
break for the human who installs them: missing files, vague descriptions,
bloated context, version conflicts. `skillci` is a single stdlib-only tool
that catches all of it offline.

## Install

No dependencies. Python 3.9+.

```sh
git clone https://github.com/yakubtheresearchoor/skillci && cd skillci
./skillci.py --help
```

## Use

```sh
# 1. Lint a skill directory (5 failure classes, zero network)
./skillci.py lint ./my-skill

# 2. Package it into a versioned, checksummed .skill artifact + installer
./skillci.py package ./my-skill --out dist/

# 3. Check a set of skills/plugins against harness + each other (SemVer)
./skillci.py check plugins.json
```

### What each command catches

**lint** — `SK001` missing metadata · `SK002` vague/unretrievable description ·
`SK003` dangling file references (backticked paths absent from the bundle) ·
`SK004` no examples anywhere · `SK005` context bloat (over-budget SKILL.md or
shipped-but-unreferenced files).

**package** — bundles `SKILL.md` + assets into `<name>-<version>.skill` with a
manifest of per-file sha256 checksums and a self-verifying `install.sh` that
refuses on checksum mismatch. Deterministic: same input → byte-identical
artifact.

**check** — validates a plugin set: harness-range violations, missing
dependencies, version conflicts, duplicate registrations, unparseable
versions, dependency cycles. Full SemVer incl. pre-releases (`~=` upper
bounds, `!=` exclusions).

## Exit codes

`0` clean / `1` findings or incompatibilities — CI-ready:

```yaml
- run: git clone https://github.com/yakubtheresearchoor/skillci
- run: python skillci/skillci.py lint ./skills/$SKILL
```

## Tests

```sh
python3 -m pytest test_skillci.py -q   # 9/9
```

## License

MIT
