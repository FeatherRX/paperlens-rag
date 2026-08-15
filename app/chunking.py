from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.ingestion import NormalizedDocument, TextSegment


DEFAULT_MAX_CHUNK_CHARACTERS = 1200
DEFAULT_CHUNK_OVERLAP_CHARACTERS = 150


class Chunk(BaseModel):
    paper_id: str
    chunk_index: int = Field(ge=0)
    text: str = Field(min_length=1)
    section_title: str | None = None
    page_numbers: list[int] = Field(default_factory=list)


@dataclass(frozen=True)
class ChunkingConfig:
    max_characters: int = DEFAULT_MAX_CHUNK_CHARACTERS
    overlap_characters: int = DEFAULT_CHUNK_OVERLAP_CHARACTERS

    def __post_init__(self) -> None:
        if self.max_characters <= 0:
            raise ValueError("max_characters must be greater than zero")
        if self.overlap_characters < 0:
            raise ValueError("overlap_characters must not be negative")
        if self.overlap_characters >= self.max_characters:
            raise ValueError(
                "overlap_characters must be smaller than max_characters"
            )


@dataclass(frozen=True)
class _SourceSpan:
    start: int
    end: int
    page_number: int | None


class ChunkingService:
    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self.config = config or ChunkingConfig()

    def chunk_document(self, document: NormalizedDocument) -> list[Chunk]:
        chunks: list[Chunk] = []
        for section_title, segments in _section_groups(document.segments):
            text, spans = _join_segments(segments)
            for start, end in _chunk_spans(text, self.config):
                chunk_text = text[start:end]
                page_numbers = _page_numbers_for_span(spans, start, end)
                chunks.append(
                    Chunk(
                        paper_id=document.paper_id,
                        chunk_index=len(chunks),
                        text=chunk_text,
                        section_title=section_title,
                        page_numbers=page_numbers,
                    )
                )
        return chunks


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


def _normalized_section_title(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _normalized_text(value)
    return normalized or None


def _section_groups(
    segments: list[TextSegment],
) -> list[tuple[str | None, list[TextSegment]]]:
    groups: list[tuple[str | None, list[TextSegment]]] = []
    current_title: str | None = None
    current_segments: list[TextSegment] = []

    for segment in segments:
        text = _normalized_text(segment.text)
        if not text:
            continue

        section_title = _normalized_section_title(segment.section_title)
        normalized_segment = TextSegment(
            text=text,
            page_number=segment.page_number,
            section_title=section_title,
        )
        if current_segments and section_title != current_title:
            groups.append((current_title, current_segments))
            current_segments = []

        current_title = section_title
        current_segments.append(normalized_segment)

    if current_segments:
        groups.append((current_title, current_segments))
    return groups


def _join_segments(
    segments: list[TextSegment],
) -> tuple[str, list[_SourceSpan]]:
    parts: list[str] = []
    spans: list[_SourceSpan] = []
    cursor = 0

    for segment in segments:
        if parts:
            parts.append("\n\n")
            cursor += 2
        start = cursor
        parts.append(segment.text)
        cursor += len(segment.text)
        spans.append(
            _SourceSpan(
                start=start,
                end=cursor,
                page_number=segment.page_number,
            )
        )
    return "".join(parts), spans


def _chunk_spans(
    text: str,
    config: ChunkingConfig,
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0

    while start < len(text):
        hard_end = min(start + config.max_characters, len(text))
        end = _preferred_end(text, start, hard_end)
        content_start, content_end = _trimmed_span(text, start, end)
        if content_start < content_end:
            spans.append((content_start, content_end))

        if end >= len(text):
            break
        start = _next_start(
            text,
            start,
            end,
            config.overlap_characters,
        )

    return spans


def _preferred_end(text: str, start: int, hard_end: int) -> int:
    if hard_end >= len(text):
        return len(text)

    minimum_break = start + max(1, (hard_end - start) // 2)
    paragraph_break = text.rfind("\n\n", minimum_break, hard_end + 1)
    if paragraph_break >= minimum_break:
        return paragraph_break

    whitespace_break = max(
        text.rfind(" ", minimum_break, hard_end + 1),
        text.rfind("\n", minimum_break, hard_end + 1),
    )
    if whitespace_break >= minimum_break:
        return whitespace_break
    return hard_end


def _next_start(
    text: str,
    previous_start: int,
    previous_end: int,
    overlap_characters: int,
) -> int:
    if overlap_characters == 0:
        return previous_end

    desired_start = max(previous_start, previous_end - overlap_characters)
    if desired_start <= previous_start:
        return previous_end

    boundary = max(
        text.rfind(" ", previous_start + 1, desired_start + 1),
        text.rfind("\n", previous_start + 1, desired_start + 1),
    )
    next_start = boundary + 1 if boundary >= 0 else desired_start
    while next_start < previous_end and text[next_start].isspace():
        next_start += 1
    return next_start if next_start > previous_start else previous_end


def _trimmed_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _page_numbers_for_span(
    source_spans: list[_SourceSpan],
    start: int,
    end: int,
) -> list[int]:
    page_numbers: list[int] = []
    for source_span in source_spans:
        if source_span.start >= end or source_span.end <= start:
            continue
        page_number = source_span.page_number
        if page_number is not None and page_number not in page_numbers:
            page_numbers.append(page_number)
    return page_numbers
