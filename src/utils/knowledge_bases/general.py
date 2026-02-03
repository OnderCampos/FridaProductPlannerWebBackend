import os
import random
import threading
from datetime import datetime, timedelta, timezone
from time import perf_counter_ns
from typing import Dict, List, Tuple

from pydantic import BaseModel

from src.utils.knowledge_bases import schemas, setup
from src.utils.knowledge_bases.embeddings import SofttekOpenAIEmbeddings
from src.utils.knowledge_bases.memory import Memory
from src.utils.knowledge_bases.vectorStores import PineconeVectorStore
from src.schemas.function_response import FunctionResponse


class AppData(BaseModel):
    """
    Represents application data containing organization and application identifiers.

    Attributes:
        org_id (str): Unique identifier for the organization.
        app_id (str): Unique identifier for the application.
    """

    org_id: str
    app_id: str



def setup_knowledgebase(
    embeddings_model: SofttekOpenAIEmbeddings,
    prompt: str,
    knowledge_base_id: str,
    top_k: int,
) -> FunctionResponse:
    """Setup the knowledge base for a chatbot.

    Args:
        embeddings_model (SofttekOpenAIEmbeddings): Model to embed the prompt.
        prompt (str): The prompt to search for.
        knowledgebase_id (str): The ID of the knowledge base.
        top_k (int): The number of similar vectors to return.
        memory (Memory): The memory of the chatbot.
        description (str): The description of the knowledge base.

    Returns:
        Tuple[str, List[str], bool, str]: The context, sources, success status, and error message (if any).
    """
    try:
        # * Initialize the knowledge base
        knowledge_base = PineconeVectorStore(
            api_key=setup.PINECONE_API_KEY,
            environment=setup.PINECONE_ENVIRONMENT,
            index_name=setup.KNOWLEDGEBASE_INDEX_NAME,
        )

        # * Embed prompt
        try:
            embeddings = embeddings_model.embed(prompt)
        except Exception as e:
            print(f"Error embedding prompt: {str(e)}")
            return FunctionResponse(
                status=False,
                error=f"Error embedding prompt: {str(e)}"
            )

        # * Get similar vectors
        try:
            similar_vectors = knowledge_base.search(
                vector=schemas.UtilsVector(embeddings=embeddings),
                namespace=knowledge_base_id,
                top_k=top_k,
            )
        except Exception as e:
            print(f"Error searching vectors: {str(e)}")
            return FunctionResponse(
                status=False,
                error=f"Error searching vectors: {str(e)}"
            )

        # * Extract context
        all_sources = [vector.metadata["source"] for vector in similar_vectors]
        print("All sources from similar vectors:", all_sources)
        if all_sources:
            sources = []
            for source in all_sources:
                if source not in sources:
                    sources.append(source)
        else:
            print("Knowledge base is empty or no relevant results found.")
            return FunctionResponse(
                status=False,
                error="Knowledge base is empty or no relevant results found."
        
            )

        context = "\n".join([vector.metadata["text"] for vector in similar_vectors])
        return FunctionResponse(
            status=True,
            data=(context, sources)
        )

    except Exception as e:
        error_message = f"Error setting up knowledge base: {str(e)}"
        return FunctionResponse(
            status=False,
            error=error_message
        )
