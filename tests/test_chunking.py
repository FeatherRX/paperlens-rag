from datetime import UTC, datetime

import pytest

from app.chunking import ChunkingConfig, ChunkingService
from app.ingestion import NormalizedDocument, TextSegment


def _document(*segments: TextSegment) -> NormalizedDocument:
    return NormalizedDocument(
        paper_id="W123",
        title="Chunking test paper",
        license="cc-by",
        source_type="grobid_xml",
        segments=list(segments),
        character_count=sum(len(segment.text) for segment in segments),
        ingested_at=datetime.now(UTC).isoformat(),
        source_version="test-version",
    )


def test_short_document_produces_one_chunk() -> None:
    chunks = ChunkingService().chunk_document(
        _document(TextSegment(text="A short document.", section_title="Intro"))
    )

    assert [chunk.model_dump() for chunk in chunks] == [
        {
            "paper_id": "W123",
            "chunk_index": 0,
            "text": "A short document.",
            "section_title": "Intro",
            "page_numbers": [],
        }
    ]


def test_adjacent_segments_are_merged() -> None:
    chunks = ChunkingService(
        ChunkingConfig(max_characters=100, overlap_characters=10)
    ).chunk_document(
        _document(
            TextSegment(text="First paragraph."),
            TextSegment(text="Second paragraph."),
            TextSegment(text="Third paragraph."),
        )
    )

    assert len(chunks) == 1
    assert chunks[0].text == (
        "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    )


def test_oversized_content_is_split_at_the_configured_limit() -> None:
    text = " ".join(f"token-{index:03d}" for index in range(80))
    chunks = ChunkingService(
        ChunkingConfig(max_characters=120, overlap_characters=20)
    ).chunk_document(_document(TextSegment(text=text)))

    assert len(chunks) > 1
    assert all(0 < len(chunk.text) <= 120 for chunk in chunks)
    assert chunks[0].text.startswith("token-000")
    assert chunks[-1].text.endswith("token-079")
    assert [chunk.chunk_index for chunk in chunks] == list(
        range(len(chunks))
    )


def test_overlap_is_preserved_for_long_unbroken_text() -> None:
    chunks = ChunkingService(
        ChunkingConfig(max_characters=10, overlap_characters=3)
    ).chunk_document(_document(TextSegment(text="abcdefghijklmnopqrstuvwxyz")))

    assert chunks[0].text == "abcdefghij"
    assert chunks[1].text == "hijklmnopq"
    assert chunks[0].text[-3:] == chunks[1].text[:3]


def test_chunks_do_not_cross_section_boundaries() -> None:
    chunks = ChunkingService(
        ChunkingConfig(max_characters=200, overlap_characters=20)
    ).chunk_document(
        _document(
            TextSegment(text="Introduction one.", section_title="Introduction"),
            TextSegment(text="Introduction two.", section_title="Introduction"),
            TextSegment(text="Method one.", section_title="Methods"),
        )
    )

    assert [chunk.section_title for chunk in chunks] == [
        "Introduction",
        "Methods",
    ]
    assert chunks[0].text == "Introduction one.\n\nIntroduction two."
    assert chunks[1].text == "Method one."


def test_page_metadata_is_aggregated_in_source_order() -> None:
    chunks = ChunkingService(
        ChunkingConfig(max_characters=200, overlap_characters=20)
    ).chunk_document(
        _document(
            TextSegment(text="Page one text.", page_number=1),
            TextSegment(text="More page one text.", page_number=1),
            TextSegment(text="Page two text.", page_number=2),
        )
    )

    assert len(chunks) == 1
    assert chunks[0].page_numbers == [1, 2]


def test_empty_segments_do_not_generate_chunks() -> None:
    chunks = ChunkingService().chunk_document(
        _document(
            TextSegment(text=""),
            TextSegment(text="  \n\t  ", section_title="Empty"),
        )
    )

    assert chunks == []


def test_chunk_order_and_output_are_deterministic() -> None:
    document = _document(
        TextSegment(text="Alpha " * 20, page_number=1),
        TextSegment(text="Beta " * 20, page_number=2),
        TextSegment(text="Gamma " * 20, page_number=3),
    )
    service = ChunkingService(
        ChunkingConfig(max_characters=80, overlap_characters=12)
    )

    first = service.chunk_document(document)
    second = service.chunk_document(document)

    assert first == second
    assert [chunk.chunk_index for chunk in first] == list(range(len(first)))
    assert first[0].text.startswith("Alpha")
    assert first[-1].text.endswith("Gamma")


@pytest.mark.parametrize(
    ("max_characters", "overlap_characters"),
    [(0, 0), (100, -1), (100, 100), (100, 101)],
)
def test_invalid_chunking_configuration_is_rejected(
    max_characters: int,
    overlap_characters: int,
) -> None:
    with pytest.raises(ValueError):
        ChunkingConfig(
            max_characters=max_characters,
            overlap_characters=overlap_characters,
        )
