# Krita document adapter MVP

## Goal

Expose a narrow adapter for Krita document operations needed by agent workflows.

## Scope

- active document info
- canvas export
- image import as a new layer
- basic document dimensions and filename metadata

## Acceptance criteria

- Adapter avoids destructive writes by default.
- Operations return machine-readable results.
- Failures include actionable diagnostics.
