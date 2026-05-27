# Safety model

## Goal

Define safe defaults for a local agent bridge that can affect a creative document.

## Policy

### Local-only binding

All HTTP services (`krita-agent`, ComfyUI adapter) default to `127.0.0.1`.
No listener binds to `0.0.0.0` or a public interface unless the user explicitly overrides.

### Non-destructive defaults

| Operation | Default behavior |
|---|---|
| Canvas export | Read-only. Exports to a temp file, never overwrites the source. |
| Image import | Creates a **new layer**. Never replaces or merges existing layers. |
| Layer deletion | **Requires explicit `--allow-destructive` flag.** |
| Document save / overwrite | **Requires explicit `--allow-destructive` flag.** |
| File deletion | **Never automated.** Always raises for human confirmation. |

### Opt-in for destructive commands

Destructive operations are gated behind a CLI flag:

```
krita-agent layer delete --layer-id 3 --allow-destructive
```

Without `--allow-destructive`, the command prints what it *would* do and exits with code 1.

### CORS and remote access

- No CORS headers are set by default.
- `--allow-remote` can bind to a specific interface, but prints a warning.
- There is no authentication mechanism in the MVP. If remote access is enabled, the user accepts the risk.

### Session tokens

The MVP does not require session tokens. All commands are local-only and trust the local user.
If remote access is ever supported, a token or HMAC scheme will become mandatory.

### Logging

- Every command invocation is logged with timestamp, command name, and result code.
- Logs go to stderr by default (machine-parseable JSON).
- Optional file logging: `--log-file <path>` appends JSONL entries.
- Generated file paths are included in the log entry.
- No image data is logged; only paths and metadata.

### Safety summary output

`krita-agent doctor` reports the current safety configuration as part of its output:

```json
{
  "safety": {
    "bind_address": "127.0.0.1",
    "destructive_allowed": false,
    "remote_allowed": false,
    "log_file": null
  }
}
```

## Acceptance criteria

- [x] Safety policy is documented.
- [x] Destructive commands are opt-in only.
- [x] CORS and remote access behavior are explicit.
- [x] Session token policy is stated.
