# Release Verification Policy

`verify-release --policy` lets maintainers and reviewers codify what evidence a
release package must contain. The policy is local JSON, deterministic, and does
not call a network service or model provider.

```powershell
python -m repomori verify-release D:\Dev\RepoMori\.repomori-release-candidate `
  --policy D:\Dev\RepoMori\tests\fixtures\release-policy-basic.json `
  --json
```

The command still emits `repomori.release_verify.v1`. When a policy is supplied,
the report includes a `policy` block using schema `repomori.release_policy.v1`
and a `release_policy` check. Policy failure makes `verify-release` exit
nonzero.

## Policy Shape

```json
{
  "schema_version": "repomori.release_policy.v1",
  "require": {
    "checksums": true,
    "provenance": true,
    "sbom": true,
    "release_evidence": true,
    "release_check": true,
    "signatures": false,
    "public_key": false
  },
  "allowed_statuses": {
    "release_verify": ["pass"],
    "release_evidence": ["pass"],
    "release_check": ["pass"],
    "release_health": ["pass", "warn"],
    "signatures": ["unsigned", "signed"]
  },
  "required_schemas": {
    "release_candidate": "repomori.release_candidate.v1",
    "provenance": "repomori.release_provenance.v1",
    "sbom": "SPDX-2.3",
    "release_verify": "repomori.release_verify.v1",
    "release_evidence": "repomori.release_evidence.v1",
    "release_check": "repomori.release_check.v1"
  },
  "max_warnings": 0,
  "max_errors": 0
}
```

Supported `require` keys:

- `manifest`
- `checksums`
- `provenance`
- `sbom`
- `release_verify_report`
- `release_verify_markdown`
- `release_evidence`
- `release_evidence_markdown`
- `release_check`
- `release_health`
- `signatures`
- `public_key`

Supported `allowed_statuses` keys:

- `release_verify`
- `release_evidence`
- `release_check`
- `release_health`
- `signatures`

Supported `required_schemas` keys:

- `release_candidate` or `manifest`
- `provenance`
- `sbom`
- `release_verify`
- `release_evidence`
- `release_check`
- `release_health`

## Signature Policy

Unsigned release-candidate artifacts can pass the basic policy while signing is
being rolled out:

```json
{
  "allowed_statuses": {
    "signatures": ["unsigned", "signed"]
  }
}
```

For a stricter release lane, require the complete signature set and public key:

```json
{
  "require": {
    "signatures": true,
    "public_key": true
  },
  "allowed_statuses": {
    "signatures": ["signed"]
  }
}
```

Signature presence is not the same as identity trust. Reviewers still need to
verify the public key fingerprint through an independent channel.

## Recommended Use

Use the basic policy for candidate review:

```powershell
python -m repomori verify-release D:\Dev\RepoMori\.repomori-release-candidate `
  --policy D:\Dev\RepoMori\tests\fixtures\release-policy-basic.json `
  --format markdown `
  --out D:\Dev\RepoMori\.repomori-release-candidate\release-verify-policy.md
```

Use a stricter company policy when release signing is configured and the public
key fingerprint is already distributed through a trusted channel.
