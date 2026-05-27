# End-to-end smoke workflow

## Goal

Prove the bridge can complete a minimal creative loop without manual code changes.

## Scope

- confirm document readiness
- capture canvas state
- trigger generation through the selected adapter path
- import result as a new layer
- save a run report

## Acceptance criteria

- The workflow is repeatable from the CLI.
- Generated artifacts are excluded from git.
- Failures produce a doctor report.
