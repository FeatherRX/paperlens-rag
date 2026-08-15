import json
from pathlib import Path

import httpx2
import pytest

from app.answer import AnswerResponse, AnswerService
from app.qwen import (
    QwenConfigurationError,
    QwenConfig,
    QwenHTTPError,
    QwenLLMClient,
    QwenResponseError,
    QwenTimeoutError,
    load_qwen_config,
)
from app.retrieval import RetrievalResult


TEST_BASE_URL = "https://dashscope.example/compatible-mode/v1"
TEST_API_KEY = "test-key-not-real"


def _config() -> QwenConfig:
    return QwenConfig(
        api_key=TEST_API_KEY,
        base_url=TEST_BASE_URL,
        model="qwen3.7-flash",
    )


def _client(
    handler: httpx2.MockTransport,
) -> tuple[QwenLLMClient, httpx2.Client]:
    http_client = httpx2.Client(transport=handler)
    return QwenLLMClient(_config(), http_client), http_client


def test_load_qwen_config_reads_dashscope_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", f"  {TEST_API_KEY}  ")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", f"  {TEST_BASE_URL}/  ")
    monkeypatch.setenv("DASHSCOPE_MODEL", "  qwen3.7-flash  ")

    config = load_qwen_config(tmp_path / "missing.env")

    assert config.api_key == TEST_API_KEY
    assert config.base_url == TEST_BASE_URL
    assert config.model == "qwen3.7-flash"
    assert TEST_API_KEY not in repr(config)


def test_load_qwen_config_reads_dotenv_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for name in (
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_BASE_URL",
        "DASHSCOPE_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "\n".join(
            [
                f"DASHSCOPE_API_KEY={TEST_API_KEY}",
                f"DASHSCOPE_BASE_URL={TEST_BASE_URL}",
                "DASHSCOPE_MODEL=qwen3.7-flash",
            ]
        ),
        encoding="utf-8",
    )

    config = load_qwen_config(dotenv_path)

    assert config.api_key == TEST_API_KEY
    assert config.base_url == TEST_BASE_URL
    assert config.model == "qwen3.7-flash"


@pytest.mark.parametrize(
    ("missing_name", "message"),
    [
        ("DASHSCOPE_API_KEY", "DASHSCOPE_API_KEY is required"),
        ("DASHSCOPE_BASE_URL", "DASHSCOPE_BASE_URL is required"),
        ("DASHSCOPE_MODEL", "DASHSCOPE_MODEL is required"),
    ],
)
def test_missing_qwen_configuration_has_clear_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    missing_name: str,
    message: str,
) -> None:
    values = {
        "DASHSCOPE_API_KEY": TEST_API_KEY,
        "DASHSCOPE_BASE_URL": TEST_BASE_URL,
        "DASHSCOPE_MODEL": "qwen3.7-flash",
    }
    for name, value in values.items():
        if name == missing_name:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    with pytest.raises(QwenConfigurationError, match=message):
        load_qwen_config(tmp_path / "missing.env")


@pytest.mark.parametrize(
    "base_url",
    [
        "http://dashscope.example/compatible-mode/v1",
        "https://user:password@dashscope.example/compatible-mode/v1",
        "https://dashscope.example/compatible-mode/v1?key=value",
    ],
)
def test_qwen_base_url_must_be_secure(base_url: str) -> None:
    with pytest.raises(
        QwenConfigurationError,
        match="must be a secure HTTPS base URL",
    ):
        QwenConfig(
            api_key=TEST_API_KEY,
            base_url=base_url,
            model="qwen3.7-flash",
        )


def test_generate_uses_openai_compatible_chat_and_disables_thinking() -> None:
    observed: dict[str, object] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        observed["method"] = request.method
        observed["path"] = request.url.path
        observed["authorization"] = request.headers.get("authorization")
        observed["payload"] = json.loads(request.content)
        return httpx2.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "  Grounded answer.  "}}
                ]
            },
        )

    client, http_client = _client(httpx2.MockTransport(handler))
    try:
        result = client.generate("Evidence-grounded prompt")
    finally:
        http_client.close()

    assert result == "Grounded answer."
    assert observed == {
        "method": "POST",
        "path": "/compatible-mode/v1/chat/completions",
        "authorization": f"Bearer {TEST_API_KEY}",
        "payload": {
            "model": "qwen3.7-flash",
            "messages": [
                {"role": "user", "content": "Evidence-grounded prompt"}
            ],
            "enable_thinking": False,
        },
    }


def test_qwen_client_satisfies_answer_service_contract() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={"choices": [{"message": {"content": "Answer"}}]},
        )

    client, http_client = _client(httpx2.MockTransport(handler))
    evidence = [
        RetrievalResult(
            rank=1,
            score=0.9,
            paper_id="W100",
            chunk_index=2,
            text="Evidence",
            section_title="Results",
            page_numbers=[3],
        )
    ]
    try:
        response = AnswerService(client).generate_answer("Question", evidence)
    finally:
        http_client.close()

    assert response == AnswerResponse(answer="Answer", evidence_count=1)


def test_qwen_timeout_has_clear_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("timed out", request=request)

    client, http_client = _client(httpx2.MockTransport(handler))
    try:
        with pytest.raises(QwenTimeoutError, match="Qwen request timed out"):
            client.generate("Prompt")
    finally:
        http_client.close()


def test_qwen_http_status_has_clear_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(429, json={"error": {"message": "limited"}})

    client, http_client = _client(httpx2.MockTransport(handler))
    try:
        with pytest.raises(
            QwenHTTPError,
            match="Qwen request failed with HTTP status 429",
        ):
            client.generate("Prompt")
    finally:
        http_client.close()


def test_qwen_invalid_json_has_clear_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"not-json")

    client, http_client = _client(httpx2.MockTransport(handler))
    try:
        with pytest.raises(QwenResponseError, match="invalid JSON"):
            client.generate("Prompt")
    finally:
        http_client.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"choices": []},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": None}}]},
    ],
)
def test_qwen_empty_completion_has_clear_error(payload: object) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=payload)

    client, http_client = _client(httpx2.MockTransport(handler))
    try:
        with pytest.raises(QwenResponseError, match="empty response"):
            client.generate("Prompt")
    finally:
        http_client.close()
