"""Prompt simplification / prepare layer for krita-agent-bridge.

Issues #17 + #19: High-level prompt interface that abstracts away
raw ComfyUI workflow JSON construction.

Design:
- Stateless PrepareInput dataclass — no session state
- build_workflow() constructs ComfyUI workflow from simple fields
- validate_prepare_input() checks field values before construction
- Known-safe template: agent submits {"positive": "1girl, cat ears"}
  without knowing ComfyUI node layout
- Unknown fields are rejected with a clear error message
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class PrepareError(Enum):
    VALIDATION = "validation"
    NONE = "none"


@dataclass(frozen=True)
class PrepareResult:
    """Result from a prepare-layer operation."""

    ok: bool
    error: PrepareError = PrepareError.NONE
    message: str = ""
    data: Any = None


@dataclass(frozen=True)
class PrepareInput:
    """Simplified generation parameters.

    Stateless by design — no session state, no set_params() mutation.
    Every field is validated at construction time via validate_prepare_input().
    """

    positive: str
    negative: str | None = None
    seed: int | None = None
    strength: float | None = None
    style: str | None = None

    def __post_init__(self) -> None:
        # Validate on construction so errors surface immediately
        result = validate_prepare_input(self)
        if not result.ok:
            raise ValueError(result.message)


@dataclass(frozen=True)
class PreparedWorkflow:
    """A constructed ComfyUI workflow ready for submission.

    Contains the workflow JSON and a summary of the input used.
    """

    workflow: dict[str, Any]
    summary: dict[str, Any]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

# Known allowed fields on PrepareInput
_KNOWN_FIELDS = frozenset({
    "positive", "negative", "seed", "strength", "style",
})

# Allowed style names (extensible via set_allowed_styles)
_ALLOWED_STYLES: set[str] = set()


def set_allowed_styles(styles: set[str]) -> None:
    """Configure the set of allowed style names.

    Called by the application after fetching styles from AI Diffusion.
    If empty, any style string is accepted (no restriction).
    """
    global _ALLOWED_STYLES  # noqa: PLW0603
    _ALLOWED_STYLES = styles


def validate_prepare_input(inp: PrepareInput | dict[str, Any]) -> PrepareResult:
    """Validate a PrepareInput or raw dict before workflow construction.

    Checks:
    - positive prompt is non-empty
    - seed is non-negative int (if provided)
    - strength is 0.0–1.0 (if provided)
    - style is in allowed set (if configured)
    - No unknown fields (for dict input)
    """
    # Convert dict to check for unknown fields
    if isinstance(inp, dict):
        unknown = set(inp.keys()) - _KNOWN_FIELDS
        if unknown:
            return PrepareResult(
                ok=False,
                error=PrepareError.VALIDATION,
                message=f"Unknown field(s): {', '.join(sorted(unknown))}. "
                f"Allowed: {', '.join(sorted(_KNOWN_FIELDS))}",
            )
        positive = inp.get("positive", "")
        negative = inp.get("negative")
        seed = inp.get("seed")
        strength = inp.get("strength")
        style = inp.get("style")
    else:
        positive = inp.positive
        negative = inp.negative
        seed = inp.seed
        strength = inp.strength
        style = inp.style

    # positive is required and non-empty
    if not isinstance(positive, str) or not positive.strip():
        return PrepareResult(
            ok=False,
            error=PrepareError.VALIDATION,
            message="Field 'positive' must be a non-empty string",
        )

    # negative: if provided, must be string
    if negative is not None and not isinstance(negative, str):
        return PrepareResult(
            ok=False,
            error=PrepareError.VALIDATION,
            message="Field 'negative' must be a string or null",
        )

    # seed: non-negative int
    if seed is not None:
        if not isinstance(seed, int) or isinstance(seed, bool):
            return PrepareResult(
                ok=False,
                error=PrepareError.VALIDATION,
                message=f"Field 'seed' must be an integer, got {type(seed).__name__}",
            )
        if seed < 0:
            return PrepareResult(
                ok=False,
                error=PrepareError.VALIDATION,
                message=f"Field 'seed' must be non-negative, got {seed}",
            )

    # strength: 0.0–1.0
    if strength is not None:
        if not isinstance(strength, (int, float)) or isinstance(strength, bool):
            return PrepareResult(
                ok=False,
                error=PrepareError.VALIDATION,
                message=f"Field 'strength' must be a number, got {type(strength).__name__}",
            )
        if not (0.0 <= float(strength) <= 1.0):
            return PrepareResult(
                ok=False,
                error=PrepareError.VALIDATION,
                message=f"Field 'strength' must be 0.0–1.0, got {strength}",
            )

    # style: must be in allowed set if configured
    if style is not None:
        if not isinstance(style, str):
            return PrepareResult(
                ok=False,
                error=PrepareError.VALIDATION,
                message=f"Field 'style' must be a string, got {type(style).__name__}",
            )
        if _ALLOWED_STYLES and style not in _ALLOWED_STYLES:
            return PrepareResult(
                ok=False,
                error=PrepareError.VALIDATION,
                message=f"Unknown style '{style}'. "
                f"Available: {', '.join(sorted(_ALLOWED_STYLES))}",
            )

    return PrepareResult(ok=True, message="Input validated")


# ---------------------------------------------------------------------------
# Workflow construction
# ---------------------------------------------------------------------------

# Default node IDs for the known-safe template
_NODE_CLIP_POSITIVE = "6"
_NODE_CLIP_NEGATIVE = "7"
_NODE_KSAMPLER = "3"
_NODE_CHECKPOINT = "4"
_NODE_VAE_DECODE = "8"
_NODE_SAVE_IMAGE = "9"


def build_workflow(inp: PrepareInput) -> PrepareResult:
    """Build a ComfyUI workflow JSON from a PrepareInput.

    Uses a known-safe template:
    - CLIPTextEncode for positive/negative prompts
    - KSampler with seed, strength (cfg), style
    - CheckpointLoaderSimple
    - VAEDecode
    - SaveImage

    Returns PreparedWorkflow containing the workflow dict and an input summary.
    """
    # Validate first
    validation = validate_prepare_input(inp)
    if not validation.ok:
        return validation

    positive_text = inp.positive
    negative_text = inp.negative or ""
    seed = inp.seed if inp.seed is not None else -1  # -1 = random
    cfg_strength = inp.strength if inp.strength is not None else 7.0

    # Style prefix for positive prompt (if provided)
    style_prefix = f"{inp.style}, " if inp.style else ""

    workflow: dict[str, Any] = {
        _NODE_CHECKPOINT: {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {
                "ckpt_name": "model.safetensors",
            },
        },
        _NODE_CLIP_POSITIVE: {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": f"{style_prefix}{positive_text}",
                "clip": [_NODE_CHECKPOINT, 1],
            },
        },
        _NODE_CLIP_NEGATIVE: {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": negative_text,
                "clip": [_NODE_CHECKPOINT, 1],
            },
        },
        _NODE_KSAMPLER: {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": 20,
                "cfg": cfg_strength,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": [_NODE_CHECKPOINT, 0],
                "positive": [_NODE_CLIP_POSITIVE, 0],
                "negative": [_NODE_CLIP_NEGATIVE, 0],
                "latent_image": ["5", 0],
            },
        },
        _NODE_VAE_DECODE: {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": [_NODE_KSAMPLER, 0],
                "vae": [_NODE_CHECKPOINT, 2],
            },
        },
        _NODE_SAVE_IMAGE: {
            "class_type": "SaveImage",
            "inputs": {
                "filename_prefix": "krita-agent",
                "images": [_NODE_VAE_DECODE, 0],
            },
        },
    }

    summary = {
        "positive": positive_text,
        "negative": negative_text,
        "seed": seed,
        "strength": inp.strength,
        "style": inp.style,
        "nodes": list(workflow.keys()),
    }

    return PrepareResult(
        ok=True,
        message="Workflow constructed",
        data=PreparedWorkflow(workflow=workflow, summary=summary),
    )


def prepare_from_dict(fields: dict[str, Any]) -> PrepareResult:
    """Convenience: validate a raw dict and build a workflow.

    Combines validate_prepare_input() + PrepareInput construction +
    build_workflow() into a single call. Returns PrepareResult with
    PreparedWorkflow on success.
    """
    # Validate raw dict first (catches unknown fields)
    validation = validate_prepare_input(fields)
    if not validation.ok:
        return validation

    # Construct PrepareInput (triggers __post_init__ validation)
    try:
        inp = PrepareInput(
            positive=str(fields["positive"]),
            negative=fields.get("negative"),
            seed=fields.get("seed"),
            strength=fields.get("strength"),
            style=fields.get("style"),
        )
    except ValueError as exc:
        return PrepareResult(
            ok=False,
            error=PrepareError.VALIDATION,
            message=str(exc),
        )

    return build_workflow(inp)
