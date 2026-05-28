"""Tests for the prompt simplification / prepare layer (Issues #17 + #19).

Covers:
- validate_prepare_input(): all field validations
- PrepareInput construction and __post_init__ validation
- build_workflow(): correct workflow structure from input
- prepare_from_dict(): end-to-end dict → workflow
- Unknown field rejection
- Style restriction via set_allowed_styles
- Edge cases: negative seed, strength boundary, empty positive
"""

from __future__ import annotations

import pytest

from krita_agent_bridge.prepare import (
    PrepareError,
    PrepareInput,
    PrepareResult,
    PreparedWorkflow,
    build_workflow,
    prepare_from_dict,
    resolve_checkpoint,
    set_allowed_styles,
    validate_prepare_input,
)


CHECKPOINT = "real-model.safetensors"


# ---------------------------------------------------------------------------
# validate_prepare_input — dict input
# ---------------------------------------------------------------------------


class TestValidateDict:
    def test_valid_minimal(self) -> None:
        result = validate_prepare_input({"positive": "1girl, cat ears"})
        assert result.ok

    def test_valid_all_fields(self) -> None:
        result = validate_prepare_input({
            "positive": "1girl",
            "negative": "bad quality",
            "seed": 42,
            "strength": 0.8,
            "style": "anime",
            "checkpoint": CHECKPOINT,
            "width": 1024,
            "height": 768,
        })
        assert result.ok

    def test_unknown_field_rejected(self) -> None:
        result = validate_prepare_input({"positive": "test", "foo": "bar"})
        assert not result.ok
        assert result.error == PrepareError.VALIDATION
        assert "Unknown field" in result.message
        assert "foo" in result.message

    def test_multiple_unknown_fields(self) -> None:
        result = validate_prepare_input({"positive": "test", "a": 1, "b": 2})
        assert not result.ok
        assert "a" in result.message
        assert "b" in result.message

    def test_empty_positive_rejected(self) -> None:
        result = validate_prepare_input({"positive": ""})
        assert not result.ok
        assert "positive" in result.message

    def test_whitespace_positive_rejected(self) -> None:
        result = validate_prepare_input({"positive": "   "})
        assert not result.ok

    def test_missing_positive_rejected(self) -> None:
        result = validate_prepare_input({})
        assert not result.ok

    def test_negative_must_be_string(self) -> None:
        result = validate_prepare_input({"positive": "test", "negative": 123})
        assert not result.ok
        assert "negative" in result.message

    def test_checkpoint_must_be_non_empty_string(self) -> None:
        result = validate_prepare_input({"positive": "test", "checkpoint": ""})
        assert not result.ok
        assert "checkpoint" in result.message

    def test_width_height_must_be_positive_ints(self) -> None:
        assert not validate_prepare_input({"positive": "test", "width": 0}).ok
        assert not validate_prepare_input({"positive": "test", "height": -1}).ok
        assert not validate_prepare_input({"positive": "test", "width": 1.5}).ok
        assert not validate_prepare_input({"positive": "test", "height": True}).ok


# ---------------------------------------------------------------------------
# validate_prepare_input — seed
# ---------------------------------------------------------------------------


class TestValidateSeed:
    def test_valid_seed(self) -> None:
        result = validate_prepare_input({"positive": "test", "seed": 0})
        assert result.ok

    def test_large_seed(self) -> None:
        result = validate_prepare_input({"positive": "test", "seed": 999999999})
        assert result.ok

    def test_negative_seed_rejected(self) -> None:
        result = validate_prepare_input({"positive": "test", "seed": -1})
        assert not result.ok
        assert "seed" in result.message
        assert "non-negative" in result.message

    def test_float_seed_rejected(self) -> None:
        result = validate_prepare_input({"positive": "test", "seed": 1.5})
        assert not result.ok
        assert "seed" in result.message

    def test_bool_seed_rejected(self) -> None:
        result = validate_prepare_input({"positive": "test", "seed": True})
        assert not result.ok


# ---------------------------------------------------------------------------
# validate_prepare_input — strength
# ---------------------------------------------------------------------------


class TestValidateStrength:
    def test_valid_strength_0(self) -> None:
        result = validate_prepare_input({"positive": "test", "strength": 0.0})
        assert result.ok

    def test_valid_strength_1(self) -> None:
        result = validate_prepare_input({"positive": "test", "strength": 1.0})
        assert result.ok

    def test_valid_strength_int(self) -> None:
        result = validate_prepare_input({"positive": "test", "strength": 0})
        assert result.ok

    def test_strength_above_1_rejected(self) -> None:
        result = validate_prepare_input({"positive": "test", "strength": 1.1})
        assert not result.ok
        assert "strength" in result.message

    def test_strength_below_0_rejected(self) -> None:
        result = validate_prepare_input({"positive": "test", "strength": -0.1})
        assert not result.ok

    def test_bool_strength_rejected(self) -> None:
        result = validate_prepare_input({"positive": "test", "strength": True})
        assert not result.ok


# ---------------------------------------------------------------------------
# validate_prepare_input — style
# ---------------------------------------------------------------------------


class TestValidateStyle:
    def test_style_accepted_no_restrictions(self) -> None:
        set_allowed_styles(set())
        result = validate_prepare_input({"positive": "test", "style": "anything"})
        assert result.ok

    def test_style_in_allowed_set(self) -> None:
        set_allowed_styles({"anime", "photo"})
        result = validate_prepare_input({"positive": "test", "style": "anime"})
        assert result.ok
        set_allowed_styles(set())  # cleanup

    def test_style_not_in_allowed_set(self) -> None:
        set_allowed_styles({"anime", "photo"})
        result = validate_prepare_input({"positive": "test", "style": "painterly"})
        assert not result.ok
        assert "painterly" in result.message
        assert "anime" in result.message
        set_allowed_styles(set())  # cleanup

    def test_style_must_be_string(self) -> None:
        result = validate_prepare_input({"positive": "test", "style": 123})
        assert not result.ok
        assert "style" in result.message


# ---------------------------------------------------------------------------
# PrepareInput dataclass
# ---------------------------------------------------------------------------


class TestPrepareInput:
    def test_valid_construction(self) -> None:
        inp = PrepareInput(positive="1girl", seed=42, strength=0.8)
        assert inp.positive == "1girl"
        assert inp.seed == 42
        assert inp.strength == 0.8
        assert inp.negative is None
        assert inp.style is None

    def test_invalid_construction_raises(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            PrepareInput(positive="")

    def test_negative_seed_raises(self) -> None:
        with pytest.raises(ValueError, match="seed"):
            PrepareInput(positive="test", seed=-5)

    def test_strength_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="strength"):
            PrepareInput(positive="test", strength=2.0)

    def test_frozen(self) -> None:
        inp = PrepareInput(positive="test")
        with pytest.raises(AttributeError):
            inp.positive = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# build_workflow
# ---------------------------------------------------------------------------


class TestBuildWorkflow:
    def test_basic_workflow_structure(self) -> None:
        inp = PrepareInput(positive="1girl, cat ears", checkpoint=CHECKPOINT)
        result = build_workflow(inp)
        assert result.ok
        assert isinstance(result.data, PreparedWorkflow)

        wf = result.data.workflow
        # Must contain key nodes
        assert "3" in wf  # KSampler
        assert "6" in wf  # CLIP positive
        assert "7" in wf  # CLIP negative
        assert "4" in wf  # CheckpointLoader
        assert "5" in wf  # EmptyLatentImage
        assert "8" in wf  # VAEDecode
        assert "9" in wf  # SaveImage

    def test_positive_in_workflow(self) -> None:
        inp = PrepareInput(positive="1girl, sunset", checkpoint=CHECKPOINT)
        result = build_workflow(inp)
        assert result.ok
        wf = result.data.workflow
        positive_text = wf["6"]["inputs"]["text"]
        assert "1girl, sunset" in positive_text

    def test_negative_in_workflow(self) -> None:
        inp = PrepareInput(positive="test", negative="bad quality", checkpoint=CHECKPOINT)
        result = build_workflow(inp)
        assert result.ok
        wf = result.data.workflow
        assert wf["7"]["inputs"]["text"] == "bad quality"

    def test_seed_in_workflow(self) -> None:
        inp = PrepareInput(positive="test", seed=42, checkpoint=CHECKPOINT)
        result = build_workflow(inp)
        assert result.ok
        wf = result.data.workflow
        assert wf["3"]["inputs"]["seed"] == 42

    def test_default_seed_negative_one(self) -> None:
        inp = PrepareInput(positive="test", checkpoint=CHECKPOINT)
        result = build_workflow(inp)
        assert result.ok
        wf = result.data.workflow
        assert wf["3"]["inputs"]["seed"] == -1

    def test_strength_maps_to_cfg(self) -> None:
        inp = PrepareInput(positive="test", strength=0.5, checkpoint=CHECKPOINT)
        result = build_workflow(inp)
        assert result.ok
        wf = result.data.workflow
        assert wf["3"]["inputs"]["cfg"] == 0.5

    def test_default_strength(self) -> None:
        inp = PrepareInput(positive="test", checkpoint=CHECKPOINT)
        result = build_workflow(inp)
        assert result.ok
        wf = result.data.workflow
        assert wf["3"]["inputs"]["cfg"] == 7.0

    def test_style_prepended_to_positive(self) -> None:
        inp = PrepareInput(positive="1girl", style="anime", checkpoint=CHECKPOINT)
        result = build_workflow(inp)
        assert result.ok
        wf = result.data.workflow
        positive_text = wf["6"]["inputs"]["text"]
        assert positive_text.startswith("anime, ")
        assert "1girl" in positive_text

    def test_no_style_no_prefix(self) -> None:
        inp = PrepareInput(positive="1girl", checkpoint=CHECKPOINT)
        result = build_workflow(inp)
        assert result.ok
        wf = result.data.workflow
        positive_text = wf["6"]["inputs"]["text"]
        assert positive_text == "1girl"

    def test_summary_contains_input(self) -> None:
        inp = PrepareInput(
            positive="1girl",
            seed=42,
            strength=0.8,
            style="anime",
            checkpoint=CHECKPOINT,
        )
        result = build_workflow(inp)
        assert result.ok
        summary = result.data.summary
        assert summary["positive"] == "1girl"
        assert summary["seed"] == 42
        assert summary["strength"] == 0.8
        assert summary["style"] == "anime"
        assert summary["checkpoint"] == CHECKPOINT
        assert "nodes" in summary

    def test_checkpoint_in_workflow(self) -> None:
        inp = PrepareInput(positive="test", checkpoint=CHECKPOINT)
        result = build_workflow(inp)
        assert result.ok
        assert result.data.workflow["4"]["inputs"]["ckpt_name"] == CHECKPOINT

    def test_empty_latent_image_uses_dimensions(self) -> None:
        inp = PrepareInput(positive="test", checkpoint=CHECKPOINT, width=512, height=768)
        result = build_workflow(inp)
        assert result.ok
        latent = result.data.workflow["5"]["inputs"]
        assert latent == {"width": 512, "height": 768, "batch_size": 1}
        assert result.data.workflow["3"]["inputs"]["latent_image"] == ["5", 0]

    def test_checkpoint_required_without_adapter(self) -> None:
        inp = PrepareInput(positive="test")
        result = build_workflow(inp)
        assert not result.ok
        assert "checkpoint" in result.message

    def test_invalid_input_returns_error(self) -> None:
        # Build with invalid input — should fail at build_workflow
        # (PrepareInput would reject at construction, so use dict path)
        result = prepare_from_dict({"positive": "", "seed": -1})
        assert not result.ok


class _ObjectInfoResult:
    def __init__(self, ok: bool, data: object = None, message: str = "") -> None:
        self.ok = ok
        self.data = data
        self.message = message


class _ComfyStub:
    def object_info(self, node_filter: str | None = None) -> _ObjectInfoResult:
        assert node_filter == "CheckpointLoaderSimple"
        return _ObjectInfoResult(
            True,
            {
                "CheckpointLoaderSimple": {
                    "input": {
                        "required": {
                            "ckpt_name": [["first.safetensors", "second.safetensors"], {}]
                        }
                    }
                }
            },
        )


class TestResolveCheckpoint:
    def test_explicit_checkpoint_wins(self) -> None:
        result = resolve_checkpoint(CHECKPOINT, comfyui_adapter=_ComfyStub())
        assert result.ok
        assert result.data == CHECKPOINT

    def test_resolves_first_available_checkpoint(self) -> None:
        result = resolve_checkpoint(None, comfyui_adapter=_ComfyStub())
        assert result.ok
        assert result.data == "first.safetensors"

    def test_build_workflow_resolves_checkpoint_from_adapter(self) -> None:
        result = build_workflow(PrepareInput(positive="test"), comfyui_adapter=_ComfyStub())
        assert result.ok
        assert result.data.workflow["4"]["inputs"]["ckpt_name"] == "first.safetensors"


# ---------------------------------------------------------------------------
# prepare_from_dict
# ---------------------------------------------------------------------------


class TestPrepareFromDict:
    def test_end_to_end(self) -> None:
        result = prepare_from_dict({
            "positive": "1girl, cat ears",
            "negative": "bad",
            "seed": 42,
            "strength": 0.8,
            "style": "anime",
            "checkpoint": CHECKPOINT,
        })
        assert result.ok
        assert isinstance(result.data, PreparedWorkflow)
        wf = result.data.workflow
        assert wf["3"]["inputs"]["seed"] == 42

    def test_unknown_field_caught(self) -> None:
        result = prepare_from_dict({"positive": "test", "steps": 50})
        assert not result.ok
        assert "steps" in result.message

    def test_minimal_input(self) -> None:
        result = prepare_from_dict({"positive": "test", "checkpoint": CHECKPOINT})
        assert result.ok
        wf = result.data.workflow
        assert "3" in wf


# ---------------------------------------------------------------------------
# PrepareResult / PreparedWorkflow dataclass checks
# ---------------------------------------------------------------------------


class TestPrepareResult:
    def test_frozen(self) -> None:
        result = PrepareResult(ok=True)
        with pytest.raises(AttributeError):
            result.ok = False  # type: ignore[misc]

    def test_defaults(self) -> None:
        result = PrepareResult(ok=True)
        assert result.error == PrepareError.NONE
        assert result.message == ""
        assert result.data is None


class TestPreparedWorkflow:
    def test_frozen(self) -> None:
        wf = PreparedWorkflow(workflow={}, summary={})
        with pytest.raises(AttributeError):
            wf.workflow = {}  # type: ignore[misc]
