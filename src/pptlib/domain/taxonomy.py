"""Fixed, deterministic hierarchical taxonomy rules for imported slides.

The classifier intentionally stays local and explainable.  It combines evidence
from the source deck, section context, title and body text; it does not call a
model or depend on a network service.  ``classify_slide`` remains the small
tuple-returning API used by older importers, while ``classify_slide_result``
exposes the richer classification metadata needed by the new library UI.
"""

from __future__ import annotations

from dataclasses import dataclass

CLASSIFIER_VERSION = "taxonomy-v2"

# Keep the order stable: it is used as the deterministic tie breaker and is
# also the order shown in the library's content navigation.
TOPIC_TREE = {
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
TOPICS = tuple(TOPIC_TREE)
SUBTOPICS = tuple(subtopic for values in TOPIC_TREE.values() for subtopic in values)

# Page purpose intentionally keeps the existing API values.  They are a
# separate dimension from the business subject and remain backwards compatible
# with stored rows and query parameters.
PAGE_TYPES = (
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


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    """The complete, explainable result for one slide."""

    topic: str
    subtopic: str
    page_type: str
    confidence: str
    classifier_version: str = CLASSIFIER_VERSION


@dataclass(frozen=True, slots=True)
class _Rule:
    label: str
    keywords: tuple[str, ...]
    negative_keywords: tuple[str, ...] = ()


def _normalize(value: str) -> str:
    return "".join(value.casefold().split())


def _score(rule: _Rule, fields: tuple[tuple[str, int], ...]) -> int:
    """Score a rule, giving each source field an explicit deterministic weight."""

    score = 0
    for value, weight in fields:
        normalized_value = _normalize(value)
        for keyword in rule.keywords:
            normalized_keyword = _normalize(keyword)
            if normalized_keyword and normalized_keyword in normalized_value:
                score += normalized_value.count(normalized_keyword) * weight
        for keyword in rule.negative_keywords:
            normalized_keyword = _normalize(keyword)
            if normalized_keyword and normalized_keyword in normalized_value:
                score -= normalized_value.count(normalized_keyword) * weight
    return max(score, 0)


# Each subtopic has a compact phrase group.  Phrases (for example, ``用户增长``
# rather than just ``增长``) are deliberate: they avoid routing the ambiguous
# word "增长" to the metrics bucket when the context is strategic or product
# related.
_SUBTOPIC_RULES: tuple[tuple[str, tuple[_Rule, ...]], ...] = (
    (
        "战略与增长",
        (
            _Rule("企业战略与愿景", ("企业战略", "战略", "愿景", "使命", "价值观")),
            _Rule(
                "业务规划与增长",
                ("业务规划", "增长规划", "增长战略", "增长路线", "年度目标", "业务目标"),
            ),
            _Rule("OKR与目标管理", ("okr", "目标管理", "关键结果", "年度目标", "目标拆解")),
            _Rule("路线图与重点项目", ("路线图", "roadmap", "重点项目", "战略项目", "里程碑")),
        ),
    ),
    (
        "客户与市场",
        (
            _Rule(
                "客户洞察与需求",
                ("客户洞察", "客户需求", "用户需求", "用户画像", "客群", "痛点"),
            ),
            _Rule("市场与竞争", ("市场分析", "市场规模", "竞争", "竞品", "行业格局", "市场格局")),
            _Rule("品牌与营销", ("品牌", "营销", "传播", "活动", "内容营销", "投放")),
            _Rule("销售与渠道", ("销售", "渠道", "经销", "商机", "转化漏斗", "客户开发")),
        ),
    ),
    (
        "组织与人才",
        (
            _Rule("组织设计与治理", ("组织架构", "组织设计", "组织结构", "治理", "部门设置")),
            _Rule("人才招聘与发展", ("人才", "招聘", "人才发展", "任职资格", "培训发展", "梯队")),
            _Rule("绩效与激励", ("绩效", "激励", "奖金", "薪酬", "绩效指标", "考核")),
            _Rule("文化与协作", ("企业文化", "文化", "协作", "团队", "沟通", "共创")),
        ),
    ),
    (
        "产品与运营",
        (
            _Rule(
                "产品策略与体验",
                ("产品策略", "产品规划", "产品体验", "用户体验", "产品增长", "功能"),
            ),
            _Rule(
                "运营机制与流程",
                ("运营机制", "运营流程", "运营策略", "运营", "流程机制"),
                ("供应链",),
            ),
            _Rule("服务与交付", ("服务", "交付", "客户成功", "实施", "服务体验")),
            _Rule("供应链与效率", ("供应链", "采购", "库存", "履约", "生产效率")),
        ),
    ),
    (
        "数据与经营",
        (
            _Rule(
                "经营指标与看板",
                ("经营指标", "经营看板", "数据看板", "kpi", "指标体系", "看板"),
            ),
            _Rule(
                "财务与收入",
                ("财务", "收入", "利润", "毛利", "营收", "收入增长", "同比", "环比"),
            ),
            _Rule(
                "用户与业务增长",
                ("用户增长", "业务增长", "活跃用户", "留存", "转化率", "用户规模"),
                ("收入增长", "增长战略"),
            ),
            _Rule("效率与成本", ("成本", "降本", "效率", "人效", "成本结构", "资源利用")),
        ),
    ),
    (
        "项目与复盘",
        (
            _Rule("项目计划与进度", ("项目计划", "项目进度", "项目推进", "进度管理", "推进")),
            _Rule("风险与问题", ("风险", "问题", "阻塞", "依赖", "风险管理")),
            _Rule("复盘与经验", ("复盘", "经验总结", "项目回顾", "教训", "复盘会议")),
            _Rule("行动项与跟进", ("行动项", "下一步", "跟进", "todo", "责任人", "待办")),
        ),
    ),
    (
        "方法论与培训",
        (
            _Rule("模型与框架", ("模型", "框架", "方法论", "方法体系", "思维框架")),
            _Rule("课程与培训", ("培训", "课程", "课堂", "学习", "训练营")),
            _Rule("工具与模板", ("工具", "模板", "工具包", "画布", "表单模板")),
            _Rule("SOP与能力建设", ("sop", "标准作业", "能力建设", "能力模型", "操作手册")),
        ),
    ),
    (
        "公司介绍与案例",
        (
            _Rule("公司概览与能力", ("公司介绍", "公司概览", "公司简介", "企业概况", "我们的能力")),
            _Rule("解决方案与产品", ("解决方案", "产品方案", "服务方案", "产品能力")),
            _Rule("客户案例", ("客户案例", "成功案例", "客户故事", "案例研究")),
            _Rule("结果与证言", ("结果", "证言", "客户评价", "testimonial", "成果")),
        ),
    ),
)

_PAGE_TYPE_RULES = (
    _Rule("封面与目录", ("封面", "目录", "agenda", "contents", "议程")),
    _Rule("观点与结论", ("观点", "结论", "建议", "洞察", "要点")),
    _Rule("对比与矩阵", ("对比", "比较", "矩阵", "swot", "优劣", "差异")),
    _Rule("流程与步骤", ("流程", "步骤", "sop", "路径", "推进")),
    _Rule("时间线与路线图", ("时间线", "路线图", "roadmap", "里程碑", "阶段")),
    _Rule("组织架构", ("组织架构", "组织结构", "架构", "团队结构", "部门")),
    _Rule(
        "数据图表",
        ("数据", "图表", "收入", "增长", "同比", "环比", "利润", "指标", "%", "趋势"),
    ),
    _Rule("表格与清单", ("表格", "清单", "列表", "明细", "checklist", "表单")),
    _Rule("案例与证言", ("案例", "证言", "客户故事", "成功案例", "testimonial")),
    _Rule("总结与行动", ("行动", "下一步", "总结", "结语", "复盘", "todo")),
)


def _best_subtopic(
    fields: tuple[tuple[str, int], ...],
) -> tuple[str, str, int, int]:
    best_topic = TOPICS[6]
    best_subtopic = TOPIC_TREE[best_topic][0]
    best_score = 0
    second_score = 0
    for topic, rules in _SUBTOPIC_RULES:
        for subtopic in rules:
            score = _score(subtopic, fields)
            if score > best_score:
                second_score = best_score
                best_topic, best_subtopic, best_score = topic, subtopic.label, score
            elif score > second_score:
                second_score = score
    return best_topic, best_subtopic, best_score, second_score


def _classify_page_type(fields: tuple[tuple[str, int], ...]) -> str:
    scores = [_score(rule, fields) for rule in _PAGE_TYPE_RULES]
    best_index, best_score = 0, 0
    for index, score in enumerate(scores):
        if score > best_score:
            best_index, best_score = index, score
    return _PAGE_TYPE_RULES[best_index].label if best_score else "观点与结论"


def classify_slide_result(
    deck_name: str,
    title: str,
    text: str,
    section_title: str = "",
    notes: str = "",
) -> ClassificationResult:
    """Classify one slide and return subject, purpose, confidence and version.

    ``section_title`` and ``notes`` are optional so existing import callers can
    continue passing three arguments while newer importers can provide chapter
    context and speaker notes.
    """

    fields = (
        (title, 8),
        (section_title, 5),
        (deck_name, 2),
        (text, 1),
        (notes, 1),
    )
    topic, subtopic, best_score, second_score = _best_subtopic(fields)
    page_type = _classify_page_type(fields)
    if best_score == 0:
        confidence = "low"
    elif best_score >= 8 and best_score - second_score >= 3:
        confidence = "high"
    else:
        confidence = "medium"
    return ClassificationResult(topic, subtopic, page_type, confidence)


# Descriptive alias for callers that prefer the word "structured" in the API.
classify_slide_structured = classify_slide_result


def classify_slide(deck_name: str, title: str, text: str) -> tuple[str, str]:
    """Compatibility API returning the historical ``(topic, page_type)`` tuple."""

    result = classify_slide_result(deck_name, title, text)
    return result.topic, result.page_type


class Taxonomy:
    """Compatibility namespace exposing the fixed taxonomy contract."""

    TOPIC_TREE = TOPIC_TREE
    TOPICS = TOPICS
    SUBTOPICS = SUBTOPICS
    PAGE_TYPES = PAGE_TYPES
    CLASSIFIER_VERSION = CLASSIFIER_VERSION
    classify_slide = staticmethod(classify_slide)
    classify_slide_result = staticmethod(classify_slide_result)
    classify_slide_structured = staticmethod(classify_slide_structured)
