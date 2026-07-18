from pptlib.domain.taxonomy import (
    CLASSIFIER_VERSION,
    PAGE_TYPES,
    SUBTOPICS,
    TOPIC_TREE,
    TOPICS,
    classify_slide,
    classify_slide_result,
)


def test_classify_slide_uses_fixed_topic_and_page_type_rules() -> None:
    assert classify_slide("组织人才管理.pptx", "组织架构设计", "") == (
        "组织与人才",
        "组织架构",
    )
    assert classify_slide("客户案例.pptx", "项目推进流程", "") == (
        "项目与复盘",
        "流程与步骤",
    )
    assert classify_slide("年度经营.pptx", "收入增长", "同比增长 32%") == (
        "数据与经营",
        "数据图表",
    )


def test_classify_slide_falls_back_to_fixed_defaults() -> None:
    assert classify_slide("未命名.pptx", "普通内容", "") == (
        "方法论与培训",
        "观点与结论",
    )


def test_taxonomy_values_are_fixed_and_ordered() -> None:
    assert TOPICS == (
        "战略与增长",
        "客户与市场",
        "组织与人才",
        "产品与运营",
        "数据与经营",
        "项目与复盘",
        "方法论与培训",
        "公司介绍与案例",
    )
    assert TOPIC_TREE == {
        "战略与增长": (
            "企业战略与愿景",
            "业务规划与增长",
            "OKR与目标管理",
            "路线图与重点项目",
        ),
        "客户与市场": (
            "客户洞察与需求",
            "市场与竞争",
            "品牌与营销",
            "销售与渠道",
        ),
        "组织与人才": (
            "组织设计与治理",
            "人才招聘与发展",
            "绩效与激励",
            "文化与协作",
        ),
        "产品与运营": (
            "产品策略与体验",
            "运营机制与流程",
            "服务与交付",
            "供应链与效率",
        ),
        "数据与经营": (
            "经营指标与看板",
            "财务与收入",
            "用户与业务增长",
            "效率与成本",
        ),
        "项目与复盘": (
            "项目计划与进度",
            "风险与问题",
            "复盘与经验",
            "行动项与跟进",
        ),
        "方法论与培训": (
            "模型与框架",
            "课程与培训",
            "工具与模板",
            "SOP与能力建设",
        ),
        "公司介绍与案例": (
            "公司概览与能力",
            "解决方案与产品",
            "客户案例",
            "结果与证言",
        ),
    }
    assert all(len(subtopics) == 4 for subtopics in TOPIC_TREE.values())
    assert tuple(
        subtopic for subtopics in TOPIC_TREE.values() for subtopic in subtopics
    ) == SUBTOPICS
    assert PAGE_TYPES == (
        "封面与目录",
        "观点与结论",
        "对比与矩阵",
        "流程与步骤",
        "时间线与路线图",
        "组织架构",
        "数据图表",
        "表格与清单",
        "案例与证言",
        "总结与行动",
    )


def test_growth_routes_to_context_specific_subtopics() -> None:
    assert classify_slide_result(
        "增长战略规划.pptx", "增长路线与年度目标", ""
    ).subtopic == "业务规划与增长"
    assert classify_slide_result(
        "经营分析.pptx", "收入增长与利润", "同比、环比、经营指标"
    ).subtopic == "财务与收入"
    assert classify_slide_result(
        "用户运营.pptx", "用户增长与活跃", "留存、转化、活跃用户"
    ).subtopic == "用户与业务增长"


def test_section_context_resolves_an_ambiguous_slide_title() -> None:
    result = classify_slide_result(
        "业务复盘.pptx",
        "增长方案",
        "",
        section_title="用户增长与活跃",
    )
    assert result.topic == "数据与经营"
    assert result.subtopic == "用户与业务增长"


def test_negative_growth_keyword_excludes_revenue_from_user_growth() -> None:
    result = classify_slide_result(
        "年度经营.pptx", "收入增长", "同比增长 32%"
    )
    assert result.subtopic == "财务与收入"
    assert result.subtopic != "用户与业务增长"


def test_structured_result_marks_unknown_slides_low_confidence() -> None:
    result = classify_slide_result("未命名.pptx", "普通内容", "")
    assert result.topic == "方法论与培训"
    assert result.subtopic == "模型与框架"
    assert result.page_type == "观点与结论"
    assert result.confidence == "low"
    assert result.classifier_version == CLASSIFIER_VERSION


def test_structured_result_exposes_high_confidence_and_legacy_compatibility() -> None:
    result = classify_slide_result("组织人才管理.pptx", "绩效激励方案", "绩效指标和奖金")
    assert result.topic == "组织与人才"
    assert result.subtopic == "绩效与激励"
    assert result.confidence == "high"
    assert classify_slide("组织人才管理.pptx", "绩效激励方案", "绩效指标和奖金") == (
        result.topic,
        result.page_type,
    )
