# Snapshot Backup And Restore

RepoMori snapshot memory is local file state. Backing it up means copying the
snapshot output directory and any external anchor records you rely on.

Use hidden snapshot directories for routine automation:

```powershell
python -m repomori memory D:\Dev\YourRepo --out-dir D:\Dev\YourRepo\.repomori-packs --anchor-out D:\Dev\YourRepo\.repomori-packs\timeline-anchor.json --anchor-freshness safe --anchor-verify --json
```

## What To Back Up

Copy the whole snapshot directory, not only `latest.repomori`.

Minimum useful contents:

- `snapshots.json`
- `latest.repomori`
- indexed `.repomori` snapshot packs
- indexed snapshot, compare, inspect-diff, diff-context, and handoff artifacts
- exported anchors or anchor logs if you store them outside the snapshot directory

If you use external anchor logs, keep a copy away from the mutable snapshot
directory. Anchors are tamper-evidence records, not encryption or signatures.

## Restore Check

After restoring a snapshot directory, run:

```powershell
python -m repomori restore-check D:\Restores\YourRepo\.repomori-packs --json
```

For a stronger local check, verify every indexed pack and compare an exported
anchor:

```powershell
python -m repomori restore-check D:\Restores\YourRepo\.repomori-packs --verify-packs --anchor D:\Restores\YourRepo\.repomori-packs\timeline-anchor.json --json
```

The command emits `repomori.restore_check.v1`. It wraps `doctor`, `chain`,
`timeline`, and optional `verify-anchor` into one read-only report. It does not
copy, delete, or rewrite restored files.

Snapshot indexes may contain absolute paths from the machine that created them.
If a restored copy still points outside the restored directory, `restore-check`
returns `warn` with a portability warning. For a clean restore check, restore to
the original path or rebuild the snapshot timeline in its new location.

## Expected Results

- `status = pass`: the restored timeline is ready for normal RepoMori use.
- `status = warn`: the timeline is readable, but warnings need review before
  using it as an audit source.
- `status = fail`: do not trust the restore until missing or mismatched files
  are fixed.

Useful follow-up commands:

```powershell
python -m repomori doctor D:\Restores\YourRepo\.repomori-packs --verify-packs --json
python -m repomori chain D:\Restores\YourRepo\.repomori-packs --json
python -m repomori timeline D:\Restores\YourRepo\.repomori-packs --format json
python -m repomori verify-anchor D:\Restores\YourRepo\.repomori-packs\timeline-anchor.json D:\Restores\YourRepo\.repomori-packs --json
```

## Safe Restore Notes

- Restore into a new directory first, then run `restore-check`.
- Do not run `prune --apply` against a restored directory until
  `restore-check` passes.
- Keep public issue trackers and chat uploads free of source-bearing packs,
  handoffs, context files, and snapshot archives.
- Treat backup access as source-code access because `.repomori` packs can
  restore exact source bytes.
