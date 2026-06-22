# Release Candidate Process

RepoMori release candidates are source-available pre-releases. They prove the
local package, public-release scan, compatibility contracts, and operational
health gates before a final release tag is cut.

The `0.2.0` release line used validated candidate `0.2.0rc1` before the final
`0.2.0` release. Treat those as historical release records; use this runbook
for the next candidate by substituting the version, tag, and notes paths.

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
  -f ref=main `
  -f release_policy=tests/fixtures/release-policy-basic.json
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
- `release-verify-policy.json`
- `release-verify-policy.md`
- `release-review-checklist.md`
- `release-artifact-index.md`
- `release-bundle-completeness.json`
- `release-review-handoff.json`
- `release-review-handoff.md`
- `release-evidence.json`
- `release-evidence.md`
- `release-candidate.json`
- `release-candidate.md`
- `release-check` JSON, Markdown, and drift log
- optional `*.asc` GPG signatures when release signing secrets are configured
- optional `repomori-release-public-key.asc` when the public key variable is
  configured

The workflow validates that `release-verify-policy.json` contains a
`policy.review.decision` of `reviewable` and that `release-verify-policy.md`
renders the same reviewer guidance before the artifact bundle is uploaded. The
Markdown policy artifact also includes a profile preflight section linking to
[release-policy-selection.md](release-policy-selection.md),
[release-policy-matrix.md](release-policy-matrix.md), and
[release-policy.md#policy-diagnostics](release-policy.md#policy-diagnostics)
so reviewers can confirm the selected profile before approving the candidate.
It also writes `release-review-checklist.md`, a fill-in reviewer decision log
covering the selected profile, policy outcome, diagnostics reviewed,
artifact hash/provenance checks, and final reviewer decision.
The companion `release-artifact-index.md` is a first-stop reviewer map of the
expected candidate artifacts, selected policy profile, policy outcome, and
diagnostics references. It also shows the generation order: provisional
completeness feeds the handoff, then final fail-fast completeness requires
`release-review-handoff.json` and `release-review-handoff.md` before upload.
Before upload, the workflow writes `release-bundle-completeness.json` and fails
if reviewer artifacts, selected profile, checksum/provenance material, or
diagnostics references are missing. Failed completeness reports include a
`remediation` list plus per-error remediation objects so maintainers can see the
next repair step from the uploaded CI artifact.
The final first-read artifact is `release-review-handoff.md`, backed by
`release-review-handoff.json` using `repomori.release_review_handoff.v1`. It
summarises the selected profile, policy outcome, completeness status, artifact
index, checklist, diagnostics references, and any remediation list. The final
bundle completeness check also expects these handoff artifacts and fails fast if
they are missing or no longer match the selected policy profile/outcome.

## Bundle Completeness Remediation

When `release-bundle-completeness.json` reports `status: "fail"`, fix the
bundle generator that owns the failed artifact, then rerun the release-candidate
workflow. Common failure groups are:

| Failure Area | Usually Means | Reviewer/Maintainer Next Step |
| --- | --- | --- |
| policy report | `release-verify-policy.json` or `.md` is missing, invalid, or lacks policy schema/profile data. | Re-run `python -m repomori verify-release ... --policy <policy>` and confirm the workflow `release_policy` input points at a checked policy file. |
| release evidence | `release-evidence.json` or `.md` is missing or not `repomori.release_evidence.v1`. | Re-run `python -m repomori release-evidence` after `release-check` and `verify-release` artifacts exist. |
| checksums, provenance, and SBOM | `checksums.txt`, `release-provenance.json`, `sbom.spdx.json`, or `release-candidate.json` is missing or stale. | Rebuild the candidate package so `write_release_package_artifacts` regenerates integrity files from the current `dist` artifacts. |
| reviewer checklist | `release-review-checklist.md` is missing selected profile, policy outcome, hash/provenance checks, or final decision placeholders. | Regenerate the checklist from the policy report and release evidence before review. |
| artifact index and diagnostics references | `release-artifact-index.md` is missing the policy report, checklist, matrix, diagnostics guide, or evidence references. | Regenerate the artifact index and confirm links to the selection guide, matrix, diagnostics guide, integrity guide, and evidence runbook. |
| reviewer handoff | `release-review-handoff.json` or `.md` is missing, stale, or no longer matches the selected profile/policy outcome. | Regenerate the handoff after the policy report, evidence, artifact index, checklist, and provisional completeness report are current. |
| selected profile | The policy report is present but `policy.profile` is empty. | Use one of the checked policy profiles or fix the policy JSON before approving the candidate. |

See [release-integrity.md](release-integrity.md) for checksum, provenance, and
SBOM verification guidance. See [release-signing.md](release-signing.md) for
signing setup and key rotation.
See [release-policy.md](release-policy.md) for checked policy profiles such as
`release-policy-dev-unsigned.json`,
`release-policy-enterprise-signed.json`, and
`release-policy-strict-no-warnings.json`.

After downloading or unpacking the workflow artifact, verify the whole integrity
bundle locally:

```powershell
python -m repomori verify-release D:\Dev\RepoMori\.repomori-release-candidate --json
python -m repomori verify-release D:\Dev\RepoMori\.repomori-release-candidate `
  --policy D:\Dev\RepoMori\tests\fixtures\release-policy-basic.json `
  --json
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
  -f prerelease=false `
  -f release_policy=tests/fixtures/release-policy-basic.json
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
