# Standard Library Imports
import json
from datetime import datetime, timedelta, timezone
from time import perf_counter_ns
from typing import Dict, List, Tuple

# Third-Party Library Imports

from langchain_core.messages import HumanMessage, SystemMessage

# Local Application Imports
from src.utils.llmops_utils import log_to_llmops
from src.utils.logging import add_request_log
from src.utils.team import get_team_name
from src.utils.validation_utils import has_expected_epic_structure, get_code_block
from src.services.setup.variables_setup import (
    gpt40_client,
    gpt40_mini_client,
    LLMOPS_API_KEY,
    MODEL,
)
from src.utils.knowledge_bases import schemas, general
from src.utils.knowledge_bases.embeddings import SofttekOpenAIEmbeddings
from src.schemas.function_response import FunctionResponse
import sys
sys.stdout.reconfigure(encoding='utf-8')


class Message:
    """
    Base class for messages.
    """

    def to_dict(self) -> dict:
        """
        Converts the message to a dictionary format.

        Returns:
            dict: The dictionary representation of the message.
        """
        raise NotImplementedError("Subclasses must implement the `to_dict` method.")


class TextMessage(Message):
    """
    Class for creating text messages.
    """

    def __init__(self, text: str):
        """
        Initializes a TextMessage.

        Args:
            text (str): The text content of the message.
        """
        self.text = text

    def get_text(self):
        return self.text

    def to_dict(self) -> dict:
        """
        Converts the text message to a dictionary format.

        Returns:
            dict: The dictionary representation of the text message.
        """
        return {"type": "text", "text": self.text}


class ImageMessage(Message):
    """
    Class for creating image messages.
    """

    def __init__(self, image_data: str):
        """
        Initializes an ImageMessage.

        Args:
            image_data (str): The Base64-encoded image data.
        """
        # Ensure the image data starts with "data:image"
        if not image_data.startswith("data:image"):
            image_data = f"data:image/jpeg;base64,{image_data}"
        self.image_data = image_data

    def to_dict(self) -> dict:
        """
        Converts the image message to a dictionary format.

        Returns:
            dict: The dictionary representation of the image message.
        """
        return {
            "type": "image_url",
            "image_url": {"url": self.image_data},
        }


class AzureChatService:
    """
    AzureChatService is a service class that interacts with Azure's GPT-based chat models and knowledge bases.

    Attributes:
        api_key (str): The API key for authenticating with Azure services.
        user_name (str): The name of the user interacting with the service.
        user_id (str): The unique identifier of the user.
        team_id (str): The unique identifier of the team the user belongs to.
        knowledge_base_id (str): The ID of the knowledge base to interact with.
    """

    def __init__(self, api_key: str, user_data: dict, knowledge_base_id: str):
        """
        Initializes the AzureChatService with user data and knowledge base information.

        Args:
            api_key (str): The API key for authenticating with Azure services.
            user_data (dict): A dictionary containing user information such as user_name, user_id, and team_id.
            knowledge_base_id (str): The ID of the knowledge base to interact with.
        """
        self.api_key = api_key
        self.user_name = user_data.get_user_name()
        self.user_id = user_data.get_user_id()
        self.team_id = user_data.get_team_id()
        self.user_email = user_data.get_email()
        self.knowledge_base_id = knowledge_base_id

    def create_text_message(self, text: str):
        """
        Creates a text message.

        Args:
            text (str): The text content of the message.

        Returns:
            dict: A dictionary representing the text message.
        """
        return {"type": "text", "text": text}

    def create_image_message(self, image_data: str):
        """
        Creates an image message with a Base64-encoded image.

        Args:
            image_data (str): The Base64-encoded image data.

        Returns:
            dict: A dictionary representing the image message.
        """
        # Check if the image_data already starts with "data:image"
        if not image_data.startswith("data:image"):
            image_data = f"data:image/jpeg;base64,{image_data}"

        return {
            "type": "image_url",
            "image_url": {"url": image_data},
        }

    async def call_with_retry(
        self,
        messages: List,
        key: str = None,
        tries: int = 3,
        expected_keys: List[str] = [],
        return_full_response: bool = False,
        use_images: bool = False
    ) -> Dict:
        """
        This function calls the LLM API with a given prompt and retries if the response is not as expected.

        Args:
            messages (List): The list of messages to send to the LLM API.
            key (str, optional): The key to look for in the JSON response. If None, checks the entire response.
            tries (int): The number of times to try calling the LLM API.
            expected_keys (List[str]): The list of expected keys in the JSON response.
            return_full_response (bool): Whether to return the full JSON response or just the filtered objects.

        Returns:
            Dict: The JSON response from the LLM API or the filtered objects.
        """
        for _ in range(tries):
            response = self.chat_completion(messages, use_images=use_images)
            json_response = None
            try:
                json_response = json.loads(response)
            except json.JSONDecodeError:
                try:
                    # Attempt to load the JSON again within a code block
                    response = get_code_block(response)
                    json_response = json.loads(response)
                except json.JSONDecodeError:
                    return None

            # If no key is specified, validate the entire response
            if key is None:
                if expected_keys:
                    # Use has_expected_epic_structure to validate the entire response
                    if has_expected_epic_structure(json_response, expected_keys):
                        print("[DEBUG] Valid response received from LLM")
                        return json_response
                else:
                    # If no expected keys are provided, return the response as is
                    return json_response
            else:
                # Process the response with the specified key
                objects = json_response.get(key)
                if objects is not None:
                    good_responses = []
                    for obj in objects:
                        if has_expected_epic_structure(obj, expected_keys):
                            good_responses.append(obj)
                            continue
                    if len(good_responses) > 0:
                        if return_full_response:
                            json_response[key] = good_responses
                            return json_response
                        return good_responses
        return None

    def chat_completion(self, messages_list: list, use_images: bool = False) -> str:
        """
        Sends a list of messages to the Azure GPT-based chat model and retrieves the response.

        Args:
            messages_list (list): A list of messages to send to the chat model.
            system_prompt (str, optional): A system-level prompt to guide the model's behavior.

        Returns:
            str: The content of the response from the chat model.
        """

        global_start = perf_counter_ns()

        # Invoke the chat service
        if use_images:
            print("Using GPT-4.0 model with image support")
            client = gpt40_client
        else:
            print("Using GPT-4.0 Mini model")
            client = gpt40_mini_client
        response = client.invoke(messages_list)

        prompt_tokens = response.response_metadata["token_usage"]["prompt_tokens"]
        completion_tokens = response.response_metadata["token_usage"][
            "completion_tokens"
        ]

        # Add the request log to the database
        add_request_log(
            user_id=self.user_id,
            team_id=self.team_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        # Log the request and response to LLMOPS
        team_name = "Unknown Team"
        try:
            # print(f"Logging to LLMOPS: {messages_list}, {response.content}, {prompt_tokens}, {completion_tokens}, {global_start}")
            team_name = get_team_name(self.team_id)
        except Exception as e:
            print(f"Error getting team name: {e}")
            team_name = "Unknown Team"

        additional_kwargs = {
            "team_name": team_name,
        }
        print(f"Logging to LLMOPS with additional kwargs: {additional_kwargs}")
        try:
            prompt = ""
            for message in messages_list:
                prompt += f" {message.content}\n"

            print(f"Final prompt for LLMOPS logging: {prompt}")
            log_to_llmops(
                prompt=prompt,
                response=response.content,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                call_start=global_start,
                created=datetime.now(timezone.utc).isoformat(),
                uid="",
                api_key=LLMOPS_API_KEY,
                model=MODEL,
                additional_kwargs=additional_kwargs,
                email=self.user_email,
                status="success",
                team_id=self.team_id,
            )
        except Exception as e:
            print(f"Error logging to LLMOPS: {e}")
        return response.content

    async def completion_without_knowledge_base(
        self, prompt: str, key: str = None, attempts: int = 3, expected_keys: list = [], return_full_response: bool = False, images: list = []
    ):
        if len(images) > 0:
            image_messages = [ImageMessage(image_data=image).to_dict() for image in images]
            messages = [HumanMessage(content=[TextMessage(text=prompt).to_dict()] + image_messages)]
            return await self.call_with_retry(messages, key, attempts, expected_keys, return_full_response=return_full_response, use_images=True)
        messages = [HumanMessage(content=[TextMessage(text=prompt).to_dict()])]
        return await self.call_with_retry(messages, key, attempts, expected_keys, return_full_response=return_full_response)

    async def simple_completion(self, prompt: str):
        return self.chat_completion([HumanMessage(content=prompt)])

    async def simple_kb_completion(self, prompt: str) -> FunctionResponse:

        if not self.knowledge_base_id:
            raise ValueError(
                "Knowledge base ID is not provided. Cannot proceed with knowledge base interaction."
            )

        try:
            # Initialize the embeddings model
            embeddings_model = SofttekOpenAIEmbeddings(
                model_name="OpenAIEmbeddings", api_key=self.api_key
            )

            # Set up knowledge base with error handling
            kb_response = general.setup_knowledgebase(
                embeddings_model=embeddings_model,
                prompt=prompt,
                knowledge_base_id=self.knowledge_base_id,
                top_k=6,
            )

            print("Knowledge base response:", kb_response)
            if kb_response.is_error():
                print("Knowledge base error:", kb_response.get_error_message())
                return kb_response

            context, _sources = kb_response.get_data()

            # Call the chat model with retry logic
            response = await self.simple_completion(
                prompt=f"Use the following context to answer the question:\n{context}\n\nQuestion: {prompt}"
            )

            return FunctionResponse(status=True, data=response)

        except Exception as e:
            # Raise a RuntimeError for any unexpected errors
            raise RuntimeError(
                f"An error occurred while interacting with the knowledge base: {str(e)}"
            ) from e

    async def completion_with_knowledge_base(
        self,
        prompt: str = "",
        system_prompt: str = None,
        key: str = None,
        attempts: int = 3,
        expected_keys: list = [],
        return_full_response: bool = False
    ) -> FunctionResponse:
        """
        Interacts with a knowledge base to retrieve relevant information and sends the processed messages to the Azure GPT-based chat model.

        Args:
            messages (List): A list of user-provided messages or prompts.
            key (str): The key to look for in the JSON response.
            attempts (int): The number of retry attempts for the API call.
            expected_keys (list): A list of expected keys in the JSON response.

        Returns:
            FunctionResponse: The response from the chat model, including knowledge base context.

        Raises:
            ValueError: If the knowledge base ID is not provided.
            RuntimeError: If an error occurs during the interaction with the knowledge base or the chat model.
        """
        print(
            f"Knowledge base ID in <self.knowledge_base_id>: {self.knowledge_base_id}"
        )
        if not self.knowledge_base_id:
            raise ValueError(
                "Knowledge base ID is not provided. Cannot proceed with knowledge base interaction."
            )

        try:
            # Initialize the embeddings model
            embeddings_model = SofttekOpenAIEmbeddings(
                model_name="OpenAIEmbeddings", api_key=self.api_key
            )

            # Set up the knowledge base with error handling
            print("Setting up the knowledge base...")
            kb_response = general.setup_knowledgebase(
                embeddings_model=embeddings_model,
                prompt=prompt,
                knowledge_base_id=self.knowledge_base_id,
                top_k=6,
            )

            if kb_response.is_error():
                return kb_response
                
            context, _sources = kb_response.get_data()

            new_messages = []
            if system_prompt:
                new_messages.append(SystemMessage(content=system_prompt))
            new_messages.append(
                HumanMessage(
                    content=[
                        TextMessage(
                            text=f"Use the following context to answer the question:\n{context}\n\nQuestion: {prompt}"
                        ).to_dict()
                    ]
                )
            )

            # Call the chat model with retry logic
            response = await self.call_with_retry(
                messages=new_messages,
                key=key,
                tries=attempts,
                expected_keys=expected_keys,
                return_full_response=return_full_response
            )
            print("Response from chat model with knowledge base:", response)

            return FunctionResponse(status=True, data=response)

        except Exception as e:
            # Raise a RuntimeError for any unexpected errors
            raise RuntimeError(
                f"An error occurred while interacting with the knowledge base: {str(e)}"
            ) from e

    async def embed_text(self, prompt: str) -> List[float]:
        """
        Embeds a given text prompt using the Softtek OpenAI embeddings model.

        Args:
            prompt (str): The text prompt to be embedded.

        Returns:
            List[float]: The embedding of the prompt as a list of floats.
        """
        try:
            embeddings_model = SofttekOpenAIEmbeddings(
                model_name="OpenAIEmbeddings", api_key=self.api_key
            )
            embedding = embeddings_model.embed(prompt)
            return embedding
        except Exception as e:
            print(f"Error embedding text: {str(e)}")
        
