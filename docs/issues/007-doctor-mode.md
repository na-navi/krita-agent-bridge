# Doctor mode diagnostics

## Goal

Provide a one-command diagnostic report for agent-driven Krita workflows.

## Scope

- Krita process status
- local bridge status
- AI Diffusion availability
- ComfyUI availability
- port checks
- common recovery hints

## Acceptance criteria

- Report is JSON plus readable summary.
- Exit code distinguishes OK, recoverable issue, and fatal issue.
- No process is killed automatically.
