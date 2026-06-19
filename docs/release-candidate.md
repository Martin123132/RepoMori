# Release Candidate Process

RepoMori release candidates are source-available pre-releases. They prove the
local package, public-release scan, compatibility contracts, and operational
health gates before a final release tag is cut.

Latest validated candidate: `0.2.0rc1`.

Final release: `0.2.0`.

## Local Gate

Run these from a clean checkout:

```powershell
python -m repomori release-check D:\Dev\RepoMori `
  --baseline D:\Dev\RepoMori\.repomori-scan-baseline.json `
  --fail-on low `
  --artifacts-dir D:\Dev\RepoMori\.repomori-release-candidate\release-check `
  --drift-log D:\Dev\RepoMori\.repomori-release-candidate\release-check\baseline-drift.jsonl `
  --json

python -m repomori memory D:\Dev\RepoMori `
  --out-dir D:\Dev\RepoMori\.repomori-packs `
  --no-handoff `
  --json

python -m repomori release-health D:\Dev\RepoMori `
  --snapshot-dir D:\Dev\RepoMori\.repomori-packs `
  --baseline D:\Dev\RepoMori\.repomori-scan-baseline.json `
  --artifacts-dir D:\Dev\RepoMori\.repomori-release-candidate\health `
  --drift-log D:\Dev\RepoMori\.repomori-release-candidate\health\baseline-drift.jsonl `
  --json

python -m pip wheel D:\Dev\RepoMori --no-deps `
  --wheel-dir D:\Dev\RepoMori\.repomori-release-candidate\dist
```

Install the wheel in a clean environment before calling the candidate ready:

```powershell
python -m pip install --force-reinstall --no-index `
  --find-links D:\Dev\RepoMori\.repomori-release-candidate\dist `
  repomori==0.2.0rc1

repomori demo --out D:\Dev\RepoMori\.repomori-release-candidate\demo --force --json
repomori contract-check --fixture D:\Dev\RepoMori\tests\fixtures\compat-contracts.json --json
```

Generated outputs should stay under hidden `.repomori-*` directories so
`release-check` remains strict about visible repository artifacts.

## GitHub Candidate Workflow

The `release-candidate` workflow builds the same proof bundle in CI. It can be
run manually while preparing the candidate:

```powershell
gh workflow run release-candidate.yml `
  --repo Martin123132/RepoMori `
  --ref main `
  -f version=0.2.0rc1 `
  -f ref=main
```

It also runs automatically for pushed tags matching `v*`. The workflow does
not publish a GitHub release by itself; it uploads reviewable artifacts:

- wheel in `.repomori-release-candidate/dist`
- source archive made from the checked-out commit
- `checksums.txt`
- `release-provenance.json`
- `sbom.spdx.json`
- `release-verify.json`
- `release-verify.md`
- `release-candidate.json`
- `release-candidate.md`
- `release-check` JSON, Markdown, and drift log
- optional `*.asc` GPG signatures when release signing secrets are configured
- optional `repomori-release-public-key.asc` when the public key variable is
  configured

See [release-integrity.md](release-integrity.md) for checksum, provenance, and
SBOM verification guidance. See [release-signing.md](release-signing.md) for
signing setup and key rotation.

After downloading or unpacking the workflow artifact, verify the whole integrity
bundle locally:

```powershell
python -m repomori verify-release D:\Dev\RepoMori\.repomori-release-candidate --json
```

## Tag And Pre-Release

Only tag after local checks and the candidate workflow are green:

```powershell
git status --short
git tag -a v0.2.0rc1 -m "RepoMori 0.2.0rc1"
git push origin v0.2.0rc1
```

After the tag workflow succeeds, create a GitHub pre-release from the release
notes:

```powershell
gh release create v0.2.0rc1 `
  --repo Martin123132/RepoMori `
  --title "RepoMori 0.2.0rc1" `
  --notes-file D:\Dev\RepoMori\docs\releases\0.2.0rc1.md `
  --prerelease
```

For future releases, prefer the draft-first publish workflow instead of
attaching assets manually:

```powershell
gh workflow run publish-release.yml `
  --repo Martin123132/RepoMori `
  --ref main `
  -f version=0.2.1 `
  -f ref=main `
  -f tag=v0.2.1 `
  -f prerelease=false
```

See [release-publishing.md](release-publishing.md) for the draft-release
automation runbook.

After publishing the pre-release, run an outside-in install smoke from the
published wheel and record the result in `docs/releases/0.2.0rc1-validation.md`.

## Final Release Promotion

For future final releases, promote by changing `pyproject.toml` from the latest
release-candidate version to the final version, moving the changelog heading to
the final release date, rerunning the same gates, then tagging the final version.

Use `docs/releases/0.2.0-final-promotion.md` as the final checklist.
