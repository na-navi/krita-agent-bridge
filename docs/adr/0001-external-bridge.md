# ADR 0001: Build as an external bridge

## Status

Accepted for initial experiment.

## Context

The initial prototype explored agent-driven local Krita workflows with optional generation backends. An older fork-based extension was archived after it became clear that turning it into a practical upstream PR was not realistic. Upstream feedback also suggested that direct integration into an existing plugin would require careful planning and that a separate project may be better suited.

## Decision

Create `krita-agent-bridge` as a separate automation project.

The bridge will prefer public or stable-ish surfaces:

- Krita Python/document APIs
- local bridge commands
- optional local backend APIs

Krita AI Diffusion internals may be used only behind a narrow optional adapter with capability checks.

## Consequences

Positive:

- Less review and maintenance burden for upstream.
- Faster experimentation.
- Clearer safety boundary for agent actions.

Trade-offs:

- Some features may need a small local Krita shim.
- AI Diffusion internals can change and break optional features.
- End-to-end tests may require a running GUI application.
