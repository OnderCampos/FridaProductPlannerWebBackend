from abc import ABC, abstractmethod
from typing import Any, Dict, List

import requests
from fastapi import HTTPException
from typing_extensions import override

from src.utils.knowledge_bases import setup


class EmbeddingsModel(ABC):
    """
    # Embeddings Model
    Creates an abstract base class for an embeddings model. Used as a base class for implementing different types of embeddings models.

    ## Methods
    - `embed`: Method to embed text. Must be implemented by the child class.
    """

    def __init__(self, **kwargs: Any):
        """Initializes the EmbeddingsModel class."""
        super().__init__()

    @abstractmethod
    def embed(self, prompt: str, **kwargs: Any) -> List[float]:
        """
        This is an abstract method for embedding a prompt into a list of floats. This method must be implemented by a subclass.

        Args:
        - `prompt` (str): The string prompt to embed.
        - `**kwargs` (Any): Additional arguments for implementation-defined use.

        Returns:
        - (List[float]): The embedding of the prompt as a list of floats.

        Raises:
        - NotImplementedError: When this abstract method is called without being implemented in a subclass.
        """
        raise NotImplementedError("embed method must be overridden")


class SofttekOpenAIEmbeddings(EmbeddingsModel):
    """
    # Softtek OpenAI Embeddings
    Creates a Softtek OpenAI embeddings model. This class is a subclass of the EmbeddingsModel abstract base class.

    ## Attributes
    - `model_name`: Embeddings model name.
    - `api_key`: API key for the Softtek OpenAI API.

    ## Methods
    - `embed`: Embeds a prompt into a list of floats.
    """

    @override
    def __init__(self, model_name: str, api_key: str):
        """Initializes the SofttekOpenAIEmbeddings class.

        Args:
            `model_name` (str): Name of the embeddings model.

            `api_key` (str): API key for the Softtek OpenAI API.
        """
        super().__init__()
        self.__model_name = model_name
        self.__api_key = api_key

    @property
    def model_name(self) -> str:
        """Embeddings model name."""
        return self.__model_name

    @override
    def embed(self, prompt: str, additional_kwargs: Dict = {}, **kwargs) -> List[float]:
        """Embeds a prompt into a list of floats.

        Args:
            `prompt` (str): Prompt to embed.

            `additional_kwargs` (Dict, optional): Additional keyword arguments. Defaults to {}.

        Returns:
            `List[float]`: Embedding of the prompt as a list of floats.

        Raises:
            (Exception): When the API returns a non-200 status code.
        """
        response = requests.post(
            f"{setup.EMBEDDING_URL}/embeddings/",
            headers={"api-key": self.__api_key},
            json={
                "input": prompt,
                "model": self.model_name,
                "additional_kwargs": additional_kwargs,
            },
        )

        if not response.ok:
            raise HTTPException(
                status_code=response.status_code,
                detail=response.json(),
            )

        return response.json()["data"][0]["embedding"]
