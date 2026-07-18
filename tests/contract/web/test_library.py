from pathlib import Path

from fastapi.testclient import TestClient

from pptlib.application.library import InMemorySlideCatalog, LibraryFilters, SlideSummary
from pptlib.config import load_settings
from pptlib.domain.taxonomy import TOPICS
from pptlib.web.app import create_app


def _client(tmp_path: Path) -> TestClient:
    slides = (
        SlideSummary(
            id="slide_growth",
            deck_id="deck_strategy",
            deck_name="战略规划.pptx",
            slide_number=3,
            title="增长策略",
            text="用户增长与市场扩张",
            tags=("strategy",),
        ),
        SlideSummary(
            id="slide_roadmap",
            deck_id="deck_strategy",
            deck_name="战略规划.pptx",
            slide_number=4,
            title="产品路线图",
            text="季度交付计划",
        ),
    )
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    return TestClient(create_app(settings, catalog=InMemorySlideCatalog(slides)))


def test_slide_search_and_preview_contract(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/v1/slides", params={"q": "增长"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["total"] == 1
    assert payload["data"]["items"][0]["id"] == "slide_growth"

    page = client.get("/library")
    assert page.status_code == 200
    assert "library-toolbar" in page.text
    assert "slide-card-actions" in page.text
    assert 'href="/library/slides/slide_growth"' in page.text
    preview = client.get("/library/slides/slide_growth")
    assert preview.status_code == 200
    assert "增长策略" in preview.text


def test_library_marks_selected_slides_and_exposes_pagination(tmp_path: Path) -> None:
    client = _client(tmp_path)
    added = client.post("/api/v1/selection/items", json={"slide_id": "slide_growth"})
    assert added.status_code == 200

    page = client.get("/library")

    assert page.status_code == 200
    assert 'data-selected="true"' in page.text
    assert 'class="add-slide is-added"' in page.text
    assert "已在选片单" in page.text
    assert 'aria-label="下一页"' not in page.text


def test_library_workbench_has_dual_entry_navigation_and_selection_drawer(tmp_path: Path) -> None:
    client = _client(tmp_path)

    page = client.get(
        "/library",
        params={"q": "增长", "topic": "战略与增长", "deck_id": "deck_strategy", "view": "source"},
    )

    assert page.status_code == 200
    assert "content-nav" in page.text
    assert "source-nav" in page.text
    assert "view-switch" in page.text
    assert "filter-chip" in page.text
    assert "selection-drawer" in page.text
    assert "page-type-nav" in page.text
    assert "流程与步骤" in page.text
    assert 'data-filter-topic="战略与增长"' in page.text
    assert 'data-filter-deck="deck_strategy"' in page.text
    assert 'aria-current="page"' in page.text
    assert "view=source" in page.text


def test_content_tree_keeps_single_all_topics_entry_and_nests_subtopics(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    page = client.get("/library", params={"view": "content"})

    assert page.status_code == 200
    assert page.text.count(">全部主题 <") == 1
    assert ">全部战略与增长 <" not in page.text
    assert ">全部组织与人才 <" not in page.text
    assert 'class="subtopic-list"' in page.text
    assert "subtopic-link" in page.text or "没有匹配的页面" in page.text


def test_content_tree_allows_long_topic_labels_to_wrap_in_one_column() -> None:
    stylesheet = (
        Path(__file__).resolve().parents[3] / "src" / "pptlib" / "web" / "static" / "app.css"
    ).read_text(encoding="utf-8")

    assert ".topic-summary {" in stylesheet
    assert "white-space: normal" in stylesheet
    assert "word-break: normal" in stylesheet
    assert ".content-nav { display: block;" in stylesheet


def test_content_tree_overrides_global_nav_row_layout() -> None:
    stylesheet = (
        Path(__file__).resolve().parents[3] / "src" / "pptlib" / "web" / "static" / "app.css"
    ).read_text(encoding="utf-8")

    desktop_rules = stylesheet.split("@media", 1)[0]
    assert ".content-nav {" in desktop_rules
    assert "display: block;" in desktop_rules
    assert "flex-direction: column;" in desktop_rules


def test_library_view_switch_changes_mode_context_and_source_focus(tmp_path: Path) -> None:
    client = _client(tmp_path)

    content_page = client.get("/library", params={"view": "content"})
    source_page = client.get("/library", params={"view": "source"})

    assert content_page.status_code == 200
    assert source_page.status_code == 200
    assert 'data-library-view="content"' in content_page.text
    assert 'class="view-context content-mode-panel"' in content_page.text
    assert 'data-library-view="source"' in source_page.text
    assert 'class="view-context source-mode-panel"' in source_page.text
    assert '<details class="source-nav" open>' in source_page.text
    assert '<details class="source-nav">' in content_page.text


def test_source_view_is_a_deck_index_and_deck_detail_is_scoped(tmp_path: Path) -> None:
    client = _client(tmp_path)

    index = client.get("/library", params={"view": "source"})
    assert index.status_code == 200
    assert 'class="deck-index"' in index.text
    assert 'class="deck-grid"' in index.text
    assert 'class="deck-card"' in index.text
    assert 'class="deck-cover"' in index.text
    assert 'data-deck-id="deck_strategy"' in index.text
    assert "战略规划.pptx" in index.text
    assert "2 个页面" in index.text
    assert 'href="/library?view=source&deck_id=deck_strategy' in index.text
    assert 'class="slide-grid"' not in index.text

    detail = client.get(
        "/library",
        params={"view": "source", "deck_id": "deck_strategy", "q": "增长"},
    )
    assert detail.status_code == 200
    assert 'class="deck-detail"' in detail.text
    assert 'class="breadcrumb-link"' in detail.text
    assert "战略规划.pptx" in detail.text
    assert 'href="/library?view=source' in detail.text
    assert 'class="slide-grid"' in detail.text
    assert "增长策略" in detail.text
    assert "产品路线图" not in detail.text


def test_source_view_rejects_unknown_deck_and_preserves_detail_filters(tmp_path: Path) -> None:
    client = _client(tmp_path)

    missing = client.get("/library", params={"view": "source", "deck_id": "missing"})
    assert missing.status_code == 409

    detail = client.get(
        "/library",
        params={
            "view": "source",
            "deck_id": "deck_strategy",
            "topic": "战略与增长",
            "subtopic": "业务规划与增长",
            "page_type": "观点与结论",
            "q": "增长",
        },
    )
    assert detail.status_code == 200
    assert "subtopic=%E4%B8%9A%E5%8A%A1%E8%A7%84%E5%88%92%E4%B8%8E%E5%A2%9E%E9%95%BF" in detail.text
    assert "deck_id=deck_strategy" in detail.text
    assert "view=source" in detail.text


def test_library_normalizes_invalid_view_to_content(tmp_path: Path) -> None:
    client = _client(tmp_path)

    page = client.get("/library", params={"view": "unexpected<script>"})

    assert page.status_code == 200
    assert "unexpected" not in page.text
    assert "view=content" in page.text


def test_library_pagination_preserves_view_and_active_filters(tmp_path: Path) -> None:
    slides = tuple(
        SlideSummary(
            id=f"slide_{index}",
            deck_id="deck_many",
            deck_name="批量页面.pptx",
            slide_number=index,
            title=f"页面 {index}",
            topic="组织与人才",
            page_type="观点与结论",
        )
        for index in range(1, 26)
    )
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    client = TestClient(create_app(settings, catalog=InMemorySlideCatalog(slides)))

    page = client.get(
        "/library",
        params={
            "q": "页面",
            "topic": "组织与人才",
            "page_type": "观点与结论",
            "deck_id": "deck_many",
            "view": "source",
        },
    )

    assert page.status_code == 200
    assert "view=source" in page.text
    assert "deck_id=deck_many&page=2" in page.text


def test_library_page_has_navigation_for_more_than_one_page(tmp_path: Path) -> None:
    slides = tuple(
        SlideSummary(
            id=f"slide_{index}",
            deck_id="deck_many",
            deck_name="批量页面.pptx",
            slide_number=index,
            title=f"页面 {index}",
        )
        for index in range(1, 26)
    )
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    client = TestClient(create_app(settings, catalog=InMemorySlideCatalog(slides)))

    first_page = client.get("/library", params={"q": "页面"})
    second_page = client.get("/library", params={"q": "页面", "page": 2})

    assert 'aria-label="下一页"' in first_page.text
    assert "第 1 / 2 页" in first_page.text
    assert 'aria-label="上一页"' in second_page.text
    assert "第 2 / 2 页" in second_page.text


def test_library_pagination_preserves_query_and_all_filters(tmp_path: Path) -> None:
    slides = tuple(
        SlideSummary(
            id=f"slide_{index}",
            deck_id="deck_many",
            deck_name="批量页面.pptx",
            slide_number=index,
            title=f"页面 {index}",
            topic="组织与人才",
            page_type="观点与结论",
        )
        for index in range(1, 26)
    )
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    client = TestClient(create_app(settings, catalog=InMemorySlideCatalog(slides)))

    page = client.get(
        "/library",
        params={
            "q": "页面",
            "topic": "组织与人才",
            "page_type": "观点与结论",
            "deck_id": "deck_many",
        },
    )

    assert page.status_code == 200
    assert "q=%E9%A1%B5%E9%9D%A2" in page.text
    assert "topic=%E7%BB%84%E7%BB%87%E4%B8%8E%E4%BA%BA%E6%89%8D" in page.text
    assert "page_type=%E8%A7%82%E7%82%B9%E4%B8%8E%E7%BB%93%E8%AE%BA" in page.text
    assert "deck_id=deck_many&page=2" in page.text


def test_selection_add_remove_and_reorder_contract(tmp_path: Path) -> None:
    client = _client(tmp_path)

    added = client.post("/api/v1/selection/items", json={"slide_id": "slide_growth"})
    assert added.status_code == 200
    assert added.json()["data"]["count"] == 1
    client.post("/api/v1/selection/items", json={"slide_id": "slide_roadmap"})

    reordered = client.post(
        "/api/v1/selection/reorder",
        json={"slide_ids": ["slide_roadmap", "slide_growth"]},
    )
    assert [item["id"] for item in reordered.json()["data"]["items"]] == [
        "slide_roadmap",
        "slide_growth",
    ]
    removed = client.delete("/api/v1/selection/items/slide_roadmap")
    assert [item["id"] for item in removed.json()["data"]["items"]] == ["slide_growth"]

    selection_page = client.get("/selection")
    assert "selection-layout" in selection_page.text
    assert "selection-sidebar" in selection_page.text


def test_selection_rejects_unknown_slide_and_bad_reorder(tmp_path: Path) -> None:
    client = _client(tmp_path)
    unknown = client.post("/api/v1/selection/items", json={"slide_id": "missing"})
    assert unknown.status_code == 409
    assert unknown.json()["error"]["code"] == "NOT_FOUND"

    client.post("/api/v1/selection/items", json={"slide_id": "slide_growth"})
    bad = client.post("/api/v1/selection/reorder", json={"slide_ids": []})
    assert bad.status_code == 409
    assert bad.json()["error"]["code"] == "REQUEST_INVALID"


def test_library_filters_and_facets_contract(tmp_path: Path) -> None:
    slides = (
        SlideSummary(
            id="slide_org",
            deck_id="deck_org",
            deck_name="组织手册.pptx",
            slide_number=1,
            title="组织与人才",
            text="团队与招聘",
            topic="组织与人才",
            page_type="观点与结论",
        ),
        SlideSummary(
            id="slide_metrics",
            deck_id="deck_metrics",
            deck_name="经营复盘.pptx",
            slide_number=1,
            title="收入增长",
            text="数据与经营",
            topic="数据与经营",
            page_type="数据图表",
        ),
    )
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    client = TestClient(create_app(settings, catalog=InMemorySlideCatalog(slides)))

    filtered = client.get("/api/v1/slides", params={"topic": "组织与人才"})
    assert filtered.status_code == 200
    assert filtered.json()["data"]["filters"] == {
        "topic": "组织与人才",
        "page_type": "",
        "deck_id": "",
    }
    assert [item["id"] for item in filtered.json()["data"]["items"]] == ["slide_org"]

    facets = client.get("/api/v1/library/facets")
    assert facets.status_code == 200
    data = facets.json()["data"]
    assert [item["id"] for item in data["topics"]] == list(TOPICS)
    assert [item for item in data["decks"] if item["id"] == "deck_org"][0]["count"] == 1

    page = client.get("/library", params={"topic": "数据与经营"})
    assert page.status_code == 200
    assert "数据与经营" in page.text
    assert "1个页面" in page.text


def test_library_api_exposes_hierarchical_taxonomy_metadata_and_subtopic_filter(
    tmp_path: Path,
) -> None:
    slides = (
        SlideSummary(
            id="slide_perf",
            deck_id="deck_org",
            deck_name="组织手册.pptx",
            slide_number=1,
            title="绩效指标",
            text="奖金与考核",
            topic="组织与人才",
            subtopic="绩效与激励",
            page_type="数据图表",
            confidence="high",
            classification_source="manual",
        ),
    )
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    client = TestClient(create_app(settings, catalog=InMemorySlideCatalog(slides)))

    response = client.get("/api/v1/slides", params={"subtopic": "绩效与激励"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["filters"]["subtopic"] == "绩效与激励"
    assert data["items"][0]["subtopic"] == "绩效与激励"
    assert data["items"][0]["confidence"] == "high"
    assert data["items"][0]["classification_source"] == "manual"

    facets = client.get("/api/v1/library/facets").json()["data"]
    topic = next(option for option in facets["topics"] if option["id"] == "组织与人才")
    assert next(child for child in topic["children"] if child["id"] == "绩效与激励")["count"] == 1


def test_content_view_renders_nested_tree_and_slide_classification_metadata(tmp_path: Path) -> None:
    slides = (
        SlideSummary(
            id="slide_low",
            deck_id="deck_org",
            deck_name="组织手册.pptx",
            slide_number=1,
            title="待确认页面",
            text="需要人工确认的内容",
            topic="组织与人才",
            subtopic="绩效与激励",
            page_type="观点与结论",
            confidence="low",
        ),
    )
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    client = TestClient(create_app(settings, catalog=InMemorySlideCatalog(slides)))

    page = client.get(
        "/library",
        params={
            "q": "待确认",
            "topic": "组织与人才",
            "subtopic": "绩效与激励",
            "page_type": "观点与结论",
            "deck_id": "deck_org",
            "view": "content",
        },
    )

    assert page.status_code == 200
    assert "topic-tree" in page.text
    assert 'class="topic-group"' in page.text
    assert 'data-filter-subtopic="绩效与激励"' in page.text
    assert "subtopic=%E7%BB%A9%E6%95%88%E4%B8%8E%E6%BF%80%E5%8A%B1" in page.text
    assert 'class="slide-tags"' in page.text
    assert 'data-classification="组织与人才/绩效与激励"' in page.text
    assert 'class="confidence-marker is-low"' in page.text
    assert "待确认" in page.text


def test_library_empty_state_reset_link_keeps_view_and_filter_shape(tmp_path: Path) -> None:
    client = _client(tmp_path)

    page = client.get(
        "/library",
        params={
            "q": "不存在的页面",
            "topic": "战略与增长",
            "subtopic": "业务规划与增长",
            "page_type": "观点与结论",
            "deck_id": "deck_strategy",
            "view": "content",
        },
    )

    assert page.status_code == 200
    assert "没有匹配的页面" in page.text
    assert (
        'href="/library?q=&amp;topic=&amp;subtopic=&amp;page_type=&amp;deck_id=&amp;view=content"'
        in page.text
    )


def test_in_memory_catalog_applies_combined_filters(tmp_path: Path) -> None:
    slides = (
        SlideSummary("s1", "d1", "一.pptx", 1, "A", topic="组织与人才", page_type="观点与结论"),
        SlideSummary("s2", "d1", "一.pptx", 2, "B", topic="组织与人才", page_type="数据图表"),
        SlideSummary("s3", "d2", "二.pptx", 1, "C", topic="数据与业绩", page_type="数据图表"),
    )
    catalog = InMemorySlideCatalog(slides)
    page = catalog.search(
        "",
        page=1,
        page_size=10,
        filters=LibraryFilters(topic="组织与人才", page_type="数据图表", deck_id="d1"),
    )
    assert [item.id for item in page.items] == ["s2"]
