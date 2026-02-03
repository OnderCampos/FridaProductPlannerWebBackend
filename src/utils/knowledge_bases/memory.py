from typing import Dict, List, Literal

from src.utils.knowledge_bases import schemas


class Memory:
    """
    # Memory
    Represents the memory of the assistant. Stores all the messages that have been exchanged between the user and the assistant.

    ## Methods
    - `add_message`: Adds a message to the memory.
    - `delete_message`: Deletes a message from the memory.
    - `get_message`: Returns a message from the memory.
    - `get_messages`: Returns all the messages from the memory. It is a copy of the original list of messages. Appending to this list will not affect the original list.
    - `clear_messages`: Clears all messages from the memory.
    - `messages_to_dict`: Returns all the messages from the memory in a list of dictionaries.
    """

    def __init__(self):
        """Initializes the Memory class."""
        self.__messages: List[schemas.Message] = []

    @classmethod
    def from_messages(cls, messages: List[schemas.Message]):
        """Initializes the Memory class from a list of messages.

        Args:
            `messages` (List[Message]): The list of messages to initialize the memory with.

        Returns:
            (Memory): The initialized memory.
        """
        memory = cls()
        for message in messages:
            memory.add_message(message.role, message.content)
        return memory

    def add_message(
        self, role: Literal["system", "user", "assistant", "function"], content: str
    ):
        """Adds a message to the memory.

        Args:
            `role` (Literal["system", "user", "assistant", "function"]): The role of the message.
            `content` (str): The content of the message.
        """
        self.__messages.append(schemas.Message(role=role, content=content))

    def delete_message(self, index: int):
        """Deletes a message from the memory.

        Args:
            `index` (int): The index of the message to delete.
        """
        self.__messages.pop(index)

    def get_message(self, index: int) -> schemas.Message:
        """Returns a message from the memory.

        Args:
            `index` (int): The index of the message to return.

        Returns:
            (Message): The message at the given index.
        """
        return self.__messages[index]

    def get_messages(self) -> List[schemas.Message]:
        """Returns all the messages from the memory. It is a copy of the original list of messages. Appending to this list will not affect the original list.

        Returns:
            (List[Message]): A copy of the list of messages.
        """
        return self.__messages.copy()

    def clear_messages(self):
        """Clears all messages from the memory."""
        self.__messages.clear()

    def messages_to_dict(self) -> List[Dict]:
        """Returns all the messages from the memory in a list of dictionaries.

        Returns:
            (List[Dict]): The list of messages in dictionary format.
        """
        return [message.model_dump() for message in self.__messages]
