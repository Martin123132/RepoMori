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
  -f overwrite_draft=true
```

If `tag` is omitted, the workflow uses `v<version>`. If `notes_file` is omitted,
it uses `docs/releases/<version>.md`.

## Safety Rules

- The workflow requires `pyproject.toml` to match the requested version.
- `release-check` must pass.
- The built wheel must install in a clean workflow environment.
- `verify-release` must return `repomori.release_verify.v1` with status `pass`.
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
- `release-candidate.json`
- `release-candidate.md`

The workflow also uploads a CI artifact bundle containing the same release
package plus release-check JSON, Markdown, and drift telemetry.

## Review Before Publishing

Before turning the draft into a public release:

```powershell
python -m repomori verify-release D:\Dev\RepoMori\.repomori-release-candidate --json
```

Also confirm the tag target, release notes, license posture, checksums,
provenance, SBOM, and commercial-use language.
