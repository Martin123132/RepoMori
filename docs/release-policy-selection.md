# Release Policy Selection Guide

Use this guide before running a release-candidate verification policy. It maps
common review situations to the checked policy profiles in `tests/fixtures` and
keeps the default unsigned development path safe.

## Quick Choice

| Situation | Use Policy Profile | Why |
| --- | --- | --- |
| Default candidate workflow or routine public review | `release-policy-basic.json` | Requires checksums, provenance, SBOM, release evidence, and release-check while accepting unsigned or signed packages. |
| OSS/dev candidate before signing is configured | `release-policy-dev-unsigned.json` | Makes unsigned status explicit while still requiring the release evidence bundle and release-check pass. |
| Customer, procurement, or enterprise verification where signatures are mandatory | `release-policy-enterprise-signed.json` | Requires detached signatures, `repomori-release-public-key.asc`, and signed status. |
| Final release review where warnings should block approval | `release-policy-strict-no-warnings.json` | Rejects release verification or release evidence warnings instead of treating them as reviewable. |

When in doubt, start with `release-policy-basic.json`. Move to
`release-policy-enterprise-signed.json` only when the candidate package contains
the full signature set and reviewers already know how to verify the public key.

## Preflight Checklist

Before choosing a stricter profile, check:

- The release package contains `release-candidate.json`, `checksums.txt`,
  `release-provenance.json`, `sbom.spdx.json`, and release evidence outputs.
- `release-verify-policy.json` and `release-verify-policy.md` are generated
  by the candidate workflow when a policy profile is supplied.
- Signing policy is intentional: unsigned/dev packages should use `basic` or
  `dev_unsigned`; enterprise signed review should use `enterprise_signed`.
- Warning tolerance is intentional: use `strict_no_warnings` only when any
  warning should block approval.
- If a policy blocks, review the diagnostics in
  [release-policy.md](release-policy.md#policy-diagnostics) before switching
  profiles.

## Common Outcomes

| Candidate State | Recommended Profile | Expected Decision |
| --- | --- | --- |
| Complete unsigned development package, no warnings | `release-policy-dev-unsigned.json` | `reviewable` |
| Complete unsigned package under the default workflow gate | `release-policy-basic.json` | `reviewable` |
| Complete unsigned package checked with signed enterprise policy | `release-policy-enterprise-signed.json` | `blocked` until signatures and public key are present. |
| Complete signed package, no warnings | `release-policy-enterprise-signed.json` | `reviewable` |
| Complete signed package with warnings, final no-warning lane | `release-policy-strict-no-warnings.json` | `blocked` until warnings are resolved. |

The full checked outcome table is in
[release-policy-matrix.md](release-policy-matrix.md). The test suite verifies
that these docs and bundled policy fixtures stay aligned with the policy engine.

## Example

```powershell
python -m repomori verify-release D:\Dev\RepoMori\.repomori-release-candidate `
  --policy D:\Dev\RepoMori\tests\fixtures\release-policy-basic.json `
  --format markdown `
  --out D:\Dev\RepoMori\.repomori-release-candidate\release-verify-policy.md
```

For workflow runs, pass the selected profile without changing publishing
behavior:

```powershell
gh workflow run release-candidate.yml `
  --repo Martin123132/RepoMori `
  --ref main `
  -f version=0.2.1 `
  -f ref=main `
  -f release_policy=tests/fixtures/release-policy-basic.json
```

