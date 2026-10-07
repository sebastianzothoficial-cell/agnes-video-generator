"""Deterministic prompt and creation-mode specifications for Agnes video jobs."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


CREATION_MODES = (
    "simple",
    "creative",
    "manuscript",
    "poetry",
    "digital_narration",
)


@dataclass(frozen=True)
class PromptSpec:
    original: str
    style: str = "cinematic"
    scene: str = "single coherent scene"
    subject: str = ""
    action: str = ""
    camera: str = "cinematic camera, stable composition"
    lighting: str = "natural cinematic lighting"
    atmosphere: str = "coherent atmosphere"
    reference_consistency: str = ""

    def render(self) -> str:
        parts = [
            f"STYLE: {self.style}",
            f"SCENE: {self.scene}",
            f"SUBJECT: {self.subject or self.original}",
            f"ACTION: {self.action or self.original}",
            f"CAMERA: {self.camera}",
            f"LIGHTING: {self.lighting}",
            f"ATMOSPHERE: {self.atmosphere}",
        ]
        if self.reference_consistency:
            parts.append(f"REFERENCE CONSISTENCY: {self.reference_consistency}")
        parts.append("ONE COHERENT SHOT: do not combine incompatible scenes or locations.")
        return "\n".join(parts)


@dataclass(frozen=True)
class SceneSpec:
    index: int
    duration: int
    place: str = ""
    characters: str = ""
    action: str = ""
    camera: str = ""
    lighting: str = ""
    atmosphere: str = ""
    style: str = ""
    prompt_original: str = ""
    prompt_processed: str = ""
    model_used: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CreativePlan:
    idea: str
    scenes: tuple[SceneSpec, ...]
    style: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"idea": self.idea, "style": self.style, "scenes": [s.as_dict() for s in self.scenes]}


@dataclass(frozen=True)
class ManuscriptSegment:
    index: int
    original_text: str
    estimated_duration: float
    visual_concept: str = ""
    prompt_original: str = ""
    prompt_processed: str = ""
    narration: str = ""
    subtitle: str = ""


@dataclass(frozen=True)
class PoetrySegment:
    index: int
    original_verse: str
    visual_concept: str = ""
    prompt_original: str = ""
    prompt_processed: str = ""
    narration: str = ""
    subtitle: str = ""


@dataclass(frozen=True)
class NarrationSpec:
    script_original: str
    reference_image: str = ""
    voice_model: str = ""
    narration_text: str = ""
    subtitle_text: str = ""


def build_video_prompt(
    prompt: str,
    *,
    style: str = "cinematic",
    scene: str = "single coherent scene",
    subject: str = "",
    action: str = "",
    camera: str = "cinematic camera, stable composition",
    lighting: str = "natural cinematic lighting",
    atmosphere: str = "coherent atmosphere",
    reference_consistency: str = "",
) -> PromptSpec:
    """Build one deterministic, debuggable prompt without changing user input."""
    return PromptSpec(
        original=prompt,
        style=style or "cinematic",
        scene=scene or "single coherent scene",
        subject=subject or prompt,
        action=action or prompt,
        camera=camera or "cinematic camera, stable composition",
        lighting=lighting or "natural cinematic lighting",
        atmosphere=atmosphere or "coherent atmosphere",
        reference_consistency=reference_consistency,
    )
