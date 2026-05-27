# Project boundary and adapter map

## Goal

Document the project boundary so the bridge remains a separate tool and does not become an accidental fork of Krita AI Diffusion.

## Scope

- Define stable vs internal integration surfaces.
- Define what can be copied, referenced, or wrapped.
- Record license and maintenance assumptions.

## Acceptance criteria

- `docs/adr/0001-external-bridge.md` is reviewed and updated if needed.
- README clearly says this is external and optional.
- AI Diffusion internals are marked as optional and unstable.
