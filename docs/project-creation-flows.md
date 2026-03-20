# Project Creation Flows

This document describes the three supported project creation flows and their API steps.

## Spec Generation Agent Flow (Lean)
This is the documented, structured flow used to generate the specification document.
It is source-agnostic (QA, document, file, figma).

Steps:
1. Context Builder
2. Spec Planner
3. Spec Writer
4. Consistency Checker
5. Compliance Validator

Code reference:
- `FridaProductPlannerBackend/src/utils/ai/spec_generation_flow.py`

## Q&A Flow
1. `POST /project/creation/qa`
   - Body: `name`, `project_key`, `description`
   - Response: `project` + `clarification` (questions)
2. `POST /project/{project_id}/clarification/answer`
   - Body: `answers` array of `{ question, answer }`
   - Repeat until `status = spec_ready`
3. `POST /project/{project_id}/spec/accept`
   - Finalizes project and generates epics

## Figma Flow
1. `POST /project/creation/figma`
   - Body: `name`, `project_key`, `figma_url` (optional `figma_notes`, `description`)
   - Response: `project` + `clarification` with `status = spec_ready` and spec text/url
2. `POST /project/{project_id}/spec/accept`
   - Finalizes project and generates epics
