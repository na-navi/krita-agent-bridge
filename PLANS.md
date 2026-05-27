# Plan

## Direction

Build `krita-agent-bridge` as an external automation project. The bridge may communicate with Krita AI Diffusion when present, but the plugin itself is not the primary integration surface.

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
      |-- Krita AI Diffusion capability adapter
      |-- ComfyUI adapter
```

## Phase 0 — Planning and boundaries

- Record why this is a separate project.
- Define what is stable API vs internal API.
- Define safety and local-only assumptions.

## Phase 1 — CLI and diagnostics MVP

- `krita-agent status`
- `krita-agent doctor`
- detect Krita process, bridge availability, AI Diffusion availability, ComfyUI availability.

## Phase 2 — Krita document MVP

- active document info
- canvas export
- generated image import as a new layer
- non-destructive default behavior

## Phase 3 — AI Diffusion optional adapter

- detect plugin presence and version
- query active model/capabilities through a narrow shim
- avoid depending on private implementation details unless isolated

## Phase 4 — ComfyUI adapter

- inspect nodes and queue
- submit known-safe workflows
- map output files back to bridge artifacts

## Phase 5 — E2E smoke workflow

- create/open document manually or via Krita API
- capture state
- generate through AI Diffusion or ComfyUI path
- apply result as a new layer
- produce a machine-readable run report

## Review rule

Each issue should be small enough to review independently. Prefer adapters and tests over broad rewrites.
