import os

from src.intelligence.agents.base_agent import Agent
from src.intelligence.agents.prompts.document_chunk_extraction_agent_prompt import (
    DOCUMENT_CHUNK_EXTRACTION_TASK,
)


_DOCUMENT_CHUNK_EXTRACTION_DEPLOYMENT = (
    os.getenv("DOCUMENT_CHUNK_EXTRACTION_AGENT_DEPLOYMENT")
    or os.getenv("AZURE_DEPLOYMENT")
    or os.getenv("AZURE_DEPLOYMENT_MINI")
)


DOCUMENT_CHUNK_EXTRACTION_AGENT = Agent(
    name="DocumentChunkExtractionAgent",
    task=DOCUMENT_CHUNK_EXTRACTION_TASK,
    azure_deployment=_DOCUMENT_CHUNK_EXTRACTION_DEPLOYMENT,
    args=["project_name", "chunk_id", "text", "language"],
)
