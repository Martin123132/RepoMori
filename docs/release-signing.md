# Release Signing Operations

RepoMori release signing is optional, but when it is enabled the release
workflows create detached GPG signatures for the integrity artifacts. Signing
adds an identity check on top of checksums, provenance, SBOM, and
`verify-release`.

The private signing key must never be committed. Keep local key exports under
`D:\Dev\RepoMori\.repomori-release-keys`, which is ignored by git.

## Signing Model

- `REPOMORI_RELEASE_GPG_PRIVATE_KEY`: GitHub Actions secret containing the
  ASCII-armored private signing key.
- `REPOMORI_RELEASE_GPG_PASSPHRASE`: optional GitHub Actions secret containing
  the private key passphrase.
- `REPOMORI_RELEASE_GPG_PUBLIC_KEY`: GitHub Actions repository variable
  containing the matching ASCII-armored public key.

When the private key secret is missing, release workflows skip signing and still
produce unsigned release packages. When the public key variable is present, the
workflow writes `repomori-release-public-key.asc` into the release package and
checks that it matches the private signing key fingerprint.

## Create A Release Key

Create the key on a trusted maintainer machine:

```powershell
New-Item -ItemType Directory -Force D:\Dev\RepoMori\.repomori-release-keys | Out-Null
gpg --quick-generate-key "RepoMori Release Signing" ed25519 sign 2y
```

Capture the fingerprint:

```powershell
$fingerprint = (
  gpg --with-colons --list-secret-keys "RepoMori Release Signing" |
    Select-String '^fpr:' |
    Select-Object -First 1
).Line.Split(':')[9]

$fingerprint
```

Export the private and public keys:

```powershell
gpg --armor --output D:\Dev\RepoMori\.repomori-release-keys\repomori-release-private-key.asc --export-secret-keys $fingerprint
gpg --armor --output D:\Dev\RepoMori\.repomori-release-keys\repomori-release-public-key.asc --export $fingerprint
```

Record the fingerprint in a company-controlled location outside the release
bundle, such as internal release notes, a company website, or a protected
operations vault. The public key can be shared; the private key and passphrase
cannot.

## Configure GitHub

Upload the private key as a repository secret:

```powershell
gh secret set REPOMORI_RELEASE_GPG_PRIVATE_KEY `
  --repo Martin123132/RepoMori `
  < D:\Dev\RepoMori\.repomori-release-keys\repomori-release-private-key.asc
```

If the key has a passphrase, set it interactively:

```powershell
gh secret set REPOMORI_RELEASE_GPG_PASSPHRASE --repo Martin123132/RepoMori
```

Upload the public key as a repository variable:

```powershell
gh variable set REPOMORI_RELEASE_GPG_PUBLIC_KEY `
  --repo Martin123132/RepoMori `
  < D:\Dev\RepoMori\.repomori-release-keys\repomori-release-public-key.asc
```

Confirm the entries exist:

```powershell
gh secret list --repo Martin123132/RepoMori
gh variable list --repo Martin123132/RepoMori
```

## Verify A Signed Release

After downloading a release package, compare the public key fingerprint with the
trusted fingerprint before using it:

```powershell
gpg --show-keys --with-fingerprint D:\Dev\RepoMori\.repomori-release-candidate\repomori-release-public-key.asc
```

Then verify signatures:

```powershell
gpg --import D:\Dev\RepoMori\.repomori-release-candidate\repomori-release-public-key.asc
gpg --verify D:\Dev\RepoMori\.repomori-release-candidate\checksums.txt.asc D:\Dev\RepoMori\.repomori-release-candidate\checksums.txt
gpg --verify D:\Dev\RepoMori\.repomori-release-candidate\release-provenance.json.asc D:\Dev\RepoMori\.repomori-release-candidate\release-provenance.json
gpg --verify D:\Dev\RepoMori\.repomori-release-candidate\sbom.spdx.json.asc D:\Dev\RepoMori\.repomori-release-candidate\sbom.spdx.json
gpg --verify D:\Dev\RepoMori\.repomori-release-candidate\release-verify.json.asc D:\Dev\RepoMori\.repomori-release-candidate\release-verify.json
```

Run RepoMori's release verifier as the package-level check:

```powershell
python -m repomori verify-release D:\Dev\RepoMori\.repomori-release-candidate --json
```

## Rotation

Rotate the release key when a maintainer changes, a key is suspected to be
exposed, the expiry date is near, or the signing policy changes.

Use this sequence:

- generate a new key and record the new fingerprint;
- update `REPOMORI_RELEASE_GPG_PRIVATE_KEY`,
  `REPOMORI_RELEASE_GPG_PASSPHRASE`, and
  `REPOMORI_RELEASE_GPG_PUBLIC_KEY`;
- trigger `release-candidate.yml` and verify the generated
  `repomori-release-public-key.asc` fingerprint;
- keep old public keys available so older release signatures remain
  verifiable;
- record which release version was first signed by the new key.

Do not overwrite a published release merely to rotate a key. New signatures
should start with the next release candidate or draft release.

## Limits

Detached signatures prove that the signer controlled the private key at signing
time. They do not prove that the source code is safe, that the build machine was
compromise-free, or that the public key is trustworthy unless the fingerprint is
checked through an independent channel.
