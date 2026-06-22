# Release Publishing

RepoMori keeps release publishing as a deliberate draft-first operation. The
`publish-release` workflow builds the release package, verifies it locally, then
creates or refreshes a GitHub draft release.

It does not publish a public release automatically. A maintainer must review the
draft, assets, notes, and license posture before publishing.

## Workflow

Run the workflow manually:

```powershell
gh workflow run publish-release.yml `
  --repo Martin123132/RepoMori `
  --ref main `
  -f version=0.2.1 `
  -f ref=main `
  -f tag=v0.2.1 `
  -f prerelease=false `
  -f overwrite_draft=true `
  -f release_policy=tests/fixtures/release-policy-basic.json
```

If `tag` is omitted, the workflow uses `v<version>`. If `notes_file` is omitted,
it uses `docs/releases/<version>.md`.
If `release_policy` is omitted, the workflow uses
`tests/fixtures/release-policy-basic.json`; point it at a stricter internal
policy file when signature/public-key requirements are mandatory. Checked
examples are available as `release-policy-dev-unsigned.json`,
`release-policy-enterprise-signed.json`, and
`release-policy-strict-no-warnings.json` under `tests/fixtures`.

## Safety Rules

- The workflow requires `pyproject.toml` to match the requested version.
- `release-check` must pass.
- The built wheel must install in a clean workflow environment.
- `verify-release` must return `repomori.release_verify.v1` with status `pass`.
- If a release policy is used, `verify-release --policy <file>` must also
  report `policy.status = pass`.
- GPG signatures are created when `REPOMORI_RELEASE_GPG_PRIVATE_KEY` is
  configured.
- Existing published releases are never overwritten.
- Existing draft releases are only updated when `overwrite_draft=true`.
- Assets are replaced with `gh release upload --clobber` only for draft releases.

## Draft Release Assets

The draft release receives:

- wheel
- source archive
- `checksums.txt`
- `release-provenance.json`
- `sbom.spdx.json`
- `release-verify.json`
- `release-verify.md`
- `release-verify-policy.json`
- `release-verify-policy.md`
- `release-review-checklist.md`
- `release-artifact-index.md`
- `release-evidence.json`
- `release-evidence.md`
- `release-candidate.json`
- `release-candidate.md`
- optional `*.asc` detached signatures for integrity artifacts
- optional `repomori-release-public-key.asc` for reviewers

The workflow also uploads a CI artifact bundle containing the same release
package plus release-check JSON, Markdown, and drift telemetry.

## Signing Secrets

Configure these GitHub Actions secrets to emit signatures:

- `REPOMORI_RELEASE_GPG_PRIVATE_KEY`: ASCII-armored private signing key.
- `REPOMORI_RELEASE_GPG_PASSPHRASE`: optional passphrase for that key.
- `REPOMORI_RELEASE_GPG_PUBLIC_KEY`: repository variable containing the matching
  ASCII-armored public key.

Keep the matching public-key fingerprint in a durable company location so
reviewers can verify `.asc` files independently. See
[release-signing.md](release-signing.md) for setup and rotation.

## Review Before Publishing

Before turning the draft into a public release:

```powershell
python -m repomori verify-release D:\Dev\RepoMori\.repomori-release-candidate --json
python -m repomori verify-release D:\Dev\RepoMori\.repomori-release-candidate `
  --policy D:\Dev\RepoMori\tests\fixtures\release-policy-basic.json `
  --json
```

Also confirm the tag target, release notes, license posture, checksums,
provenance, SBOM, release evidence, and commercial-use language.
