# Release Policy Matrix

This matrix compares the checked release-policy profiles in `tests/fixtures`
against representative candidate states. It is a reviewer aid, not a publishing
workflow. All examples are local and can be reproduced with
`verify-release --policy`.

## Candidate States

| State | Meaning |
| --- | --- |
| `unsigned_clean` | Complete release package and release evidence, no detached signatures, no warnings. |
| `signed_clean` | Complete release package, release evidence, detached signatures, and public key, no warnings. |
| `signed_warning` | Signed package with release evidence warning count greater than zero. |

## Expected Outcomes

| Candidate State | Policy Profile | Review Decision | Diagnostic Outcome | Expected Reason Codes |
| --- | --- | --- | --- | --- |
| `unsigned_clean` | `basic` | `reviewable` | `policy_passed` | none |
| `unsigned_clean` | `dev_unsigned` | `reviewable` | `policy_passed` | none |
| `unsigned_clean` | `enterprise_signed` | `blocked` | `signature_requirements_not_met` | `release_policy_required_file_missing`, `release_policy_signatures_missing`, `release_policy_status_not_allowed` |
| `unsigned_clean` | `strict_no_warnings` | `reviewable` | `policy_passed` | none |
| `signed_clean` | `basic` | `reviewable` | `policy_passed` | none |
| `signed_clean` | `dev_unsigned` | `reviewable` | `policy_passed` | none |
| `signed_clean` | `enterprise_signed` | `reviewable` | `policy_passed` | none |
| `signed_clean` | `strict_no_warnings` | `reviewable` | `policy_passed` | none |
| `signed_warning` | `basic` | `reviewable` | `policy_passed` | none |
| `signed_warning` | `dev_unsigned` | `reviewable` | `policy_passed` | none |
| `signed_warning` | `enterprise_signed` | `reviewable` | `policy_passed` | none |
| `signed_warning` | `strict_no_warnings` | `blocked` | `warning_or_error_threshold_exceeded` | `release_policy_threshold_exceeded` |

## Reviewer Notes

- Use `basic` for the default workflow gate.
- Use `dev_unsigned` when a development or OSS candidate is expected to remain
  unsigned.
- Use `enterprise_signed` when detached signatures and
  `repomori-release-public-key.asc` are mandatory.
- Use `strict_no_warnings` when a final review lane should reject any release
  verification or evidence warnings.

The test suite checks this matrix against the real policy engine and bundled
fixtures so drift in profile behavior is visible before release.
