# Agent workflow recipes

Practical patterns for coding agents that use `krita-agent-bridge` safely.

## Recipe 1: Status-before-action

**Precondition:** Krita and/or ComfyUI may or may not be running.

**Pattern:** Always check environment before any write operation.

```powershell
# Step 1: Diagnose
krita-agent doctor --json

# Step 2: Parse exit code
#   0 = all OK, proceed
#   1 = recoverable, some features unavailable
#   2 = fatal, stop and report to human
if ($LASTEXITCODE -eq 0) {
    Write-Host "Environment ready."
} elseif ($LASTEXITCODE -eq 1) {
    Write-Host "Partial availability. Check JSON for details."
} else {
    Write-Host "Fatal issue. Report to human before continuing."
    exit 1
}
```

**Why:** Prevents silent failures during generation. Agents should never assume the environment is up.

---

## Recipe 2: Validate workflow before submitting

**Precondition:** ComfyUI is running (doctor exit 0 or 1).

**Pattern:** Validate node types against `/object_info` before prompt submission.

```python
from krita_agent_bridge.comfyui import ComfyUIAdapter

adapter = ComfyUIAdapter()
result = adapter.validate_prompt({"nodes": [
    {"type": "KSampler", "id": 1},
    {"type": "CLIPTextEncode", "id": 2},
    {"type": "SaveImage", "id": 3},
]})

if not result.ok:
    # STOP: do not submit. Report to human.
    print(f"Validation failed: {result.message}")
else:
    print("Workflow validated. Safe to submit.")
```

**Why:** Catches typos and missing custom nodes before ComfyUI processes them.

---

## Recipe 3: Generate and collect output

**Precondition:** ComfyUI has finished a generation (prompt_id known).

**Pattern:** Resolve output files, report absolute paths.

```python
from krita_agent_bridge.comfyui import ComfyUIAdapter

adapter = ComfyUIAdapter()
result = adapter.resolve_outputs("prompt-abc123")

if result.ok:
    for path in result.data:
        print(f"Generated: {path}")
        # All paths are absolute. Safe to pass to Krita or file system.
else:
    print(f"Could not resolve outputs: {result.message}")
```

**Why:** Absolute paths avoid working-directory confusion in agent scripts.

---

## Recipe 4: Check AI Diffusion availability

**Precondition:** Krita bridge is responding.

**Pattern:** Detect plugin presence before attempting generation through the AI Diffusion path.

```powershell
# Check if AI Diffusion is available
$result = krita-agent doctor --json | ConvertFrom-Json
$aiDiff = $result.doctor.checks | Where-Object { $_.name -eq "ai_diffusion" }

if ($aiDiff.ok) {
    Write-Host "AI Diffusion is active. Can use generation path."
} else {
    Write-Host "AI Diffusion not available. Use ComfyUI path only."
}
```

**Why:** Falls back gracefully when the plugin is missing or disabled.

---

## Recipe 5: Troubleshooting loop

**Precondition:** An operation failed.

**Pattern:** Run doctor, classify error, suggest fix.

```
Operation failed? → krita-agent doctor --json
  ├─ exit 0  → Logic error in agent code. Check inputs.
  ├─ exit 1  → Recoverable. Read hints in JSON, retry after fix.
  └─ exit 2  → Fatal. Stop and ask human to start services.

Error types from ComfyUI adapter:
  ├─ connection → Service unreachable. Start it.
  ├─ validation → Bad input. Fix the workflow.
  └─ execution  → Runtime error. Check ComfyUI logs.
```

**Why:** Agents need a deterministic error recovery path.

---

## Safety rules for all recipes

1. **Never delete or overwrite files without `--allow-destructive`.**
2. **Always run `doctor` before a generation session.**
3. **Report absolute paths, never relative.**
4. **If exit code is 2, stop and notify the human. Do not retry.**
5. **If a destructive operation is attempted without the flag, print what would happen and exit 1.**
