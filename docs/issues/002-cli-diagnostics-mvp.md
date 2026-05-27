# CLI diagnostics MVP

## Goal

Provide a small CLI that lets agents inspect local readiness before trying creative automation.

## Scope

- `krita-agent status`
- `krita-agent doctor`
- JSON output by default
- non-zero exit codes for unavailable dependencies

## Acceptance criteria

- CLI runs without third-party dependencies.
- It detects Krita bridge status.
- It detects ComfyUI object info availability.
- Errors are readable by both humans and agents.
