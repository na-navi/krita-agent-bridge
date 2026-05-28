# Plan

## Direction

Build `krita-agent-bridge` as an external Krita scripting and diagnostics project. Optional local integrations may be detected when present, but Krita document operations and safety boundaries are the primary integration surface.

## Architecture sketch

```text
Agent
  |
  | CLI / local HTTP
  v
krita-agent-bridge
  |-- core command model
  |-- safety policy
  |-- diagnostics / doctor mode
  |-- adapters
      |-- Krita document adapter
      |-- optional plugin capability adapter
      |-- optional backend adapter
```

## Phase 0 — Planning and boundaries

- Record why this is a separate project.
- Define what is stable API vs internal API.
- Define safety and local-only assumptions.

## Phase 1 — CLI and diagnostics MVP ✅

- `krita-agent status`
- `krita-agent doctor`
- detect Krita process, bridge availability, optional plugin availability, optional backend availability.

## Phase 2 — Krita document MVP ✅

- active document info
- canvas export
- generated image import as a new layer
- non-destructive default behavior

## Phase 3 — Optional plugin capability adapter ✅

- detect Krita AI Diffusion plugin presence and version
- query active model/capabilities through a narrow shim
- mode switching (manual / watch / auto)
- style list query
- avoid depending on internal implementation details unless isolated

## Phase 4 — Optional backend adapter ✅

- inspect ComfyUI nodes and queue
- submit known-safe workflows
- map output files back to bridge artifacts

## Phase 5 — Integration layers ✅

- prompt simplification layer (prepare)
- parameter shortcut wrapper (seed, strength, style)
- job status monitoring bridge (prompt_id → job_id)
- unified snapshot endpoint

## Phase 6 — E2E smoke workflow 📋

- `krita-agent smoke` runner and JSON report
- create/open document manually or via Krita API
- capture state
- generate through an optional local integration path
- apply result as a new layer
- produce a machine-readable run report

## Review rule

Each GitHub issue or PR should be small enough to review independently. Prefer adapters and tests over broad rewrites.
