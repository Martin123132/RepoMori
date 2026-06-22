# RepoMori CLI Reference

Generated from the live `argparse` command surface.

- Schema: `repomori.cli_commands.v1`
- Commands: `50`

## Commands

### `build`

Build a .repomori pack from a repository.

```text
repomori build [--chunk-size CHUNK_SIZE] [--base BASE] [--force] [--json] repo pack
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| repo | argument | yes |  |  |  |
| pack | argument | yes |  |  |  |
| --chunk-size | option | no |  | 262144 |  |
| --base | option | no |  |  | Reuse unchanged file records and chunks from an existing pack. |
| --force | option | no |  | False | Overwrite an existing pack. |
| --json | option | no |  | False | Print JSON output. |

### `demo`

Create and run a complete local quickstart demo.

```text
repomori demo --out OUT [--force] [--question QUESTION] [--chunk-size CHUNK_SIZE] [--json]
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| --out | option | yes |  |  | Directory to write the demo repo and artifacts. |
| --force | option | no |  | False | Overwrite an existing demo output directory. |
| --question | option | no |  | sqlite connect Store | Question used for query, context, and MCP checks. |
| --chunk-size | option | no |  | 262144 |  |
| --json | option | no |  | False | Print demo JSON. |

### `scan`

Scan a repository for public-release and packing risks.

```text
repomori scan [--max-file-bytes MAX_FILE_BYTES] [--include-hidden] [--public-release] [--ignore-code
        IGNORE_CODE] [--baseline BASELINE] [--write-baseline WRITE_BASELINE] [--fail-on
        {info,low,medium,high}] [--json] repo
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| repo | argument | yes |  |  |  |
| --max-file-bytes | option | no |  | 1048576 |  |
| --include-hidden | option | no |  | False | Scan hidden dotfiles and dot-directories. |
| --public-release | option | no |  | False | Check source-available public-release guardrails. |
| --ignore-code | option | no |  |  | Ignore all findings with this code; repeat for more. |
| --baseline | option | no |  |  | Ignore findings listed in a scan baseline JSON file. |
| --write-baseline | option | no |  |  | Write current active findings to a baseline JSON file. |
| --fail-on | option | no | info, low, medium, high | high |  |
| --json | option | no |  | False | Print scan JSON. |

### `release-check`

Run local release readiness checks.

```text
repomori release-check [--baseline BASELINE] [--fail-on {info,low,medium,high}]
        [--no-public-release] [--skip-tests] [--skip-demo] [--demo-out DEMO_OUT] [--keep-demo]
        [--tests-dir TESTS_DIR] [--drift-log DRIFT_LOG] [--drift-policy DRIFT_POLICY]
        [--artifacts-dir ARTIFACTS_DIR] [--json] [repo]
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| repo | argument | no |  |  | Repository folder to check. |
| --baseline | option | no |  |  | Scan baseline; defaults to <repo>/.repomori-scan-baseline.json when present. |
| --fail-on | option | no | info, low, medium, high | low | Exit nonzero if scan findings reach this severity or worse. Baseline drift telemetry is non-blocking by default. |
| --no-public-release | option | no |  | False | Skip public-release guardrail checks in scan. |
| --skip-tests | option | no |  | False | Skip unittest discovery. |
| --skip-demo | option | no |  | False | Skip quickstart demo smoke. |
| --demo-out | option | no |  |  | Demo smoke output directory. |
| --keep-demo | option | no |  | False | Keep demo smoke output directory. |
| --tests-dir | option | no |  | tests | Directory passed to unittest discover. |
| --drift-log | option | no |  |  | Append baseline-drift telemetry as JSONL row. |
| --drift-policy | option | no |  |  | JSON drift policy file for non-blocking policy checks. |
| --artifacts-dir | option | no |  |  | Write release-check artifacts to this directory. |
| --json | option | no |  | False | Print release-check JSON. |

### `release-health`

Run release-check, doctor, chain, timeline, drift-summary, compat, and contract checks.

```text
repomori release-health [--snapshot-dir SNAPSHOT_DIR] [--baseline BASELINE] [--fail-on
        {info,low,medium,high}] [--no-public-release] [--skip-tests] [--skip-demo] [--demo-out
        DEMO_OUT] [--keep-demo] [--tests-dir TESTS_DIR] [--drift-log DRIFT_LOG] [--drift-policy
        DRIFT_POLICY] [--artifacts-dir ARTIFACTS_DIR] [--timeline-limit TIMELINE_LIMIT]
        [--drift-summary-limit DRIFT_SUMMARY_LIMIT] [--doctor-verify-packs] [--compat-handoff
        COMPAT_HANDOFF] [--compat-verify-pack] [--contract-fixture CONTRACT_FIXTURE] [--json] [repo]
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| repo | argument | no |  |  | Repository folder to check. |
| --snapshot-dir | option | no |  |  | Snapshot directory for doctor, chain, and timeline. |
| --baseline | option | no |  |  | Scan baseline; defaults to <repo>/.repomori-scan-baseline.json when present. |
| --fail-on | option | no | info, low, medium, high | low | Exit nonzero if scan findings reach this severity or worse. Baseline drift telemetry remains non-blocking. |
| --no-public-release | option | no |  | False | Skip public-release guardrail checks in scan. |
| --skip-tests | option | no |  | False | Skip unittest discovery. |
| --skip-demo | option | no |  | False | Skip quickstart demo smoke. |
| --demo-out | option | no |  |  | Demo smoke output directory. |
| --keep-demo | option | no |  | False | Keep demo smoke output directory. |
| --tests-dir | option | no |  | tests | Directory passed to unittest discover. |
| --drift-log | option | no |  |  | Append baseline-drift telemetry as JSONL row. |
| --drift-policy | option | no |  |  | JSON drift policy file for non-blocking policy checks. |
| --artifacts-dir | option | no |  |  | Write release-health artifacts to this directory. |
| --timeline-limit | option | no |  | 5 | Recent snapshots to include. |
| --drift-summary-limit | option | no |  | 20 | Rows to include in drift-summary. |
| --doctor-verify-packs | option | no |  | False | Run full pack verification during doctor. |
| --compat-handoff | option | no |  |  | Optional handoff directory for release-health compatibility checks. |
| --compat-verify-pack | option | no |  | False | Run full pack verification during release-health compatibility checks. |
| --contract-fixture | option | no |  |  | Optional contract fixture for release-health contract drift checks. |
| --json | option | no |  | False | Print release-health JSON. |

### `verify-release`

Verify a release package integrity bundle.

```text
repomori verify-release [--format {markdown,json}] [--out OUT] [--json] package_dir
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| package_dir | argument | yes |  |  | Release package directory containing release-candidate.json. |
| --format | option | no | markdown, json | markdown |  |
| --out | option | no |  |  | Write the verification report to this file. |
| --json | option | no |  | False | Print release verification JSON. |

### `release-evidence`

Build a release evidence bundle from local artifacts.

```text
repomori release-evidence [--repo REPO] [--release-check RELEASE_CHECK] [--release-health
        RELEASE_HEALTH] [--out-dir OUT_DIR] [--format {markdown,json}] [--out OUT] [--json]
        package_dir
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| package_dir | argument | yes |  |  | Release package directory containing release-candidate.json. |
| --repo | option | no |  |  | Repository folder associated with the release. |
| --release-check | option | no |  |  | Optional release-check JSON report. |
| --release-health | option | no |  |  | Optional release-health JSON report. |
| --out-dir | option | no |  |  | Write release-evidence.json and release-evidence.md to this directory. |
| --format | option | no | markdown, json | markdown |  |
| --out | option | no |  |  | Write the selected evidence report format to this file. |
| --json | option | no |  | False | Print release evidence JSON. |

### `init`

Write a RepoMori config file.

```text
repomori init --out-dir OUT_DIR [--config CONFIG] [--profile PROFILE] [--force] [--handoff-question
        HANDOFF_QUESTION] [--no-handoff] [--keep KEEP] [--prune-apply] [--verify-packs]
        [--timeline-limit TIMELINE_LIMIT] [--chunk-size CHUNK_SIZE] [--incremental]
        [--no-incremental] [--no-compare] [--compare-limit COMPARE_LIMIT] [--anchor-freshness
        {safe,strict,legacy}] [--diff-context] [--diff-context-question DIFF_CONTEXT_QUESTION]
        [--diff-context-max-files DIFF_CONTEXT_MAX_FILES] [--diff-context-snippet-lines
        DIFF_CONTEXT_SNIPPET_LINES] [--diff-context-snippets-per-file
        DIFF_CONTEXT_SNIPPETS_PER_FILE] [--diff-context-max-bytes DIFF_CONTEXT_MAX_BYTES]
        [--diff-context-no-source] [--json] repo
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| repo | argument | yes |  |  | Repository folder to remember. |
| --out-dir | option | yes |  |  | Directory for snapshot packs and reports. |
| --config | option | no |  |  | Config file path; defaults to <repo>/repomori.toml. |
| --profile | option | no |  | default | Profile name to write. |
| --force | option | no |  | False | Overwrite an existing config file. |
| --handoff-question | option | no |  | continue this repo |  |
| --no-handoff | option | no |  | False | Skip default handoffs in this profile. |
| --keep | option | no |  | 20 | Newest snapshots to keep in addition to latest. |
| --prune-apply | option | no |  | False | Apply safe prune in this profile. |
| --verify-packs | option | no |  | False | Run full pack verification during doctor. |
| --timeline-limit | option | no |  | 5 | Recent snapshots to return. |
| --chunk-size | option | no |  | 262144 |  |
| --incremental | option | no |  | True | Reuse the latest pack as a memory base when available. |
| --no-incremental | option | no |  | True | Rebuild snapshot packs without reusing latest pack state. |
| --no-compare | option | no |  | False | Do not compare against latest.repomori. |
| --compare-limit | option | no |  | 50 |  |
| --anchor-freshness | option | no | safe, strict, legacy |  | Anchor freshness mode for memory anchor verification. |
| --diff-context | option | no |  | False | Write changed-files context during memory runs. |
| --diff-context-question | option | no |  | what changed? |  |
| --diff-context-max-files | option | no |  | 8 |  |
| --diff-context-snippet-lines | option | no |  | 12 |  |
| --diff-context-snippets-per-file | option | no |  | 2 |  |
| --diff-context-max-bytes | option | no |  | 8192 |  |
| --diff-context-no-source | option | no |  | False | Configure diff context without exact snippets. |
| --json | option | no |  | False | Print config init JSON. |

### `snapshot`

Build a timestamped pack snapshot.

```text
repomori snapshot --out-dir OUT_DIR [--chunk-size CHUNK_SIZE] [--incremental] [--no-incremental]
        [--no-compare] [--compare-limit COMPARE_LIMIT] [--handoff HANDOFF] [--handoff-out
        HANDOFF_OUT] [--handoff-force] [--json] repo
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| repo | argument | yes |  |  |  |
| --out-dir | option | yes |  |  | Directory for snapshot packs and reports. |
| --chunk-size | option | no |  | 262144 |  |
| --incremental | option | no |  | True | Reuse the latest pack as a base when available. |
| --no-incremental | option | no |  | True | Rebuild every file instead of reusing previous pack state. |
| --no-compare | option | no |  | False | Do not compare against latest.repomori. |
| --compare-limit | option | no |  | 50 |  |
| --handoff | option | no |  |  | Build a handoff package for this snapshot using this question. |
| --handoff-out | option | no |  |  | Directory for the snapshot handoff package. |
| --handoff-force | option | no |  | False | Overwrite an existing snapshot handoff. |
| --json | option | no |  | False | Print snapshot JSON. |

### `timeline`

Read a snapshot index timeline.

```text
repomori timeline [--limit LIMIT] [--format {markdown,json}] [--out OUT] out_dir
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| out_dir | argument | yes |  |  |  |
| --limit | option | no |  |  | Maximum recent snapshots to return. |
| --format | option | no | markdown, json | markdown |  |
| --out | option | no |  |  | Write the timeline report to this file. |

### `timeline-search`

Query indexed snapshot packs for a path, symbol, or concept.

```text
repomori timeline-search [--limit LIMIT] [--per-snapshot-limit PER_SNAPSHOT_LIMIT] [--format
        {markdown,json}] [--out OUT] [--json] out_dir text
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| out_dir | argument | yes |  |  |  |
| text | argument | yes |  |  |  |
| --limit | option | no |  | 10 | Maximum matching snapshots to return. |
| --per-snapshot-limit | option | no |  | 3 | Maximum query hits per snapshot. |
| --format | option | no | markdown, json | markdown |  |
| --out | option | no |  |  | Write the timeline search report to this file. |
| --json | option | no |  | False | Print JSON output. |

### `drift-summary`

Summarize baseline drift telemetry from a JSONL log.

```text
repomori drift-summary [--limit LIMIT] [--json] log
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| log | argument | yes |  |  | Path to a baseline-drift JSONL log. |
| --limit | option | no |  | 20 | Only analyze the newest N rows. |
| --json | option | no |  | False | Print JSON output. |

### `handoff-health-summary`

Summarize handoff-health telemetry from a JSONL log.

```text
repomori handoff-health-summary [--limit LIMIT] [--format {markdown,json}] [--out OUT] [--json] log
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| log | argument | yes |  |  | Path to a handoff-health JSONL log. |
| --limit | option | no |  | 20 | Only analyze the newest N rows. |
| --format | option | no | markdown, json | markdown |  |
| --out | option | no |  |  | Write the summary report to a file. |
| --json | option | no |  | False | Print JSON output. |

### `stats`

Read snapshot reuse and storage statistics.

```text
repomori stats [--limit LIMIT] [--format {markdown,json}] [--out OUT] out_dir
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| out_dir | argument | yes |  |  |  |
| --limit | option | no |  | 10 | Maximum recent and top-reuse snapshots to return. |
| --format | option | no | markdown, json | markdown |  |
| --out | option | no |  |  | Write the stats report to this file. |

### `chain`

Verify snapshot timeline hash chain.

```text
repomori chain [--format {markdown,json}] [--out OUT] [--json] out_dir
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| out_dir | argument | yes |  |  |  |
| --format | option | no | markdown, json | markdown |  |
| --out | option | no |  |  | Write the chain report to this file. |
| --json | option | no |  | False | Print chain JSON. |

### `anchor`

Export a snapshot timeline anchor proof.

```text
repomori anchor [--format {json,markdown}] [--out OUT] [--json] out_dir
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| out_dir | argument | yes |  |  |  |
| --format | option | no | json, markdown | json |  |
| --out | option | no |  |  | Write the anchor proof to this file. |
| --json | option | no |  | False | Print anchor JSON. |

### `verify-anchor`

Verify a snapshot timeline anchor proof.

```text
repomori verify-anchor [--no-current] [--format {markdown,json}] [--out OUT] [--json] anchor
        [out_dir]
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| anchor | argument | yes |  |  | Anchor JSON file to verify. |
| out_dir | argument | no |  |  | Snapshot directory to compare against; defaults to anchor out_dir. |
| --no-current | option | no |  | False | Only verify the anchor proof hash, not the current snapshot timeline. |
| --format | option | no | markdown, json | markdown |  |
| --out | option | no |  |  | Write the verification report to this file. |
| --json | option | no |  | False | Print verification JSON. |

### `doctor`

Check snapshot directory health.

```text
repomori doctor [--verify-packs] [--json] [--out OUT] out_dir
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| out_dir | argument | yes |  |  |  |
| --verify-packs | option | no |  | False | Run full pack verification for indexed packs. |
| --json | option | no |  | False | Print doctor JSON. |
| --out | option | no |  |  | Write the doctor report to this file. |

### `prune`

Plan or apply safe snapshot cleanup.

```text
repomori prune [--keep KEEP] [--apply] [--json] out_dir
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| out_dir | argument | yes |  |  |  |
| --keep | option | no |  | 20 | Newest snapshots to keep in addition to latest. |
| --apply | option | no |  | False | Delete planned in-dir artifacts and update snapshots.json. |
| --json | option | no |  | False | Print prune JSON. |

### `memory`

Run snapshot, handoff, doctor, prune, and timeline.

```text
repomori memory [--out-dir OUT_DIR] [--config CONFIG] [--profile PROFILE] [--anchor-out ANCHOR_OUT]
        [--anchor-verify] [--anchor-freshness {safe,strict,legacy}] [--allow-unverified-anchor]
        [--anchor-log ANCHOR_LOG] [--handoff-question HANDOFF_QUESTION] [--no-handoff]
        [--with-handoff] [--handoff-quality-profile {safe,ci,strict}] [--handoff-quality-target
        HANDOFF_QUALITY_TARGET] [--keep KEEP] [--prune-apply] [--prune-dry-run] [--verify-packs]
        [--no-verify-packs] [--timeline-limit TIMELINE_LIMIT] [--chunk-size CHUNK_SIZE]
        [--incremental] [--no-incremental] [--no-compare] [--compare] [--compare-limit
        COMPARE_LIMIT] [--diff-context] [--no-diff-context] [--diff-context-question
        DIFF_CONTEXT_QUESTION] [--diff-context-max-files DIFF_CONTEXT_MAX_FILES]
        [--diff-context-snippet-lines DIFF_CONTEXT_SNIPPET_LINES] [--diff-context-snippets-per-file
        DIFF_CONTEXT_SNIPPETS_PER_FILE] [--diff-context-max-bytes DIFF_CONTEXT_MAX_BYTES]
        [--diff-context-source] [--diff-context-no-source] [--json] [repo]
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| repo | argument | no |  |  | Repository folder; falls back to repomori.toml. |
| --out-dir | option | no |  |  | Directory for snapshot packs and reports. |
| --config | option | no |  |  | Config file path; defaults to nearest repomori.toml. |
| --profile | option | no |  |  | Config profile to use. |
| --anchor-out | option | no |  |  | Write a timeline anchor to this file. |
| --anchor-verify | option | no |  |  | Verify the exported anchor against current timeline. |
| --anchor-freshness | option | no | safe, strict, legacy |  | Anchor freshness profile: strict = fail on mismatch, safe = allow mismatch, legacy = proof-only validation. |
| --allow-unverified-anchor | option | no |  |  | Allow memory runs to continue when anchor verification fails. |
| --anchor-log | option | no |  |  | Append one anchor audit row per memory run. |
| --handoff-question | option | no |  |  |  |
| --no-handoff | option | no |  |  | Skip the default snapshot handoff package. |
| --with-handoff | option | no |  | True | Force handoff even if config disables it. |
| --handoff-quality-profile | option | no | safe, ci, strict |  | Evaluate generated handoff quality and warn/fail by profile. |
| --handoff-quality-target | option | no |  |  | Override the selected handoff quality target score. |
| --keep | option | no |  |  | Newest snapshots to keep in addition to latest. |
| --prune-apply | option | no |  |  | Apply safe prune after the snapshot. |
| --prune-dry-run | option | no |  | True | Force prune dry-run even if config applies it. |
| --verify-packs | option | no |  |  | Run full pack verification during doctor. |
| --no-verify-packs | option | no |  | True | Skip full pack verification during doctor. |
| --timeline-limit | option | no |  |  | Recent snapshots to return. |
| --chunk-size | option | no |  |  |  |
| --incremental | option | no |  |  | Reuse the latest pack as a memory base when available. |
| --no-incremental | option | no |  | True | Rebuild snapshot packs without reusing latest pack state. |
| --no-compare | option | no |  |  | Do not compare against latest.repomori. |
| --compare | option | no |  | False | Compare against latest.repomori. |
| --compare-limit | option | no |  |  |  |
| --diff-context | option | no |  |  | Write changed-files context beside snapshot reports. |
| --no-diff-context | option | no |  | True | Skip diff-context even if config enables it. |
| --diff-context-question | option | no |  |  |  |
| --diff-context-max-files | option | no |  |  |  |
| --diff-context-snippet-lines | option | no |  |  |  |
| --diff-context-snippets-per-file | option | no |  |  |  |
| --diff-context-max-bytes | option | no |  |  |  |
| --diff-context-source | option | no |  |  | Include exact diff-context snippets. |
| --diff-context-no-source | option | no |  | True | Write diff-context metadata without snippets. |
| --json | option | no |  | False | Print memory JSON. |

### `agent`

Run the JSON-lines agent bridge on stdio.

```text
repomori agent [--config CONFIG] [--profile PROFILE]
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| --config | option | no |  |  | Config file path; defaults to nearest repomori.toml. |
| --profile | option | no |  |  | Config profile to use. |

### `mcp`

Run the dependency-free MCP stdio bridge.

```text
repomori mcp [--config CONFIG] [--profile PROFILE]
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| --config | option | no |  |  | Config file path; defaults to nearest repomori.toml. |
| --profile | option | no |  |  | Config profile to use. |

### `schema`

Show supported RepoMori schemas and agent methods.

```text
repomori schema [--json] [schema_version]
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| schema_version | argument | no |  |  | Specific schema version to show. |
| --json | option | no |  | False | Print schema JSON. |

### `commands`

Show the CLI command inventory and generated reference.

```text
repomori commands [--format {markdown,json}] [--out OUT] [--json]
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| --format | option | no | markdown, json | markdown |  |
| --out | option | no |  |  | Write the command reference to this file. |
| --json | option | no |  | False | Print JSON output. |

### `compat`

Check pack, handoff, schema, agent, and MCP compatibility.

```text
repomori compat [--handoff HANDOFF] [--snapshot-dir SNAPSHOT_DIR] [--verify-pack] [--format
        {markdown,json}] [--out OUT] [--json] [pack]
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| pack | argument | no |  |  | Pack to check; defaults to latest pack from --snapshot-dir when supplied. |
| --handoff | option | no |  |  | Optional handoff directory to validate against current contracts. |
| --snapshot-dir | option | no |  |  | Snapshot directory used to resolve the latest pack. |
| --verify-pack | option | no |  | False | Run full pack verification during compatibility checks. |
| --format | option | no | markdown, json | markdown |  |
| --out | option | no |  |  | Write the compatibility report to this file. |
| --json | option | no |  | False | Print JSON output. |

### `contract-check`

Compare current schema, agent, and MCP contracts with a fixture.

```text
repomori contract-check [--fixture FIXTURE] [--format {markdown,json}] [--out OUT] [--json]
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| --fixture | option | no |  | tests/fixtures/compat-contracts.json | Contract fixture JSON file. |
| --format | option | no | markdown, json | markdown |  |
| --out | option | no |  |  | Write the contract diff report to this file. |
| --json | option | no |  | False | Print JSON output. |

### `info`

Show pack metadata.

```text
repomori info [--json] pack
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| pack | argument | yes |  |  |  |
| --json | option | no |  | False |  |

### `inspect`

Inspect pack contents, storage, indexes, and vocabulary.

```text
repomori inspect [--max-files MAX_FILES] [--top-terms TOP_TERMS] [--top-symbols TOP_SYMBOLS]
        [--verify] [--format {markdown,json}] [--out OUT] [--json] pack
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| pack | argument | yes |  |  |  |
| --max-files | option | no |  | 20 |  |
| --top-terms | option | no |  | 30 |  |
| --top-symbols | option | no |  | 30 |  |
| --verify | option | no |  | False | Run full pack verification during inspection. |
| --format | option | no | markdown, json | markdown |  |
| --out | option | no |  |  | Write the inspection report to this file. |
| --json | option | no |  | False | Alias for --format json. |

### `inspect-diff`

Inspect structural changes between two packs.

```text
repomori inspect-diff [--max-files MAX_FILES] [--top-terms TOP_TERMS] [--top-symbols TOP_SYMBOLS]
        [--verify] [--format {markdown,json}] [--out OUT] [--json] base_pack target_pack
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| base_pack | argument | yes |  |  |  |
| target_pack | argument | yes |  |  |  |
| --max-files | option | no |  | 20 |  |
| --top-terms | option | no |  | 30 |  |
| --top-symbols | option | no |  | 30 |  |
| --verify | option | no |  | False | Run full verification for both packs during diff inspection. |
| --format | option | no | markdown, json | markdown |  |
| --out | option | no |  |  | Write the inspect-diff report to this file. |
| --json | option | no |  | False | Alias for --format json. |

### `tree`

List files stored in a pack.

```text
repomori tree [--limit LIMIT] [--json] pack
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| pack | argument | yes |  |  |  |
| --limit | option | no |  | 200 |  |
| --json | option | no |  | False |  |

### `query`

Search the machine-readable pack index.

```text
repomori query [--limit LIMIT] [--json] pack text
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| pack | argument | yes |  |  |  |
| text | argument | yes |  |  |  |
| --limit | option | no |  | 10 |  |
| --json | option | no |  | False |  |

### `diagnose`

Explain query ranking and snippet selection.

```text
repomori diagnose [--limit LIMIT] [--max-files MAX_FILES] [--snippet-lines SNIPPET_LINES]
        [--snippets-per-file SNIPPETS_PER_FILE] [--max-bytes MAX_BYTES] [--json] pack question
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| pack | argument | yes |  |  |  |
| question | argument | yes |  |  |  |
| --limit | option | no |  | 8 |  |
| --max-files | option | no |  |  | Alias for --limit. |
| --snippet-lines | option | no |  | 12 |  |
| --snippets-per-file | option | no |  | 2 |  |
| --max-bytes | option | no |  |  | Maximum total snippet text bytes. |
| --json | option | no |  | False |  |

### `compare`

Compare two .repomori packs.

```text
repomori compare [--limit LIMIT] [--include-unchanged] [--format {markdown,json}] [--out OUT]
        base_pack target_pack
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| base_pack | argument | yes |  |  |  |
| target_pack | argument | yes |  |  |  |
| --limit | option | no |  | 50 |  |
| --include-unchanged | option | no |  | False |  |
| --format | option | no | markdown, json | markdown |  |
| --out | option | no |  |  | Write the comparison report to this file. |

### `brief`

Build a pack orientation brief or snapshot-directory agent brief.

```text
repomori brief [--max-files MAX_FILES] [--top-terms TOP_TERMS] [--top-symbols TOP_SYMBOLS]
        [--timeline-limit TIMELINE_LIMIT] [--stats-limit STATS_LIMIT] [--verify-packs] [--format
        {markdown,json}] [--out OUT] target
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| target | argument | yes |  |  |  |
| --max-files | option | no |  | 12 |  |
| --top-terms | option | no |  | 40 |  |
| --top-symbols | option | no |  | 40 |  |
| --timeline-limit | option | no |  | 5 | Snapshot-directory mode: recent snapshots to include. |
| --stats-limit | option | no |  | 10 | Snapshot-directory mode: reuse stats rows to include. |
| --verify-packs | option | no |  | False | Snapshot-directory mode: run full pack verification during doctor. |
| --format | option | no | markdown, json | markdown |  |
| --out | option | no |  |  | Write the brief to this file. |

### `context`

Build source-backed agent context.

```text
repomori context [--limit LIMIT] [--max-files MAX_FILES] [--snippet-lines SNIPPET_LINES]
        [--snippets-per-file SNIPPETS_PER_FILE] [--max-bytes MAX_BYTES] [--no-source] [--format
        {markdown,json}] [--out OUT] pack question
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| pack | argument | yes |  |  |  |
| question | argument | yes |  |  |  |
| --limit | option | no |  | 8 |  |
| --max-files | option | no |  |  | Alias for --limit. |
| --snippet-lines | option | no |  | 12 |  |
| --snippets-per-file | option | no |  | 2 |  |
| --max-bytes | option | no |  |  | Maximum total snippet text bytes. |
| --no-source | option | no |  | False | Return rankings and metadata without snippets. |
| --format | option | no | markdown, json | markdown |  |
| --out | option | no |  |  | Write the context bundle to this file. |

### `diff-context`

Build source-backed changed-files context.

```text
repomori diff-context [--limit LIMIT] [--max-files MAX_FILES] [--snippet-lines SNIPPET_LINES]
        [--snippets-per-file SNIPPETS_PER_FILE] [--max-bytes MAX_BYTES] [--no-source] [--format
        {markdown,json}] [--out OUT] base_pack target_pack [question]
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| base_pack | argument | yes |  |  |  |
| target_pack | argument | yes |  |  |  |
| question | argument | no |  | what changed? |  |
| --limit | option | no |  | 8 |  |
| --max-files | option | no |  |  | Alias for --limit. |
| --snippet-lines | option | no |  | 12 |  |
| --snippets-per-file | option | no |  | 2 |  |
| --max-bytes | option | no |  |  | Maximum total snippet text bytes. |
| --no-source | option | no |  | False | Return change metadata without snippets. |
| --format | option | no | markdown, json | markdown |  |
| --out | option | no |  |  | Write the diff context bundle to this file. |

### `verify`

Verify pack chunks, hashes, and source recovery.

```text
repomori verify [--json] pack
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| pack | argument | yes |  |  |  |
| --json | option | no |  | False |  |

### `eval`

Evaluate context usefulness for a pack.

```text
repomori eval [--question QUESTION] [--questions-file QUESTIONS_FILE] [--limit LIMIT] [--max-files
        MAX_FILES] [--snippet-lines SNIPPET_LINES] [--snippets-per-file SNIPPETS_PER_FILE]
        [--max-bytes MAX_BYTES] [--no-source] [--format {markdown,json}] [--out OUT] pack
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| pack | argument | yes |  |  |  |
| --question | option | no |  |  | Question to evaluate; repeat for more. |
| --questions-file | option | no |  |  | Read one eval question per line. |
| --limit | option | no |  | 5 |  |
| --max-files | option | no |  |  | Alias for --limit. |
| --snippet-lines | option | no |  | 10 |  |
| --snippets-per-file | option | no |  | 2 |  |
| --max-bytes | option | no |  | 4096 | Maximum snippet text bytes per question. |
| --no-source | option | no |  | False | Evaluate rankings and metadata without snippets. |
| --format | option | no | markdown, json | markdown |  |
| --out | option | no |  |  | Write the eval report to this file. |

### `context-eval`

Run fixture-backed context quality cases.

```text
repomori context-eval --cases CASES [--limit LIMIT] [--max-files MAX_FILES] [--snippet-lines
        SNIPPET_LINES] [--snippets-per-file SNIPPETS_PER_FILE] [--max-bytes MAX_BYTES] [--no-source]
        [--format {markdown,json}] [--out OUT] [--json] pack
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| pack | argument | yes |  |  |  |
| --cases | option | yes |  |  | JSON file with context eval cases. |
| --limit | option | no |  | 8 |  |
| --max-files | option | no |  |  | Alias for --limit. |
| --snippet-lines | option | no |  | 12 |  |
| --snippets-per-file | option | no |  | 2 |  |
| --max-bytes | option | no |  | 4096 | Maximum snippet text bytes per case. |
| --no-source | option | no |  | False | Evaluate rankings and metadata without snippets. |
| --format | option | no | markdown, json | markdown |  |
| --out | option | no |  |  | Write the context quality report to this file. |
| --json | option | no |  | False | Print JSON output. |

### `capsule`

Export a dense machine-readable capsule.

```text
repomori capsule [--max-files MAX_FILES] [--top-terms TOP_TERMS] [--out OUT] pack
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| pack | argument | yes |  |  |  |
| --max-files | option | no |  |  | Maximum files to include. |
| --top-terms | option | no |  | 128 | Vocabulary terms to include. |
| --out | option | no |  |  | Write capsule JSON to this file. |

### `handoff`

Build an agent handoff package directory.

```text
repomori handoff --out OUT [--base-pack BASE_PACK] [--force] [--copy-pack] [--allow-unverified]
        [--max-files MAX_FILES] [--max-bytes MAX_BYTES] [--snippet-lines SNIPPET_LINES]
        [--snippets-per-file SNIPPETS_PER_FILE] [--capsule-max-files CAPSULE_MAX_FILES] [--top-terms
        TOP_TERMS] [--eval-question EVAL_QUESTION] [--questions-file QUESTIONS_FILE] [--json] pack
        question
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| pack | argument | yes |  |  |  |
| question | argument | yes |  |  |  |
| --out | option | yes |  |  | Directory to write handoff artifacts. |
| --base-pack | option | no |  |  | Previous pack to compare against. |
| --force | option | no |  | False | Overwrite an existing handoff directory. |
| --copy-pack | option | no |  | False | Copy the .repomori pack into the handoff. |
| --allow-unverified | option | no |  | False | Continue when pack verification fails. |
| --max-files | option | no |  | 8 |  |
| --max-bytes | option | no |  |  | Maximum total snippet text bytes. |
| --snippet-lines | option | no |  | 12 |  |
| --snippets-per-file | option | no |  | 2 |  |
| --capsule-max-files | option | no |  |  |  |
| --top-terms | option | no |  | 128 |  |
| --eval-question | option | no |  |  | Extra eval question; repeat for more. |
| --questions-file | option | no |  |  | Read extra eval questions, one per line. |
| --json | option | no |  | False | Print manifest JSON. |

### `check-handoff`

Validate a handoff package directory.

```text
repomori check-handoff [--json] handoff_dir
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| handoff_dir | argument | yes |  |  |  |
| --json | option | no |  | False |  |

### `score-handoff`

Score a handoff package for agent usefulness.

```text
repomori score-handoff [--format {markdown,json}] [--out OUT] [--json] handoff_dir
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| handoff_dir | argument | yes |  |  |  |
| --format | option | no | markdown, json | markdown |  |
| --out | option | no |  |  | Write the score report to a file. |
| --json | option | no |  | False | Print JSON output. |

### `handoff-triage`

Turn a handoff score into a prioritized fix checklist.

```text
repomori handoff-triage [--limit LIMIT] [--format {markdown,json}] [--out OUT] [--json]
        score_or_handoff
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| score_or_handoff | argument | yes |  |  | handoff-score.json or a handoff directory. |
| --limit | option | no |  | 8 | Maximum checklist items. |
| --format | option | no | markdown, json | markdown |  |
| --out | option | no |  |  | Write the triage report to a file. |
| --json | option | no |  | False | Print JSON output. |

### `handoff-quality`

Apply a safe/ci/strict quality gate to a handoff score.

```text
repomori handoff-quality [--profile {safe,ci,strict}] [--target-score TARGET_SCORE] [--format
        {markdown,json}] [--out OUT] [--json] score_or_handoff
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| score_or_handoff | argument | yes |  |  | handoff-score.json or a handoff directory. |
| --profile | option | no | safe, ci, strict | safe |  |
| --target-score | option | no |  |  | Override the profile target score. |
| --format | option | no | markdown, json | markdown |  |
| --out | option | no |  |  | Write the quality report to a file. |
| --json | option | no |  | False | Print JSON output. |

### `improve-handoff`

Build, score, triage, and retry a handoff with richer local settings.

```text
repomori improve-handoff --out OUT [--base-pack BASE_PACK] [--force] [--copy-pack]
        [--allow-unverified] [--target-score TARGET_SCORE] [--quality-profile {safe,ci,strict}]
        [--max-attempts MAX_ATTEMPTS] [--max-files MAX_FILES] [--max-bytes MAX_BYTES]
        [--snippet-lines SNIPPET_LINES] [--snippets-per-file SNIPPETS_PER_FILE] [--capsule-max-files
        CAPSULE_MAX_FILES] [--top-terms TOP_TERMS] [--eval-question EVAL_QUESTION] [--questions-file
        QUESTIONS_FILE] [--format {markdown,json}] [--json] pack question
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| pack | argument | yes |  |  |  |
| question | argument | yes |  |  |  |
| --out | option | yes |  |  | Directory to write the improved handoff. |
| --base-pack | option | no |  |  | Previous pack to compare against. |
| --force | option | no |  | False | Overwrite an existing improved handoff directory. |
| --copy-pack | option | no |  | False | Copy the .repomori pack into the final handoff. |
| --allow-unverified | option | no |  | False | Continue when pack verification fails. |
| --target-score | option | no |  | 90.0 |  |
| --quality-profile | option | no | safe, ci, strict | ci |  |
| --max-attempts | option | no |  | 3 |  |
| --max-files | option | no |  | 8 |  |
| --max-bytes | option | no |  | 4096 |  |
| --snippet-lines | option | no |  | 12 |  |
| --snippets-per-file | option | no |  | 2 |  |
| --capsule-max-files | option | no |  |  |  |
| --top-terms | option | no |  | 128 |  |
| --eval-question | option | no |  |  | Extra eval question; repeat for more. |
| --questions-file | option | no |  |  | Read extra eval questions, one per line. |
| --format | option | no | markdown, json | markdown |  |
| --json | option | no |  | False | Print JSON output. |

### `archive-handoff`

Write a portable zip archive for a handoff directory.

```text
repomori archive-handoff [--out OUT] [--force] [--quality-profile {safe,ci,strict}] [--format
        {markdown,json}] [--report-out REPORT_OUT] [--json] handoff_dir
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| handoff_dir | argument | yes |  |  |  |
| --out | option | no |  |  | Archive path; defaults to sibling .zip. |
| --force | option | no |  | False | Overwrite an existing archive. |
| --quality-profile | option | no | safe, ci, strict | safe |  |
| --format | option | no | markdown, json | markdown |  |
| --report-out | option | no |  |  | Write the archive report to a file. |
| --json | option | no |  | False | Print JSON output. |

### `handoff-health`

Run handoff check, score, triage, quality, and optional repair/archive.

```text
repomori handoff-health [--profile {safe,ci,strict}] [--target-score TARGET_SCORE] [--improve-pack
        IMPROVE_PACK] [--question QUESTION] [--improve-out IMPROVE_OUT] [--base-pack BASE_PACK]
        [--force] [--copy-pack] [--allow-unverified] [--archive] [--archive-out ARCHIVE_OUT]
        [--max-attempts MAX_ATTEMPTS] [--max-files MAX_FILES] [--max-bytes MAX_BYTES]
        [--snippet-lines SNIPPET_LINES] [--snippets-per-file SNIPPETS_PER_FILE] [--capsule-max-files
        CAPSULE_MAX_FILES] [--top-terms TOP_TERMS] [--eval-question EVAL_QUESTION] [--questions-file
        QUESTIONS_FILE] [--artifacts-dir ARTIFACTS_DIR] [--health-log HEALTH_LOG] [--format
        {markdown,json}] [--out OUT] [--json] handoff_dir
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| handoff_dir | argument | yes |  |  |  |
| --profile | option | no | safe, ci, strict | safe |  |
| --target-score | option | no |  |  | Override the profile target score. |
| --improve-pack | option | no |  |  | Pack to use when the handoff needs a local improvement pass. |
| --question | option | no |  |  | Question for improvement; defaults to manifest question when available. |
| --improve-out | option | no |  |  | Directory for an improved handoff; defaults beside the input handoff. |
| --base-pack | option | no |  |  | Previous pack to compare against during improvement. |
| --force | option | no |  | False | Overwrite generated improvement or archive outputs. |
| --copy-pack | option | no |  | False | Copy the pack into improved handoffs. |
| --allow-unverified | option | no |  | False | Continue improvement when pack verification fails. |
| --archive | option | no |  | False | Archive the active handoff after health evaluation. |
| --archive-out | option | no |  |  | Archive path; defaults to active handoff sibling .zip. |
| --max-attempts | option | no |  | 3 |  |
| --max-files | option | no |  | 8 |  |
| --max-bytes | option | no |  | 4096 |  |
| --snippet-lines | option | no |  | 12 |  |
| --snippets-per-file | option | no |  | 2 |  |
| --capsule-max-files | option | no |  |  |  |
| --top-terms | option | no |  | 128 |  |
| --eval-question | option | no |  |  | Extra eval question for improvement; repeat for more. |
| --questions-file | option | no |  |  | Read extra improvement eval questions, one per line. |
| --artifacts-dir | option | no |  |  | Write handoff-health.json and handoff-health.md to this directory. |
| --health-log | option | no |  |  | Append one compact handoff-health trend row to this JSONL log. |
| --format | option | no | markdown, json | markdown |  |
| --out | option | no |  |  | Write the selected health report format to a file. |
| --json | option | no |  | False | Print JSON output. |

### `bench`

Run an end-to-end repository benchmark.

```text
repomori bench --out OUT [--question QUESTION] [--force] [--chunk-size CHUNK_SIZE] [--max-files
        MAX_FILES] [--max-bytes MAX_BYTES] [--snippet-lines SNIPPET_LINES] [--snippets-per-file
        SNIPPETS_PER_FILE] [--capsule-max-files CAPSULE_MAX_FILES] [--top-terms TOP_TERMS]
        [--eval-question EVAL_QUESTION] [--questions-file QUESTIONS_FILE] [--copy-pack] [--json]
        repo
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| repo | argument | yes |  |  |  |
| --out | option | yes |  |  | Directory to write benchmark artifacts. |
| --question | option | no |  | How should an agent understand and continue this repository? |  |
| --force | option | no |  | False | Overwrite an existing benchmark directory. |
| --chunk-size | option | no |  | 262144 |  |
| --max-files | option | no |  | 8 |  |
| --max-bytes | option | no |  | 4096 |  |
| --snippet-lines | option | no |  | 12 |  |
| --snippets-per-file | option | no |  | 2 |  |
| --capsule-max-files | option | no |  |  |  |
| --top-terms | option | no |  | 128 |  |
| --eval-question | option | no |  |  | Extra eval question; repeat for more. |
| --questions-file | option | no |  |  | Read extra eval questions, one per line. |
| --copy-pack | option | no |  | False | Copy the pack into the handoff. |
| --json | option | no |  | False | Print benchmark JSON. |

### `get`

Restore one exact file from the pack.

```text
repomori get [--out OUT] pack path
```

| Name | Kind | Required | Choices | Default | Help |
| --- | --- | --- | --- | --- | --- |
| pack | argument | yes |  |  |  |
| path | argument | yes |  |  |  |
| --out | option | no |  |  | Write restored bytes to this file. |
