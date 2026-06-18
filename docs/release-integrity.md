# Release Integrity

RepoMori release package workflows emit procurement-friendly integrity artifacts
beside the wheel and source archive.

These artifacts are local, dependency-free, and model-free. They are
tamper-evident aids, not cryptographic signatures, external attestations, or
proof that the artifacts were built by a trusted person.

## Artifacts

The release package workflow writes:

- `checksums.txt`: SHA-256 digest lines for the wheel, source archive,
  `sbom.spdx.json`, and `release-provenance.json`.
- `release-provenance.json`: RepoMori provenance record using
  `repomori.release_provenance.v1`.
- `sbom.spdx.json`: minimal SPDX 2.3 document for the package and release
  artifacts using `LicenseRef-PolyForm-Noncommercial-1.0.0`.
- `release-candidate.json`: release package manifest with an `integrity` block
  pointing to the checksum, provenance, and SBOM artifacts.

## Verify With RepoMori

Use the local verifier first. It checks the manifest, `checksums.txt`,
provenance, SBOM, wheel, source archive, byte sizes, and SHA-256 values:

```powershell
python -m repomori verify-release D:\Dev\RepoMori\.repomori-release-candidate --json
python -m repomori verify-release D:\Dev\RepoMori\.repomori-release-candidate --format markdown --out D:\Dev\RepoMori\.repomori-release-candidate\release-verify.md
```

The report uses schema `repomori.release_verify.v1`. If you pass a downloaded
GitHub artifact parent directory, RepoMori will use the single nested release
package root when exactly one `release-candidate.json` is found.

## Manual Verify On Windows

Download the release artifacts into one directory, then check each SHA-256 value
against `checksums.txt`:

```powershell
cd D:\Dev\RepoMori\.repomori-release-candidate\dist
Get-Content ..\checksums.txt | ForEach-Object {
  $parts = $_ -split "  ", 2
  $expected = $parts[0]
  $path = Join-Path .. $parts[1]
  $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
  if ($actual -ne $expected) {
    throw "checksum mismatch: $path"
  }
}
```

For a single file:

```powershell
Get-FileHash -Algorithm SHA256 `
  -LiteralPath D:\Dev\RepoMori\.repomori-release-candidate\dist\repomori-0.2.0-py3-none-any.whl
```

Compare the hash with the matching line in `checksums.txt`.

## Read Provenance

`release-provenance.json` records:

- RepoMori version;
- commit, ref, repository, workflow, and GitHub run id;
- generated timestamp;
- release artifact paths, byte sizes, and SHA-256 hashes;
- source-available license reference.

The provenance file lets a reviewer compare the release artifact bundle with
the workflow run that created it.

## Limits

Checksums detect accidental corruption and make substitution visible when the
expected checksum is trusted. They do not prove identity or intent.

For stronger supply-chain guarantees, add signed checksums, signed provenance,
or external transparency/timestamping in a later release.
