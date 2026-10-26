"""One chat completion against the provider TestBench chose.

The backend resolves the provider on every run — from the AI Providers page, or
from Helm when Helm manages it — and sends the endpoint and model with the
request. A GUI-managed provider's key comes with it; a Helm-managed key is
mounted into this container instead and never passes through the backend.
"""

import httpx


class AiError(Exception):
    """The model could not be asked, or did not answer usefully."""


def chat(configuration: dict, messages: list[dict], *, helm_api_key: str = "", timeout: float = 300.0,
         transport: httpx.BaseTransport | None = None) -> str:
    url = str(configuration.get("url") or "").rstrip("/")
    model = configuration.get("model")
    if not url or not model:
        raise AiError("No AI endpoint and model are configured for AI Test Summary")
    key = helm_api_key if configuration.get("locked") else (configuration.get("api_key") or "")
    headers = {"Content-Type": "application/json", **(configuration.get("custom_headers") or {})}
    if key:
        headers["Authorization"] = f"Bearer {key}"
        if configuration.get("provider_type") == "azure-openai":
            headers["api-key"] = key
    # Only the model and the messages: temperature and token limits are not
    # accepted the same way by every model, and the defaults suit a report.
    body = {"model": model, "messages": messages}
    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            response = client.post(f"{url}/chat/completions", headers=headers, json=body)
    except httpx.TimeoutException as exc:
        raise AiError(f"The AI provider did not answer within {int(timeout)} seconds") from exc
    except httpx.HTTPError as exc:
        raise AiError(f"Could not reach the AI provider: {exc}") from exc
    if response.status_code >= 400:
        raise AiError(f"The AI provider refused the request ({response.status_code}): {_reason(response)}")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AiError("The AI provider's answer was not a chat completion") from exc
    if not isinstance(content, str) or not content.strip():
        raise AiError("The AI provider returned an empty answer")
    return content.strip()


def _reason(response: httpx.Response) -> str:
    try:
        error = response.json().get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)[:300]
        if error:
            return str(error)[:300]
    except ValueError:
        pass
    return response.text[:300] or response.reason_phrase
