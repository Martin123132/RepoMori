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
        default: "safe"
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
      anchor_mode: ${{ github.event.inputs.anchor_mode || 'safe' }}
      python_version: "3.12"
```

### What it does

- Runs `python -m repomori memory` from the target repo
- Writes a timeline anchor artifact in the snapshot directory
- Verifies anchor chain state immediately
- Supports:
  - `strict`: fail on mismatch
  - `safe`: continue on mismatch; anchor verification is allowed to report warn
  - `legacy`: check only the anchor proof hash (no full chain head comparison)

### CI smoke check

Use this workflow pattern if you want a repeatable smoke run in repo CI:

```yaml
name: repomori-memory-anchor-smoke

on:
  workflow_dispatch:
    inputs:
      repo:
        default: "."
        required: false
      out_dir:
        default: ".repomori-smoke"
        required: false

jobs:
  smoke:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Run memory anchor profiles
        shell: bash
        run: |
          REPO="${{ inputs.repo || '.' }}"
          BASE_DIR="${{ inputs.out_dir || '.repomori-smoke' }}"
          OUT_DIR="${BASE_DIR}/packs"
          mkdir -p "$BASE_DIR"
          mkdir -p "$OUT_DIR"
          MODES=(strict safe legacy)

          for mode in "${MODES[@]}"; do
            REPORT="${BASE_DIR}/memory-anchor-${mode}.json"
            python -m repomori memory "$REPO" \
              --out-dir "$OUT_DIR" \
              --no-handoff \
              --anchor-out "${OUT_DIR}/timeline-anchor.json" \
              --anchor-freshness "$mode" \
              --anchor-verify \
              --json > "$REPORT"

            python - <<'PY' "$REPORT" "$mode"
import json
import sys
from pathlib import Path

mode = sys.argv[2]
path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
if payload.get("summary", {}).get("anchor_freshness") != mode:
    raise SystemExit(f"anchor_freshness mismatch for mode={mode}")
if payload.get("status") not in {"pass", "warn"}:
    raise SystemExit(f"memory status was {payload.get('status')}")
print(f"{mode}: pass={payload.get('status')}")
PY "$REPORT" "$mode"
          done

          for mode in "${MODES[@]}"; do
            REPORT="${BASE_DIR}/memory-anchor-${mode}.json"
            if [ ! -f "$REPORT" ] || [ ! -s "$REPORT" ]; then
              echo "missing expected report: $REPORT"
              exit 1
            fi
          done
```

The smoke command keeps runs explicit and reproducible:
- `safe` is still non-blocking on anchor drift.
- `strict` is the hard-fail mode for CI-quality enforcement.
- `legacy` is hash-only compare mode for migration-safe checks.

### Permissions and setup

The caller repo only needs the checked-out code and Python. No secrets, keys, or
provider config are required.

## Workflow behavior by profile

- `strict`: step fails unless `memory` reports `status == "pass"`.
- `safe`: step fails on `status == "fail"` but allows `status == "warn"` for anchor drift.
- `legacy`: same non-failing behavior as `safe`; useful when timeline-structure changes are
  expected but anchor hash checks should still be recorded.
