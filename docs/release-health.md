# Release Health

`release-health` is the consolidated local health bundle for a repository:

- If `--snapshot-dir` is omitted, it reads snapshots from `<repo>/.repomori-packs` by
  default.

- release readiness (scan, schema sanity, optional unit-test/demo steps),
- snapshot health (`doctor`),
- snapshot chain verification (`chain`),
- timeline tail (`timeline`),
- compatibility check (`compat` over the latest pack, schema catalog, agent, and MCP bridge),
- and baseline drift trend (`drift-summary` over the same run log).

It runs offline, dependency-free, and model-free.

```powershell
python -m repomori release-health D:\Dev\RepoMori --snapshot-dir D:\Dev\RepoMori\.repomori-packs --json
python -m repomori release-health D:\Dev\RepoMori --snapshot-dir D:\Dev\RepoMori\.repomori-packs --baseline D:\Dev\RepoMori\.repomori-scan-baseline.json --drift-policy D:\Dev\RepoMori\.repomori-drift-policy.json --json
python -m repomori release-health D:\Dev\RepoMori --snapshot-dir D:\Dev\RepoMori\.repomori-packs --drift-log D:\Dev\RepoMori\.repomori-baseline-drift.jsonl --artifacts-dir D:\Dev\RepoMori\.repomori-health --json
python -m repomori release-health D:\Dev\RepoMori --snapshot-dir D:\Dev\RepoMori\.repomori-packs --compat-handoff D:\handoffs\repo --compat-verify-pack --json
```

Use this command for a predictable post-memory check after CI or after local work:

```powershell
python -m repomori memory D:\Dev\RepoMori --out-dir D:\Dev\RepoMori\.repomori-packs --json
python -m repomori release-health D:\Dev\RepoMori --snapshot-dir D:\Dev\RepoMori\.repomori-packs --drift-policy D:\Dev\RepoMori\.repomori-drift-policy.json --json
```

Output schema is `repomori.health.v1`. The result includes:

- `summary` with each check status and elapsed time
- `checks.release_check` (`repomori.release_check.v1`)
- `checks.doctor` (`repomori.doctor.v1`)
- `checks.chain` (`repomori.snapshot_chain.v1`)
- `checks.timeline` (`repomori.timeline.v1`)
- `checks.drift_summary` (`repomori.baseline_drift_summary.v1`)
- `checks.compat` (`repomori.compat.v1`)
- `artifacts` paths for optional JSON/Markdown output

`compat` does not require a handoff directory during normal `release-health`
runs. Add `--compat-handoff` when you want that handoff validated too, and add
`--compat-verify-pack` when the run should fully verify pack contents rather
than checking only metadata compatibility.
See [Compatibility Runbook](compatibility.md) for failure triage.

## Anchor freshness profile checks

Use `python -m repomori memory ... --anchor-freshness` to run anchor checks inside
health-driven automation:

- `strict`: mismatch sets `memory.status = fail` (hard gate).
- `safe`: mismatch sets `memory.status = warn` and continues.
- `legacy`: compare anchor proof hash only; if timeline drift is intentional or
  migration-safe, this remains non-fatal.

Pair this with `release-health`/`release-check` so each run emits both snapshot
and timeline trend health in one bundle.

## Drift policy

`--drift-policy` points to a small JSON file with optional thresholds:

- `non_strict_ratio.warn-at`
- `non_strict_ratio.fail-at`
- `non_strict_ratio.investigate-at`
- `semi_strict_delta.warn-at`, `semi_strict_delta.fail-at`
- `fallback_delta.warn-at`, `fallback_delta.fail-at`

Policy checks are additive/opt-in and do not change the meaning of
`--fail-on`. `--fail-on` still controls scan severity blocking; policy thresholds
are surfaced in `summary.drift_policy_status` and can still raise `warn` or `fail`
for the `release-health` aggregate.

```json
{
  "non_strict_ratio": { "warn-at": 0.2, "investigate-at": 0.35, "fail-at": 0.9 },
  "semi_strict_delta": { "warn-at": 2, "fail-at": 10 },
  "fallback_delta": { "warn-at": 1, "fail-at": 5 }
}
```

## CI and artifacts

When `--json` is used, `release-check` and `release-health` write stable artifacts to
the artifacts directory:

- `release-check.json` / `release-check.md`
- `release-health.json` / `release-health.md`
- `compat.json` / `compat.md`
- `baseline-drift.jsonl` (if enabled)

Store these in CI so regressions can be reviewed without re-running locally.

```powershell
python -m repomori release-health D:\Dev\RepoMori --snapshot-dir D:\Dev\RepoMori\.repomori-packs --json --artifacts-dir D:\Temp\repomori-health
```
