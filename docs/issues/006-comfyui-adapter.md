# ComfyUI adapter MVP

## Goal

Add a direct ComfyUI adapter for backend diagnostics and optional workflow execution.

## Scope

- `/object_info` inspection
- queue status and history retrieval
- prompt submission for known-safe workflows
- output file resolution

## Acceptance criteria

- Adapter can distinguish connection failure, validation failure, and execution failure.
- Output paths are reported as absolute paths.
- Node schema assumptions are validated before prompt submission.
