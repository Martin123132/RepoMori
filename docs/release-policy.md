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

Policy reports also include a `profile` name and a small `review` block with a
review decision (`reviewable` or `blocked`), guidance, and next steps. The
Markdown formatter renders the same information under `## Policy` so reviewers
can tell which profile was used without reading the raw JSON.

## Policy Shape

```json
{
  "schema_version": "repomori.release_policy.v1",
  "profile": "dev_unsigned",
  "description": "Unsigned OSS or development candidate profile.",
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

## Checked Policy Profiles

RepoMori keeps small policy examples under `tests/fixtures` so the examples are
also executable regression fixtures:

| Profile | File | Intended Use |
| --- | --- | --- |
| Basic workflow default | `release-policy-basic.json` | Candidate workflow gate with checksums, provenance, SBOM, release evidence, and release-check required. Unsigned and signed packages are accepted. |
| Unsigned OSS/dev candidate | `release-policy-dev-unsigned.json` | Public or development candidate review before release signing is configured. It still requires the release evidence bundle and release-check pass. |
| Signed enterprise package | `release-policy-enterprise-signed.json` | Procurement or customer review lane where the complete detached signature set and `repomori-release-public-key.asc` must be present. |
| Strict no-warnings | `release-policy-strict-no-warnings.json` | Final verification lane that rejects any observed release verification or evidence warnings. |

Use a checked profile directly:

```powershell
python -m repomori verify-release D:\Dev\RepoMori\.repomori-release-candidate `
  --policy D:\Dev\RepoMori\tests\fixtures\release-policy-enterprise-signed.json `
  --json
```

Reviewer output includes the selected profile and decision:

```text
## Policy
- Status: `pass`
- Profile: `enterprise_signed`
- Review decision: `reviewable`
- Guidance: Policy gate passed; this candidate is reviewable under the `enterprise_signed` profile.
```

If the decision is `blocked`, do not approve the candidate under that profile
until the listed policy violations are fixed and `verify-release --policy`
passes.

Or pass a profile to the release workflows:

```powershell
gh workflow run release-candidate.yml `
  --repo Martin123132/RepoMori `
  --ref main `
  -f version=0.2.1 `
  -f ref=main `
  -f release_policy=tests/fixtures/release-policy-strict-no-warnings.json
```

## Recommended Use

The `release-candidate` and `publish-release` workflows expose a
`release_policy` input and default it to
`tests/fixtures/release-policy-basic.json`. The policy gate runs after
`release-evidence` is written so it can require release evidence, release-check
status, and schema versions without needing secrets or publishing anything.
When it runs, the workflow emits `release-verify-policy.json` and
`release-verify-policy.md` beside the rest of the release package.

Use the basic policy for candidate review:

```powershell
python -m repomori verify-release D:\Dev\RepoMori\.repomori-release-candidate `
  --policy D:\Dev\RepoMori\tests\fixtures\release-policy-basic.json `
  --format markdown `
  --out D:\Dev\RepoMori\.repomori-release-candidate\release-verify-policy.md
```

Use a stricter company policy when release signing is configured and the public
key fingerprint is already distributed through a trusted channel.
