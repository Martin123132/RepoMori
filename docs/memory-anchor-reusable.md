# Reusable Memory Anchor Workflow

`memory-anchor-reusable.yml` is designed to be called from other repositories.
Use it for a repeatable timeline-anchor check from any repo that includes
`repomori` in its build process.

### Minimal caller workflow

Create `.github/workflows/repomori-anchor.yml` in your target repository:

```yaml
name: repomori-anchor

on:
  schedule:
    - cron: "0 2 * * *"
  workflow_dispatch:
    inputs:
      anchor_mode:
        description: "Anchor freshness profile: strict, safe, or legacy"
        required: false
        default: "strict"
        type: choice
        options:
          - strict
          - safe
          - legacy

jobs:
  repomori_anchor:
    uses: Martin123132/RepoMori/.github/workflows/memory-anchor-reusable.yml@main
    with:
      repo: .
      out_dir: .repomori-packs
      anchor_mode: ${{ github.event.inputs.anchor_mode || 'strict' }}
      python_version: "3.12"
```

### What it does

- Runs `python -m repomori memory` from the target repo
- Writes a timeline anchor artifact in the snapshot directory
- Verifies anchor chain state immediately
- Supports:
  - `strict` (default): fail on mismatch
  - `safe`: continue on mismatch; anchor verification is allowed to report warn
  - `legacy`: check only the anchor proof hash (no full chain head comparison)

### Permissions and setup

The caller repo only needs the checked-out code and Python. No secrets, keys, or
provider config are required.

## Workflow behavior by profile

- `strict`: step fails unless `memory` reports `status == "pass"`.
- `safe`: step fails on `status == "fail"` but allows `status == "warn"` for anchor drift.
- `legacy`: same non-failing behavior as `safe`; useful when timeline-structure changes are
  expected but anchor hash checks should still be recorded.
