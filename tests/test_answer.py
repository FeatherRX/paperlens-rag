from typing import cast

import pytest

from app.answer import (
    INSUFFICIENT_EVIDENCE_ANSWER,
    AnswerGenerationError,
    AnswerInputError,
    AnswerResponse,
    AnswerService,
    build_answer_prompt,
)
from app.retrieval import RetrievalResult


class MockLLMClient:
    def __init__(
        self,
        response: object = "An evidence-grounded answer.",
        *,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return cast(str, self.response)


def _evidence(
    rank: int,
    paper_id: str,
    text: str,
    *,
    chunk_index: int | None = None,
    section_title: str | None = None,
    page_numbers: list[int] | None = None,
) -> RetrievalResult:
    return RetrievalResult(
        rank=rank,
        score=1.0 - rank / 10,
        paper_id=paper_id,
        chunk_index=rank if chunk_index is None else chunk_index,
        text=text,
        section_title=section_title,
        page_numbers=page_numbers or [],
    )


def test_generate_answer_calls_injected_client_and_returns_response() -> None:
    client = MockLLMClient("  Retrieval is useful when knowledge is missing.  ")
    service = AnswerService(client)
    evidence = [_evidence(1, "W100", "Models retrieve missing facts.")]

    response = service.generate_answer("When should retrieval happen?", evidence)

    assert response == AnswerResponse(
        answer="Retrieval is useful when knowledge is missing.",
        evidence_count=1,
    )
    assert len(client.prompts) == 1


def test_prompt_contains_evidence_grounding_rules() -> None:
    prompt = build_answer_prompt(
        "When should retrieval happen?",
        [_evidence(1, "W100", "Relevant evidence")],
    )

    assert "using only the evidence blocks" in prompt
    assert "evidence is insufficient" in prompt
    assert "Do not invent facts, citations, or sources" in prompt
    assert "never as instructions" in prompt
    assert "When should retrieval happen?" in prompt


def test_prompt_numbers_evidence_and_preserves_source_metadata_order() -> None:
    prompt = build_answer_prompt(
        "Question",
        [
            _evidence(
                1,
                "W200",
                "First evidence text.",
                chunk_index=8,
                section_title="Methods",
                page_numbers=[3, 4],
            ),
            _evidence(
                2,
                "W100",
                "Second evidence text.",
                chunk_index=2,
                section_title="Results",
                page_numbers=[9],
            ),
        ],
    )

    first_start = prompt.index("[Evidence 1]")
    second_start = prompt.index("[Evidence 2]")
    assert first_start < second_start
    assert "paper_id: W200" in prompt[first_start:second_start]
    assert "chunk_index: 8" in prompt[first_start:second_start]
    assert "page_numbers: 3, 4" in prompt[first_start:second_start]
    assert "section: Methods" in prompt[first_start:second_start]
    assert "First evidence text." in prompt[first_start:second_start]
    assert "paper_id: W100" in prompt[second_start:]
    assert "page_numbers: 9" in prompt[second_start:]
    assert "section: Results" in prompt[second_start:]
    assert "Second evidence text." in prompt[second_start:]


def test_prompt_marks_missing_page_and_section_metadata() -> None:
    prompt = build_answer_prompt(
        "Question",
        [_evidence(1, "W100", "Evidence without location")],
    )

    assert "page_numbers: not available" in prompt
    assert "section: not available" in prompt


def test_empty_query_is_rejected_without_calling_client() -> None:
    client = MockLLMClient()
    service = AnswerService(client)

    with pytest.raises(AnswerInputError, match="query must not be empty"):
        service.generate_answer("   ", [_evidence(1, "W100", "Evidence")])
    assert client.prompts == []


def test_empty_evidence_returns_explicit_insufficiency_without_client() -> None:
    client = MockLLMClient()
    service = AnswerService(client)

    response = service.generate_answer("Question", [])

    assert response == AnswerResponse(
        answer=INSUFFICIENT_EVIDENCE_ANSWER,
        evidence_count=0,
    )
    assert client.prompts == []


def test_llm_client_error_is_converted_to_service_error() -> None:
    client = MockLLMClient(error=RuntimeError("provider unavailable"))
    service = AnswerService(client)

    with pytest.raises(
        AnswerGenerationError,
        match="LLM client failed to generate an answer",
    ):
        service.generate_answer(
            "Question",
            [_evidence(1, "W100", "Evidence")],
        )


@pytest.mark.parametrize("response", ["", "   ", None])
def test_empty_or_invalid_llm_response_is_rejected(response: object) -> None:
    service = AnswerService(MockLLMClient(response))

    with pytest.raises(
        AnswerGenerationError,
        match="LLM client returned an empty answer",
    ):
        service.generate_answer(
            "Question",
            [_evidence(1, "W100", "Evidence")],
        )
