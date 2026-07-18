from __future__ import annotations

from math import ceil
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from pptlib.application.library import LibraryFilters

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")


@router.get("/", response_class=HTMLResponse)
def setup_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="setup.html",
        context={"max_file_bytes": request.app.state.settings.max_file_bytes},
    )


@router.get("/library", response_class=HTMLResponse)
def library_page(
    request: Request,
    q: str = "",
    page: int = 1,
    topic: str = "",
    subtopic: str = "",
    page_type: str = "",
    deck_id: str = "",
    view: str = "content",
) -> HTMLResponse:
    page = max(1, page)
    view = view if view in {"content", "source"} else "content"
    filters = LibraryFilters(topic=topic, subtopic=subtopic, page_type=page_type, deck_id=deck_id)
    result = request.app.state.library.search(q, page, 24, filters)
    facets = request.app.state.library.facets()
    decks = request.app.state.library.decks()
    selected_deck = (
        request.app.state.library.get_deck(deck_id)
        if view == "source" and deck_id
        else None
    )
    filter_labels = {
        option.id: option.label
        for option in (*facets.topics, *facets.page_types, *facets.decks)
    }
    selected_ids = {slide.id for slide in request.app.state.library.selection.list()}
    total_pages = max(1, ceil(result.total / result.page_size))
    return templates.TemplateResponse(
        request=request,
        name="library.html",
        context={
            "result": result,
            "slides": result.items,
            "selected_ids": selected_ids,
            "total_pages": total_pages,
            "facets": facets,
            "filters": filters,
            "filter_labels": filter_labels,
            "view": view,
            "decks": decks,
            "selected_deck": selected_deck,
        },
    )


@router.get("/selection", response_class=HTMLResponse)
def selection_page(request: Request) -> HTMLResponse:
    selection = request.app.state.library.selection.list()
    return templates.TemplateResponse(
        request=request,
        name="selection.html",
        context={"slides": selection},
    )


@router.get("/library/slides/{slide_id}", response_class=HTMLResponse)
def slide_preview_page(request: Request, slide_id: str) -> HTMLResponse:
    slide = request.app.state.library.get(slide_id)
    selected = any(item.id == slide.id for item in request.app.state.library.selection.list())
    return templates.TemplateResponse(
        request=request,
        name="slide_detail.html",
        context={"slide": slide, "selected": selected},
    )
