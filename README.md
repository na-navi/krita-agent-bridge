# krita-agent-bridge

External automation bridge for agent-driven Krita creative workflows.

This project explores a separate-tool architecture instead of integrating automation directly into the Krita AI Diffusion plugin.

## Goal

Provide a small, inspectable bridge between coding agents and local creative tools:

```text
Agent / CLI
  -> krita-agent-bridge
      -> Krita document adapter
      -> optional Krita AI Diffusion adapter
      -> optional ComfyUI adapter
```

The first target workflow is:

```text
Pi Coding Agent -> bridge -> Krita -> Krita AI Diffusion -> ComfyUI
```

The design should also allow Krita-only automation when AI Diffusion is not available.

## Non-goals

- Do not require upstream Krita AI Diffusion changes for the MVP.
- Do not copy or vendor Krita AI Diffusion internals.
- Do not expose a network service beyond `127.0.0.1` by default.
- Do not automate destructive canvas/file actions without explicit opt-in.

## Adapter boundaries

| Adapter | Stability | Purpose |
| --- | --- | --- |
| Krita document adapter | preferred | active document, canvas export, layer import, selection and basic document operations |
| Krita AI Diffusion adapter | optional/internal | detect plugin availability, query active model state when safe, trigger generation through a thin shim if available |
| ComfyUI adapter | preferred for backend checks | `/object_info`, `/prompt`, `/history`, queue and output inspection |

## Safety defaults

- Bind local HTTP services to `127.0.0.1` only.
- Log bridge commands and generated artifacts.
- Treat AI Diffusion internal APIs as unstable capabilities.
- Keep generated images, reports, and local config out of git.

## Status

Planning scaffold. See `PLANS.md` and `docs/issues/`.
