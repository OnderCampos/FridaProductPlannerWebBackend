from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class SpecFlowStep:
    step_id: str
    name: str
    purpose: str
    inputs: List[str]
    outputs: List[str]


# Lean Spec Generation Agent Flow (5 steps)
# This file is intentionally descriptive: it documents the flow used to
# generate the specification document, regardless of which creation source
# (QA, document, file, figma) is used.
SPEC_GENERATION_FLOW: List[SpecFlowStep] = [
    SpecFlowStep(
        step_id="context_builder",
        name="Context Builder",
        purpose="Merge description, source payloads, and metadata into a single normalized context.",
        inputs=["description", "source_payload", "project_metadata"],
        outputs=["normalized_context"],
    ),
    SpecFlowStep(
        step_id="spec_planner",
        name="Spec Planner",
        purpose="Create an outline with the required FSD sections and identify missing info.",
        inputs=["normalized_context", "required_section_list"],
        outputs=["section_outline", "missing_information"],
    ),
    SpecFlowStep(
        step_id="spec_writer",
        name="Spec Writer",
        purpose="Write each section using the outline and enforced formatting rules.",
        inputs=["section_outline", "normalized_context"],
        outputs=["draft_spec_text"],
    ),
    SpecFlowStep(
        step_id="consistency_checker",
        name="Consistency Checker",
        purpose="Align terms, roles, IDs, and feature names across sections.",
        inputs=["draft_spec_text"],
        outputs=["normalized_spec_text"],
    ),
    SpecFlowStep(
        step_id="compliance_validator",
        name="Compliance Validator",
        purpose="Ensure all required sections and formats are present.",
        inputs=["normalized_spec_text", "required_section_list"],
        outputs=["validated_spec_text", "validation_report"],
    ),
]


def describe_spec_generation_flow() -> List[Dict[str, object]]:
    """
    Return the spec generation flow steps as plain dictionaries.
    Useful for logging, docs, or debug output.
    """
    return [
        {
            "step_id": step.step_id,
            "name": step.name,
            "purpose": step.purpose,
            "inputs": step.inputs,
            "outputs": step.outputs,
        }
        for step in SPEC_GENERATION_FLOW
    ]
