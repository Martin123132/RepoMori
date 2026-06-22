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
- `release-verify.json` / `release-verify.md`: machine-readable and readable
  reports from `verify-release` when the release-candidate workflow runs.
- `release-evidence.json` / `release-evidence.md`: combined reviewer evidence
  bundle from `release-evidence`.
- `*.asc`: optional GPG detached signatures for integrity artifacts when
  release signing secrets are configured.
- `repomori-release-public-key.asc`: optional public key artifact when release
  signing is configured with a public key repository variable.

## Verify With RepoMori

Use the local verifier first. It checks the manifest, `checksums.txt`,
provenance, SBOM, wheel, source archive, byte sizes, and SHA-256 values:

```powershell
python -m repomori verify-release D:\Dev\RepoMori\.repomori-release-candidate --json
python -m repomori verify-release D:\Dev\RepoMori\.repomori-release-candidate --format markdown --out D:\Dev\RepoMori\.repomori-release-candidate\release-verify.md
python -m repomori verify-release D:\Dev\RepoMori\.repomori-release-candidate --policy D:\Dev\RepoMori\tests\fixtures\release-policy-basic.json --json
```

The report uses schema `repomori.release_verify.v1`. If you pass a downloaded
GitHub artifact parent directory, RepoMori will use the single nested release
package root when exactly one `release-candidate.json` is found.

`--policy` adds a `repomori.release_policy.v1` block to the verification report.
Use it to require release evidence, release-check status, schema versions,
signature/public-key presence, or warning/error thresholds. See
[release-policy.md](release-policy.md).

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

## Verify Signatures

When release signing is configured, the workflow writes detached ASCII-armored
signatures for:

- `checksums.txt`
- `release-provenance.json`
- `sbom.spdx.json`
- `release-verify.json`

Compare the public key fingerprint with a trusted record first:

```powershell
gpg --show-keys --with-fingerprint D:\Dev\RepoMori\.repomori-release-candidate\repomori-release-public-key.asc
```

Import the trusted RepoMori release public key, then verify:

```powershell
gpg --import D:\Dev\RepoMori\.repomori-release-candidate\repomori-release-public-key.asc
gpg --verify D:\Dev\RepoMori\.repomori-release-candidate\checksums.txt.asc D:\Dev\RepoMori\.repomori-release-candidate\checksums.txt
gpg --verify D:\Dev\RepoMori\.repomori-release-candidate\release-provenance.json.asc D:\Dev\RepoMori\.repomori-release-candidate\release-provenance.json
gpg --verify D:\Dev\RepoMori\.repomori-release-candidate\sbom.spdx.json.asc D:\Dev\RepoMori\.repomori-release-candidate\sbom.spdx.json
gpg --verify D:\Dev\RepoMori\.repomori-release-candidate\release-verify.json.asc D:\Dev\RepoMori\.repomori-release-candidate\release-verify.json
```

The workflows skip signing unless `REPOMORI_RELEASE_GPG_PRIVATE_KEY` is present.
Use `REPOMORI_RELEASE_GPG_PASSPHRASE` when the imported signing key requires a
passphrase.

See [release-signing.md](release-signing.md) for key generation, GitHub
configuration, public-key distribution, and rotation guidance.

## Read Evidence

`release-evidence.json` records release verification, release-check status,
optional release-health status, artifact hashes, signature presence, and workflow
metadata. Use it as the fastest procurement or reviewer entry point:

```powershell
python -m repomori release-evidence D:\Dev\RepoMori\.repomori-release-candidate `
  --release-check D:\Dev\RepoMori\.repomori-release-check\release-check.json `
  --out-dir D:\Dev\RepoMori\.repomori-release-candidate `
  --json
```

See [release-evidence.md](release-evidence.md) for the evidence pack runbook.

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

For stronger supply-chain guarantees, add external timestamping or transparency
log publication in a later release.
