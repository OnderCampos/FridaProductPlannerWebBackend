import logging
import threading
from typing import Dict, List, Tuple, Optional

from time import perf_counter_ns
import requests
from datetime import datetime
from src.services.setup.variables_setup import LOGGING_ENDPOINT


def request_task(url: str, json: Dict, headers: Dict = None):
    """Send a request.

    Args:
        url (str): URL to send request to.
        json (Dict): JSON to send in request.
        headers (Dict, optional): Headers to send in request. Defaults to None.
    """
    logging.getLogger(__name__).debug("Sending request to %s.", url)
    logging.getLogger(__name__).debug("Request body: %s", json)
    response = requests.post(url, json=json, headers=headers)
    logging.getLogger(__name__).debug(
        "Request to %s finished with status code %s.", url, response.status_code
    )

def fire_and_forget(url: str, json: Dict, headers: Dict = None):
    """Dispatch a request immediately (synchronous).

    Args:
        url (str): URL to send request to.
        json (Dict): JSON to send in request.
        headers (Dict, optional): Headers to send in request. Defaults to None.
    """
    request_task(url, json, headers)
    #threading.Thread(target=request_task, args=(url, json, headers)).start()

def log_to_llmops(
    prompt: str,
    response: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    call_start: int,
    created: str,
    uid: str,
    api_key: str,
    model: str,
    email: str | None,
    status: str,
    from_cache: bool = False,
    additional_kwargs: Optional[dict] = None,
    team_id: str | None = None,
):
    """Log a request to LLMOPs."""

    # Convert created (ISO string) to seconds and nanoseconds
    dt = datetime.fromisoformat(created)
    seconds = int(dt.timestamp())
    nanoseconds = int(dt.microsecond * 1000)

    # Ensure model and email are in customMetrics
    custom_metrics = dict(additional_kwargs or {})
    custom_metrics["team_id"] = team_id
    if email:
        custom_metrics["email"] = email

    post_body = {
        "prompt": prompt,
        "response": response,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "latency": int((perf_counter_ns() - call_start) / 1e6),
        "createdAt": {
            "seconds": seconds,
            "nanoseconds": nanoseconds
        },
        "userID": uid,
        "from_cache": from_cache,
        "email": email,
        "status": status,
        "model": model,
        "customMetrics": custom_metrics,
    }
    logging.getLogger(__name__).debug("Logging to LLMOPS: %s", post_body)
    headers = {"API-Key": api_key}
    fire_and_forget(url=LOGGING_ENDPOINT, json=post_body, headers=headers)
