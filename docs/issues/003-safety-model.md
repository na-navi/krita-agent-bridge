# Local safety model

## Goal

Define safe defaults for a local agent bridge that can affect a creative document.

## Scope

- Bind local services to `127.0.0.1` only.
- Default to non-destructive layer creation.
- Log commands and artifact paths.
- Decide whether a local session token is required.

## Acceptance criteria

- Safety policy is documented.
- Destructive commands are opt-in only.
- CORS and remote access behavior are explicit.
