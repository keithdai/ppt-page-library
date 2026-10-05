"""Application ports and use cases for browsing and composing slide pages.

The web layer depends on these small interfaces rather than on SQLite or the
eventual PPTX parser.  The in-memory adapters are intentionally useful for a
local first slice and can be replaced by repository implementations later.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from pptlib.domain.errors import AppError, ErrorCode
from pptlib.domain.taxonomy import PAGE_TYPES, SUBTOPICS, TOPIC_TREE, TOPICS

LEGACY_TOPIC_ALIASES = {
    "战略与规划": "战略与增长",
    "数据与业绩": "数据与经营",
}


@dataclass(frozen=True, slots=True)
class LibraryFilters:
    topic: str = ""
    page_type: str = ""
    deck_id: str = ""
    subtopic: str = ""
    source_format: str = ""

    def to_dict(self) -> dict[str, str]:
        payload = {
            "topic": self.topic,
            "page_type": self.page_type,
            "deck_id": self.deck_id,
        }
        if self.subtopic:
            payload["subtopic"] = self.subtopic
        if self.source_format:
            payload["source_format"] = self.source_format
        return payload


DEFAULT_LIBRARY_FILTERS = LibraryFilters()


def validate_filters(filters: LibraryFilters) -> None:
    if filters.topic and filters.topic not in TOPICS and filters.topic not in LEGACY_TOPIC_ALIASES:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            "unknown topic",
            details={"topic": filters.topic, "allowed": list(TOPICS)},
        )
    if filters.subtopic and filters.subtopic not in SUBTOPICS:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            "unknown subtopic",
            details={"subtopic": filters.subtopic, "allowed": list(SUBTOPICS)},
        )
    if filters.page_type and filters.page_type not in PAGE_TYPES:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            "unknown page type",
            details={"page_type": filters.page_type, "allowed": list(PAGE_TYPES)},
        )


def ensure_pptx_exportable(source_format: str) -> None:
    if source_format != "pptx":
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            "HTML 页面当前支持入库和预览，组合导出将在后续阶段开放",
            details={"source_format": source_format},
        )


@dataclass(frozen=True, slots=True)
class FacetOption:
    id: str
    label: str
    count: int
    children: tuple[FacetOption, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"id": self.id, "label": self.label, "count": self.count}
        if self.children:
            payload["children"] = [option.to_dict() for option in self.children]
        return payload


@dataclass(frozen=True, slots=True)
class LibraryFacets:
    topics: tuple[FacetOption, ...] = ()
    page_types: tuple[FacetOption, ...] = ()
    decks: tuple[FacetOption, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "topics": [option.to_dict() for option in self.topics],
            "page_types": [option.to_dict() for option in self.page_types],
            "decks": [option.to_dict() for option in self.decks],
        }


@dataclass(frozen=True, slots=True)
class SlideSummary:
    id: str
    deck_id: str
    deck_name: str
    slide_number: int
    title: str
    text: str = ""
    thumbnail_url: str | None = None
    preview_url: str | None = None
    tags: tuple[str, ...] = ()
    topic: str = ""
    page_type: str = ""
    subtopic: str = ""
    confidence: str = ""
    classification_source: str = ""
    classifier_version: str = ""
    source_format: str = "pptx"
    page_key: str = ""
    page_kind: str = "ooxml"
    capabilities: dict[str, object] = field(default_factory=dict)
    warnings: list[object] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "deck_id": self.deck_id,
            "deck_name": self.deck_name,
            "slide_number": self.slide_number,
            "title": self.title,
            "text": self.text,
            "thumbnail_url": self.thumbnail_url,
            "preview_url": self.preview_url,
            "tags": list(self.tags),
            "topic": self.topic,
            "subtopic": self.subtopic,
            "page_type": self.page_type,
            "confidence": self.confidence,
            "classification_source": self.classification_source,
            "classifier_version": self.classifier_version,
            "source_format": self.source_format,
            "page_key": self.page_key,
            "page_kind": self.page_kind,
            "capabilities": dict(self.capabilities),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class DeckSummary:
    """A current imported deck as shown by the source-first library view."""

    id: str
    name: str
    slide_count: int
    cover_thumbnail_url: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "slide_count": self.slide_count,
            "cover_thumbnail_url": self.cover_thumbnail_url,
        }


@dataclass(frozen=True, slots=True)
class SearchPage:
    items: tuple[SlideSummary, ...]
    total: int
    page: int
    page_size: int
    query: str
    filters: LibraryFilters = LibraryFilters()

    def to_dict(self) -> dict[str, object]:
        return {
            "items": [item.to_dict() for item in self.items],
            "total": self.total,
            "page": self.page,
            "page_size": self.page_size,
            "query": self.query,
            "filters": self.filters.to_dict(),
        }


class SlideCatalog(Protocol):
    def search(
        self,
        query: str,
        *,
        page: int,
        page_size: int,
        filters: LibraryFilters = DEFAULT_LIBRARY_FILTERS,
    ) -> SearchPage: ...

    def facets(self) -> LibraryFacets: ...

    def get(self, slide_id: str) -> SlideSummary | None: ...

    def decks(self) -> tuple[DeckSummary, ...]: ...

    def get_deck(self, deck_id: str) -> DeckSummary | None: ...


class SelectionStore(Protocol):
    def list(self) -> tuple[SlideSummary, ...]: ...

    def add(self, slide: SlideSummary) -> tuple[SlideSummary, ...]: ...

    def remove(self, slide_id: str) -> tuple[SlideSummary, ...]: ...

    def reorder(self, slide_ids: Sequence[str]) -> tuple[SlideSummary, ...]: ...


class InMemorySlideCatalog:
    """Reference adapter used until the ingestion repository is available."""

    def __init__(self, slides: Iterable[SlideSummary] = ()) -> None:
        self._slides = tuple(slides)
        self._by_id = {slide.id: slide for slide in self._slides}

    def search(
        self,
        query: str,
        *,
        page: int,
        page_size: int,
        filters: LibraryFilters = DEFAULT_LIBRARY_FILTERS,
    ) -> SearchPage:
        validate_filters(filters)
        normalized = query.strip().casefold()
        matches = self._slides
        if normalized:
            terms = tuple(term for term in normalized.split() if term)
            matches = tuple(
                slide
                for slide in self._slides
                if all(
                    term
                    in " ".join(
                        (slide.deck_name, slide.title, slide.text, " ".join(slide.tags))
                    ).casefold()
                    for term in terms
                )
            )
        if filters.topic:
            canonical = LEGACY_TOPIC_ALIASES.get(filters.topic, filters.topic)
            matches = tuple(
                slide
                for slide in matches
                if slide.topic in {filters.topic, canonical}
            )
        if filters.subtopic:
            matches = tuple(slide for slide in matches if slide.subtopic == filters.subtopic)
        if filters.page_type:
            matches = tuple(slide for slide in matches if slide.page_type == filters.page_type)
        if filters.deck_id:
            matches = tuple(slide for slide in matches if slide.deck_id == filters.deck_id)
        if filters.source_format:
            matches = tuple(
                slide for slide in matches if slide.source_format == filters.source_format
            )
        start = (page - 1) * page_size
        return SearchPage(
            tuple(matches[start : start + page_size]),
            len(matches),
            page,
            page_size,
            query,
            filters,
        )

    def facets(self) -> LibraryFacets:
        def aliases_for(topic: str) -> set[str]:
            return {
                topic,
                *[
                    legacy
                    for legacy, canonical in LEGACY_TOPIC_ALIASES.items()
                    if canonical == topic
                ],
            }

        topics = tuple(
            FacetOption(
                topic,
                topic,
                sum(slide.topic in aliases_for(topic) for slide in self._slides),
                tuple(
                    FacetOption(
                        subtopic,
                        subtopic,
                        sum(
                            slide.subtopic == subtopic
                            and slide.topic in aliases_for(topic)
                            for slide in self._slides
                        ),
                    )
                    for subtopic in TOPIC_TREE[topic]
                ),
            )
            for topic in TOPICS
        )
        page_types = tuple(
            FacetOption(
                page_type,
                page_type,
                sum(slide.page_type == page_type for slide in self._slides),
            )
            for page_type in PAGE_TYPES
        )
        decks: dict[str, FacetOption] = {}
        for slide in self._slides:
            current = decks.get(slide.deck_id)
            decks[slide.deck_id] = FacetOption(
                slide.deck_id,
                slide.deck_name,
                (current.count if current else 0) + 1,
            )
        return LibraryFacets(topics, page_types, tuple(decks.values()))

    def get(self, slide_id: str) -> SlideSummary | None:
        return self._by_id.get(slide_id)

    def decks(self) -> tuple[DeckSummary, ...]:
        grouped: dict[str, list[SlideSummary]] = {}
        for slide in self._slides:
            grouped.setdefault(slide.deck_id, []).append(slide)
        return tuple(
            DeckSummary(
                deck_id,
                min(slides, key=lambda slide: slide.slide_number).deck_name,
                len(slides),
                min(slides, key=lambda slide: slide.slide_number).thumbnail_url,
            )
            for deck_id, slides in sorted(
                grouped.items(),
                key=lambda item: (item[1][0].deck_name.casefold(), item[0]),
            )
        )

    def get_deck(self, deck_id: str) -> DeckSummary | None:
        return next((deck for deck in self.decks() if deck.id == deck_id), None)


class InMemorySelectionStore:
    def __init__(self) -> None:
        self._items: list[SlideSummary] = []

    def list(self) -> tuple[SlideSummary, ...]:
        return tuple(self._items)

    def add(self, slide: SlideSummary) -> tuple[SlideSummary, ...]:
        if all(item.id != slide.id for item in self._items):
            self._items.append(slide)
        return self.list()

    def remove(self, slide_id: str) -> tuple[SlideSummary, ...]:
        self._items = [item for item in self._items if item.id != slide_id]
        return self.list()

    def reorder(self, slide_ids: Sequence[str]) -> tuple[SlideSummary, ...]:
        current = {item.id: item for item in self._items}
        if set(slide_ids) != set(current) or len(slide_ids) != len(current):
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                "reorder must contain every selected slide exactly once",
                details={"expected_ids": list(current)},
            )
        self._items = [current[slide_id] for slide_id in slide_ids]
        return self.list()


class PageLibraryService:
    def __init__(self, catalog: SlideCatalog, selection: SelectionStore) -> None:
        self.catalog = catalog
        self.selection = selection

    def search(
        self,
        query: str,
        page: int,
        page_size: int,
        filters: LibraryFilters = DEFAULT_LIBRARY_FILTERS,
    ) -> SearchPage:
        validate_filters(filters)
        return self.catalog.search(query, page=page, page_size=page_size, filters=filters)

    def facets(self) -> LibraryFacets:
        return self.catalog.facets()

    def get(self, slide_id: str) -> SlideSummary:
        slide = self.catalog.get(slide_id)
        if slide is None:
            raise AppError(ErrorCode.NOT_FOUND, "slide not found", details={"slide_id": slide_id})
        return slide

    def decks(self) -> tuple[DeckSummary, ...]:
        return self.catalog.decks()

    def get_deck(self, deck_id: str) -> DeckSummary:
        deck = self.catalog.get_deck(deck_id)
        if deck is None:
            raise AppError(ErrorCode.NOT_FOUND, "deck not found", details={"deck_id": deck_id})
        return deck

    def add(self, slide_id: str) -> tuple[SlideSummary, ...]:
        return self.selection.add(self.get(slide_id))

    def remove(self, slide_id: str) -> tuple[SlideSummary, ...]:
        return self.selection.remove(slide_id)

    def reorder(self, slide_ids: Sequence[str]) -> tuple[SlideSummary, ...]:
        return self.selection.reorder(slide_ids)

    def selection_dict(self) -> dict[str, object]:
        items = self.selection.list()
        return {"items": [item.to_dict() for item in items], "count": len(items)}
