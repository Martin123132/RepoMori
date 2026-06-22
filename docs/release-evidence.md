# Release Evidence Pack

The release evidence pack is the reviewer-facing proof bundle for a RepoMori
release package. It collects local verification, release-check status,
provenance, SBOM, checksums, signature presence, artifact hashes, and workflow
metadata into one JSON/Markdown report.

It does not call a model, use the network, or trust a provider. It reads local
files that already exist in the release package.

## Build Evidence Locally

After a release package has been generated:

```powershell
python -m repomori release-evidence D:\Dev\RepoMori\.repomori-release-candidate `
  --repo D:\Dev\RepoMori `
  --release-check D:\Dev\RepoMori\.repomori-release-check\release-check.json `
  --out-dir D:\Dev\RepoMori\.repomori-release-candidate `
  --json
```

The command writes:

- `release-evidence.json`: schema `repomori.release_evidence.v1`.
- `release-evidence.md`: compact human-readable review summary.

It also prints JSON when `--json` is supplied.

Point `package_dir` at the exact release package root containing
`release-candidate.json`. A downloaded artifact parent directory is accepted
only when it contains one nested release package; if it contains several
historical check folders, pass the specific nested package root.

## What It Checks

- `verify-release` status for the release package.
- `release-check` status when supplied or discoverable.
- optional `release-health` status when supplied or discoverable.
- release version, commit, ref, workflow, repository, and run id.
- SHA-256 and byte size for package artifacts, integrity artifacts, reports,
  signatures, and public-key artifact.
- signature set completeness for `checksums.txt`,
  `release-provenance.json`, `sbom.spdx.json`, and `release-verify.json`.

Signature presence is not the same as trust. Reviewers still need to compare the
public key fingerprint through an independent channel before trusting `.asc`
files.

## CI Use

The `release-candidate` and `publish-release` workflows write
`release-evidence.json` and `release-evidence.md` after release verification and
optional signing. The publish workflow attaches those files to draft GitHub
releases.

Treat `status="fail"` as a release blocker. A signed release without all
expected signature files reports a warning-level partial signature state.
