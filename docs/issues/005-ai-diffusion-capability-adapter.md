# Krita AI Diffusion capability adapter

## Goal

Detect and optionally use Krita AI Diffusion without making it a hard dependency.

## Scope

- plugin presence detection
- version/capability reporting
- active model availability check
- isolated access to internal document model if needed

## Acceptance criteria

- Bridge works when AI Diffusion is absent.
- Internal API access is behind one adapter module.
- Capability failures degrade gracefully.
