import os
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from urllib.parse import urlsplit

import httpx2
from dotenv import load_dotenv


DEFAULT_DOTENV_PATH = Path(__file__).resolve().parents[1] / ".env"
DEFAULT_QWEN_TIMEOUT_SECONDS = 30.0
CHAT_COMPLETIONS_PATH = "/chat/completions"


class QwenClientError(RuntimeError):
    """Base error for the Qwen OpenAI-compatible client."""


class QwenConfigurationError(QwenClientError):
    """Raised when required DashScope configuration is missing or invalid."""


class QwenTimeoutError(QwenClientError):
    """Raised when DashScope does not respond before the configured timeout."""


class QwenHTTPError(QwenClientError):
    """Raised when the DashScope HTTP request fails."""


class QwenResponseError(QwenClientError):
    """Raised when DashScope returns an unusable completion response."""


@dataclass(frozen=True)
class QwenConfig:
    api_key: str = field(repr=False)
    base_url: str
    model: str
    timeout_seconds: float = DEFAULT_QWEN_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        api_key = self.api_key.strip()
        base_url = _validated_base_url(self.base_url)
        model = self.model.strip()
        if not api_key:
            raise QwenConfigurationError("DASHSCOPE_API_KEY is required")
        if not model:
            raise QwenConfigurationError("DASHSCOPE_MODEL is required")
        if self.timeout_seconds <= 0:
            raise QwenConfigurationError(
                "Qwen timeout_seconds must be greater than zero"
            )

        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "model", model)


def load_qwen_config(
    dotenv_path: str | os.PathLike[str] | None = None,
) -> QwenConfig:
    load_dotenv(dotenv_path=dotenv_path or DEFAULT_DOTENV_PATH)
    return QwenConfig(
        api_key=os.getenv("DASHSCOPE_API_KEY", ""),
        base_url=os.getenv("DASHSCOPE_BASE_URL", ""),
        model=os.getenv("DASHSCOPE_MODEL", ""),
    )


class QwenLLMClient:
    def __init__(
        self,
        config: QwenConfig,
        http_client: httpx2.Client | None = None,
    ) -> None:
        self.config = config
        self._http_client = http_client or httpx2.Client()
        self._owns_http_client = http_client is None

    @classmethod
    def from_env(
        cls,
        dotenv_path: str | os.PathLike[str] | None = None,
    ) -> "QwenLLMClient":
        return cls(load_qwen_config(dotenv_path))

    def generate(self, prompt: str) -> str:
        try:
            response = self._http_client.post(
                f"{self.config.base_url}{CHAT_COMPLETIONS_PATH}",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.config.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "enable_thinking": False,
                },
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
        except httpx2.TimeoutException as exc:
            raise QwenTimeoutError("Qwen request timed out") from exc
        except httpx2.HTTPStatusError as exc:
            raise QwenHTTPError(
                "Qwen request failed with HTTP status "
                f"{exc.response.status_code}"
            ) from exc
        except httpx2.RequestError as exc:
            raise QwenHTTPError("Qwen request failed") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise QwenResponseError("Qwen returned invalid JSON") from exc

        content = _completion_content(payload)
        if not content:
            raise QwenResponseError("Qwen returned an empty response")
        return content

    def close(self) -> None:
        if self._owns_http_client:
            self._http_client.close()

    def __enter__(self) -> "QwenLLMClient":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _validated_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if not normalized:
        raise QwenConfigurationError("DASHSCOPE_BASE_URL is required")
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError as exc:
        raise QwenConfigurationError("DASHSCOPE_BASE_URL is invalid") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port not in (None, 443)
    ):
        raise QwenConfigurationError(
            "DASHSCOPE_BASE_URL must be a secure HTTPS base URL"
        )
    return normalized


def _completion_content(payload: object) -> str | None:
    if not isinstance(payload, dict):
        raise QwenResponseError("Qwen returned an invalid response")
    choices = payload.get("choices")
    if not isinstance(choices, list):
        raise QwenResponseError("Qwen returned an invalid response")
    if not choices:
        return None
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise QwenResponseError("Qwen returned an invalid response")
    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise QwenResponseError("Qwen returned an invalid response")
    content = message.get("content")
    if not isinstance(content, str):
        return None
    normalized = content.strip()
    return normalized or None
