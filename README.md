# RadialRig

RadialRig, a Radial 47 product. Governed-record retrieval, authoring,
and validation tooling that mounts onto a project you already have.

This repository holds releases only: the Windows installer, the two
Linux bundles, and the engine wheel beside them. The source lives in
a private repository.

Start at the Releases page. Each release states the download and
first-run warnings, and how to remove the product, before the files.

## The authoring server

A mount registers the retrieval server and the authoring server side
by side in `.mcp.json`, and again in a fenced region of
`.codex/config.toml` so a Codex agent reaches the same two servers.
The authoring server saves a ruling into your working copy and stops
there; you approve the ruling in one sentence, an agent reads the
diff, and the commit follows. To switch capture off, remove the
`radialrig-write` entry from each file: this key from `.mcp.json`, and
the matching table from `.codex/config.toml`. Nothing else changes, and
unmount and upgrade keep working around the removal.

```json
"radialrig-write": { "command": "radialrig-write", "args": [] }
```

```toml
[mcp_servers.radialrig-write]
command = "radialrig-write"
args = []
```

When `radialrig-write` is not on PATH, register the absolute path of
the console script instead, or run it through
`uv run --with radialrig radialrig-write`.
