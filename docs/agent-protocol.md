# RepoMori Agent Protocol

`python -m repomori agent` runs a dependency-free JSON-lines bridge on stdio.
It is local-only: no network calls, no model calls, and no API keys.

Start the bridge:

```powershell
python -m repomori agent --config D:\Dev\RepoMori\repomori.toml
```

Send one JSON object per line:

```json
{"id":1,"method":"agent.help"}
{"id":2,"method":"memory.run","params":{"keep":20}}
{"id":3,"method":"query.run","params":{"text":"sqlite Store","limit":3}}
{"id":4,"method":"context.build","params":{"question":"where is storage handled?","max_files":3}}
{"id":5,"method":"file.get","params":{"path":"repomori/codec.py"}}
```

Each response is one JSON line:

```json
{"schema_version":"repomori.agent.response.v1","jsonrpc":"2.0","id":1,"ok":true,"result":{}}
```

Errors use the same envelope with `ok:false` and an `error` object.

## Methods

- `agent.help`: returns protocol and method metadata.
- `ping`: returns a simple status payload.
- `memory.run`: runs the configured memory cycle.
- `timeline.read`: reads the configured snapshot timeline.
- `doctor.run`: checks snapshot directory health.
- `query.run`: runs pack query; uses latest configured pack if `pack` is omitted.
- `context.build`: builds a source-backed context bundle.
- `handoff.build`: writes a handoff package directory.
- `capsule.build`: exports a capsule payload.
- `file.get`: retrieves exact file bytes as text when decodable plus base64.

## Pack Resolution

Methods that operate on a pack accept `params.pack`. If omitted, RepoMori reads
the configured snapshot timeline and uses the latest indexed pack.
