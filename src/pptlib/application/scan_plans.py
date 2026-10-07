from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pptlib.application.import_decks import (
    MissingPolicy,
    scan_and_import,
    sync_removed_source_paths,
)
from pptlib.config import Settings
from pptlib.discovery.scanner import (
    DiscoveryIssue,
    ScanRules,
    discover_paths_with_diagnostics,
    hash_discovered_file,
)
from pptlib.domain.ids import new_id
from pptlib.infrastructure.db.connection import connect
from pptlib.infrastructure.db.repositories import DeckRepository
from pptlib.rendering.thumbnails import render_cache_is_complete

JsonDict = dict[str, Any]
ProgressCallback = Callable[[JsonDict], None]
CancelCheck = Callable[[], bool]
_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_ALLOWED_FORMATS = {"pptx", "html", "html_zip"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _is_below(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _minute_of_day(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def _time_in_window(value: str, start: str, end: str) -> bool:
    current = _minute_of_day(value)
    first = _minute_of_day(start)
    last = _minute_of_day(end)
    if first <= last:
        return first <= current <= last
    return current >= first or current <= last


def _normalize_plan(payload: JsonDict) -> JsonDict:
    name = str(payload.get("name", "")).strip()
    if not name or len(name) > 80:
        raise ValueError("计划名称需为 1-80 个字符")
    schedule_kind = str(payload.get("scheduleKind", "daily"))
    if schedule_kind not in {"manual", "daily"}:
        raise ValueError("不支持的执行频率")
    schedule_time = str(payload.get("scheduleTime", "02:30"))
    window_start = str(payload.get("windowStart", "02:00"))
    window_end = str(payload.get("windowEnd", "05:00"))
    if not all(
        _TIME_PATTERN.fullmatch(value) for value in (schedule_time, window_start, window_end)
    ):
        raise ValueError("时间必须使用 HH:MM 格式")
    if schedule_kind == "daily" and not _time_in_window(schedule_time, window_start, window_end):
        raise ValueError("执行时间必须位于执行窗口内")
    formats = sorted({str(value) for value in payload.get("formats", ["pptx"])})
    if not formats or not set(formats).issubset(_ALLOWED_FORMATS):
        raise ValueError("至少选择一种受支持的文件格式")
    min_bytes = int(payload.get("minFileBytes", 0))
    max_bytes = int(payload.get("maxFileBytes", 3 * 1024 * 1024 * 1024))
    if min_bytes < 0 or max_bytes <= 0 or min_bytes > max_bytes:
        raise ValueError("文件大小范围无效")
    if max_bytes > 3 * 1024 * 1024 * 1024:
        raise ValueError("单文件最大值不能超过 3 GB")
    stability_seconds = int(payload.get("stabilitySeconds", 60))
    if stability_seconds < 0 or stability_seconds > 3600:
        raise ValueError("文件稳定等待时间需在 0-3600 秒之间")
    missing_policy = str(payload.get("missingPolicy", "keep"))
    if missing_policy not in {"keep", "remove"}:
        raise ValueError("无效的源文件缺失策略")

    roots: list[JsonDict] = []
    for raw in payload.get("roots", []):
        if not isinstance(raw, dict):
            raise ValueError("文件夹配置无效")
        raw_path = str(raw.get("path", "")).strip()
        if not raw_path:
            raise ValueError("文件夹路径不能为空")
        path = Path(raw_path).expanduser().resolve()
        roots.append(
            {
                "id": str(raw.get("id", "")).strip(),
                "name": str(raw.get("name", "")).strip() or path.name or str(path),
                "path": str(path),
                "recursive": raw.get("recursive", True) is not False,
            }
        )
    if not roots:
        raise ValueError("至少添加一个扫描文件夹")
    if len({str(root["path"]).casefold() for root in roots}) != len(roots):
        raise ValueError("同一计划中不能重复添加文件夹")
    resolved = [Path(str(root["path"])) for root in roots]
    for index, path in enumerate(resolved):
        for other in resolved[index + 1 :]:
            if _is_below(path, other) or _is_below(other, path):
                raise ValueError(f"文件夹范围重叠：{path} 与 {other}")

    return {
        "id": str(payload.get("id", "")).strip(),
        "name": name,
        "enabled": payload.get("enabled", True) is not False,
        "scheduleKind": schedule_kind,
        "scheduleTime": schedule_time,
        "windowStart": window_start,
        "windowEnd": window_end,
        "formats": formats,
        "minFileBytes": min_bytes,
        "maxFileBytes": max_bytes,
        "stabilitySeconds": stability_seconds,
        "missingPolicy": missing_policy,
        "repairPreviews": payload.get("repairPreviews", True) is not False,
        "roots": roots,
    }


def _token_payload(plan: JsonDict) -> JsonDict:
    return {key: value for key, value in plan.items() if key not in {"id"} and key != "roots"} | {
        "roots": [
            {
                "name": root["name"],
                "path": root["path"],
                "recursive": root["recursive"],
            }
            for root in plan["roots"]
        ]
    }


def preview_token(payload: JsonDict) -> str:
    plan = _normalize_plan(payload)
    digest = hashlib.sha256(_json(_token_payload(plan)).encode("utf-8")).hexdigest()
    return f"preview_{digest}"


def _rules(plan: JsonDict, root: JsonDict) -> ScanRules:
    return ScanRules(
        formats=frozenset(str(value) for value in plan["formats"]),
        min_file_bytes=int(plan["minFileBytes"]),
        max_file_bytes=int(plan["maxFileBytes"]),
        recursive=bool(root["recursive"]),
        stability_seconds=int(plan["stabilitySeconds"]),
    )


def _issue_dict(issue: DiscoveryIssue) -> JsonDict:
    return {
        "path": str(issue.path),
        "name": issue.path.name,
        "decision": "skipped",
        "action": "",
        "reason": issue.reason,
        "detail": issue.detail,
        "sizeBytes": issue.size_bytes,
        "slideCount": 0,
    }


def _root_preview(
    connection: sqlite3.Connection,
    settings: Settings,
    plan: JsonDict,
    root: JsonDict,
    *,
    should_cancel: CancelCheck | None = None,
    source_paths: list[Path] | None = None,
) -> JsonDict:
    root_path = Path(str(root["path"]))
    authorization = "ok"
    blockers: list[str] = []
    if not root_path.exists():
        authorization = "missing"
        blockers.append("文件夹不存在或需要重新授权")
    elif not root_path.is_dir():
        authorization = "unreadable"
        blockers.append("路径不是文件夹")

    report = discover_paths_with_diagnostics(
        source_paths or [root_path],
        rules=_rules(plan, root),
    )
    repository = DeckRepository(connection)
    items = [_issue_dict(issue) for issue in report.rejected]
    action_counts: Counter[str] = Counter(item["reason"] for item in items)
    accepted_hashes: Counter[str] = Counter()
    scanned_by_path = {}

    for index, discovered in enumerate(report.accepted):
        if should_cancel is not None and should_cancel():
            for pending in report.accepted[index:]:
                items.append(
                    {
                        "path": str(pending.path),
                        "name": pending.path.name,
                        "decision": "cancelled",
                        "action": "cancelled",
                        "reason": "用户停止任务",
                        "detail": "尚未开始处理",
                        "sizeBytes": pending.size_bytes,
                        "slideCount": 0,
                    }
                )
                action_counts["cancelled"] += 1
            break
        current = repository.current_for_path(discovered.path)
        action = "created"
        detail = "新文件"
        slide_count = 0
        try:
            if (
                current is not None
                and current.size_bytes == discovered.size_bytes
                and current.mtime_ns == discovered.mtime_ns
                and current.ctime_ns == discovered.ctime_ns
            ):
                slide_count = current.slide_count
                if bool(plan["repairPreviews"]) and not render_cache_is_complete(
                    settings.assets_dir, current.version_id, current.slide_count
                ):
                    action = "repair_preview"
                    detail = "索引未变化，预览需要修复"
                else:
                    action = "unchanged"
                    detail = "文件未变化"
            else:
                scanned = hash_discovered_file(discovered)
                scanned_by_path[discovered.path] = scanned
                accepted_hashes[scanned.sha256] += 1
                if current is not None:
                    slide_count = current.slide_count
                    if scanned.sha256 == current.sha256:
                        action = (
                            "repair_preview"
                            if bool(plan["repairPreviews"])
                            and not render_cache_is_complete(
                                settings.assets_dir,
                                current.version_id,
                                current.slide_count,
                            )
                            else "unchanged"
                        )
                        detail = "内容未变化，仅文件时间发生变化"
                    else:
                        action = "updated"
                        detail = "内容发生变化"
        except (OSError, ValueError) as error:
            action = "failed"
            detail = str(error)
        items.append(
            {
                "path": str(discovered.path),
                "name": discovered.path.name,
                "decision": "accepted" if action != "failed" else "failed",
                "action": action,
                "reason": "" if action != "failed" else "scan_failed",
                "detail": detail,
                "sizeBytes": discovered.size_bytes,
                "slideCount": slide_count,
            }
        )
        action_counts[action] += 1

    plan_roots = tuple(Path(str(value["path"])) for value in plan["roots"])
    for item in items:
        if item["action"] != "created":
            continue
        relocation_scan = scanned_by_path.get(Path(str(item["path"])))
        if relocation_scan is None or accepted_hashes[relocation_scan.sha256] != 1:
            continue
        candidates = [
            current
            for current in repository.current_for_sha256(relocation_scan.sha256)
            if any(_is_below(current.path, plan_root) for plan_root in plan_roots)
            and not current.path.is_file()
        ]
        if len(candidates) == 1:
            action_counts["created"] -= 1
            action_counts["moved"] += 1
            item["action"] = "moved"
            item["detail"] = f"将复用原索引：{candidates[0].path}"
            item["slideCount"] = candidates[0].slide_count

    if source_paths is None and root_path.is_dir():
        for row in connection.execute(
            """
            SELECT d.canonical_path, v.slide_count
            FROM decks d
            JOIN deck_versions v ON v.id = d.current_version_id
            ORDER BY d.canonical_path
            """
        ).fetchall():
            source = Path(str(row[0]))
            in_scope = _is_below(source, root_path)
            if in_scope and not bool(root["recursive"]):
                in_scope = source.parent == root_path
            if in_scope and not source.is_file():
                items.append(
                    {
                        "path": str(source),
                        "name": source.name,
                        "decision": "missing",
                        "action": "missing",
                        "reason": "source_missing",
                        "detail": (
                            "保留页库内容"
                            if plan["missingPolicy"] == "keep"
                            else "确认执行后将同步移出页库"
                        ),
                        "sizeBytes": 0,
                        "slideCount": int(row[1]),
                    }
                )
                action_counts["missing"] += 1

    return {
        "root": {**root, "authorizationStatus": authorization},
        "counts": dict(action_counts),
        "items": items,
        "blockers": blockers,
    }


def preview_scan_plan(settings: Settings, payload: JsonDict) -> JsonDict:
    plan = _normalize_plan(payload)
    connection = connect(settings.database_path)
    try:
        roots = [_root_preview(connection, settings, plan, root) for root in plan["roots"]]
    finally:
        connection.close()
    totals: Counter[str] = Counter()
    blockers: list[str] = []
    for root in roots:
        totals.update(root["counts"])
        blockers.extend(f"{root['root']['name']}：{message}" for message in root["blockers"])
    return {
        "ok": not blockers,
        "previewToken": preview_token(plan),
        "plan": plan,
        "counts": dict(totals),
        "roots": roots,
        "blockers": blockers,
    }


def _row_plan(connection: sqlite3.Connection, row: sqlite3.Row) -> JsonDict:
    roots = connection.execute(
        """
        SELECT id, name, path, recursive, authorization_status,
               last_scanned_at, last_result_json
        FROM scan_plan_roots WHERE plan_id = ? ORDER BY created_at, path
        """,
        (row["id"],),
    ).fetchall()
    return {
        "id": str(row["id"]),
        "name": str(row["name"]),
        "enabled": bool(row["enabled"]),
        "scheduleKind": str(row["schedule_kind"]),
        "scheduleTime": str(row["schedule_time"]),
        "windowStart": str(row["window_start"]),
        "windowEnd": str(row["window_end"]),
        "formats": json.loads(row["formats_json"]),
        "minFileBytes": int(row["min_file_bytes"]),
        "maxFileBytes": int(row["max_file_bytes"]),
        "stabilitySeconds": int(row["stability_seconds"]),
        "missingPolicy": str(row["missing_policy"]),
        "repairPreviews": bool(row["repair_previews"]),
        "lastPreviewAt": row["last_preview_at"],
        "createdAt": str(row["created_at"]),
        "updatedAt": str(row["updated_at"]),
        "roots": [
            {
                "id": str(root["id"]),
                "name": str(root["name"]),
                "path": str(root["path"]),
                "recursive": bool(root["recursive"]),
                "authorizationStatus": str(root["authorization_status"]),
                "lastScannedAt": root["last_scanned_at"],
                "lastResult": json.loads(root["last_result_json"]),
            }
            for root in roots
        ],
    }


def list_scan_plans(settings: Settings) -> JsonDict:
    connection = connect(settings.database_path)
    try:
        rows = connection.execute("SELECT * FROM scan_plans ORDER BY created_at, name").fetchall()
        plans = [_row_plan(connection, row) for row in rows]
    finally:
        connection.close()
    return {"ok": True, "plans": plans}


def get_scan_plan(settings: Settings, plan_id: str) -> JsonDict:
    connection = connect(settings.database_path)
    try:
        row = connection.execute("SELECT * FROM scan_plans WHERE id = ?", (plan_id,)).fetchone()
        if row is None:
            raise ValueError("扫描计划不存在")
        return _row_plan(connection, row)
    finally:
        connection.close()


def save_scan_plan(settings: Settings, payload: JsonDict) -> JsonDict:
    plan = _normalize_plan(payload)
    if payload.get("previewToken") != preview_token(plan):
        raise ValueError("计划配置已变化，请重新预检后再保存")
    preview = preview_scan_plan(settings, plan)
    if preview["blockers"] and plan["enabled"]:
        raise ValueError("启用计划前需修复预检阻塞项")

    plan_id = str(plan["id"]) or new_id("scan_plan")
    now = _now()
    connection = connect(settings.database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT created_at FROM scan_plans WHERE id = ?", (plan_id,)
        ).fetchone()
        created_at = str(existing[0]) if existing else now
        connection.execute(
            """
            INSERT INTO scan_plans(
                id, name, enabled, schedule_kind, schedule_time,
                window_start, window_end, formats_json, min_file_bytes,
                max_file_bytes, stability_seconds, missing_policy,
                repair_previews, last_preview_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                enabled = excluded.enabled,
                schedule_kind = excluded.schedule_kind,
                schedule_time = excluded.schedule_time,
                window_start = excluded.window_start,
                window_end = excluded.window_end,
                formats_json = excluded.formats_json,
                min_file_bytes = excluded.min_file_bytes,
                max_file_bytes = excluded.max_file_bytes,
                stability_seconds = excluded.stability_seconds,
                missing_policy = excluded.missing_policy,
                repair_previews = excluded.repair_previews,
                last_preview_at = excluded.last_preview_at,
                updated_at = excluded.updated_at
            """,
            (
                plan_id,
                plan["name"],
                int(bool(plan["enabled"])),
                plan["scheduleKind"],
                plan["scheduleTime"],
                plan["windowStart"],
                plan["windowEnd"],
                _json(plan["formats"]),
                plan["minFileBytes"],
                plan["maxFileBytes"],
                plan["stabilitySeconds"],
                plan["missingPolicy"],
                int(bool(plan["repairPreviews"])),
                now,
                created_at,
                now,
            ),
        )
        retained: list[str] = []
        for root in plan["roots"]:
            root_id = str(root["id"]) or new_id("scan_root")
            retained.append(root_id)
            authorization = "ok" if Path(str(root["path"])).is_dir() else "missing"
            connection.execute(
                """
                INSERT INTO scan_plan_roots(
                    id, plan_id, name, path, recursive, authorization_status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plan_id, path) DO UPDATE SET
                    name = excluded.name,
                    recursive = excluded.recursive,
                    authorization_status = excluded.authorization_status,
                    updated_at = excluded.updated_at
                """,
                (
                    root_id,
                    plan_id,
                    root["name"],
                    root["path"],
                    int(bool(root["recursive"])),
                    authorization,
                    now,
                    now,
                ),
            )
            stored = connection.execute(
                "SELECT id FROM scan_plan_roots WHERE plan_id = ? AND path = ?",
                (plan_id, root["path"]),
            ).fetchone()
            retained[-1] = str(stored[0])
        placeholders = ",".join("?" for _ in retained)
        connection.execute(
            f"DELETE FROM scan_plan_roots WHERE plan_id = ? AND id NOT IN ({placeholders})",
            (plan_id, *retained),
        )
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
    return {"ok": True, "plan": get_scan_plan(settings, plan_id)}


def delete_scan_plan(settings: Settings, plan_id: str) -> JsonDict:
    connection = connect(settings.database_path)
    try:
        active = connection.execute(
            """
            SELECT 1 FROM scan_runs
            WHERE plan_id = ? AND status IN ('preflighting', 'running', 'stopping')
            """,
            (plan_id,),
        ).fetchone()
        if active:
            raise ValueError("计划正在运行，停止后才能删除")
        cursor = connection.execute("DELETE FROM scan_plans WHERE id = ?", (plan_id,))
    finally:
        connection.close()
    return {"ok": True, "deleted": cursor.rowcount == 1}


def set_scan_plan_enabled(
    settings: Settings,
    plan_id: str,
    *,
    enabled: bool,
) -> JsonDict:
    connection = connect(settings.database_path)
    try:
        row = connection.execute("SELECT id FROM scan_plans WHERE id = ?", (plan_id,)).fetchone()
        if row is None:
            raise ValueError("扫描计划不存在")
        if enabled:
            roots = connection.execute(
                "SELECT id, path FROM scan_plan_roots WHERE plan_id = ?",
                (plan_id,),
            ).fetchall()
            missing = [str(root["path"]) for root in roots if not Path(str(root["path"])).is_dir()]
            if missing:
                connection.executemany(
                    """
                    UPDATE scan_plan_roots
                    SET authorization_status = 'missing', updated_at = ?
                    WHERE id = ?
                    """,
                    [(_now(), str(root["id"])) for root in roots if str(root["path"]) in missing],
                )
                raise ValueError("文件夹不存在或需要重新授权，无法开启计划")
        connection.execute(
            "UPDATE scan_plans SET enabled = ?, updated_at = ? WHERE id = ?",
            (int(enabled), _now(), plan_id),
        )
    finally:
        connection.close()
    return {"ok": True, "plan": get_scan_plan(settings, plan_id)}


def _insert_run_item(
    connection: sqlite3.Connection,
    run_id: str,
    root_id: str,
    item: JsonDict,
    *,
    elapsed_ms: int = 0,
) -> None:
    connection.execute(
        """
        INSERT INTO scan_run_items(
            id, run_id, root_id, path, decision, action, reason,
            size_bytes, slide_count, elapsed_ms, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id("scan_item"),
            run_id,
            root_id,
            item["path"],
            item.get("decision", "accepted"),
            item.get("action", ""),
            item.get("reason", ""),
            int(item.get("sizeBytes", 0)),
            int(item.get("slideCount", 0)),
            elapsed_ms,
            _now(),
        ),
    )


def run_scan_plan(
    settings: Settings,
    plan_id: str,
    *,
    root_id: str | None = None,
    trigger: str = "manual",
    scheduled_for: str | None = None,
    retry_paths: dict[str, list[Path]] | None = None,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> JsonDict:
    if trigger not in {"manual", "scheduled", "retry"}:
        raise ValueError("无效的触发方式")
    plan = get_scan_plan(settings, plan_id)
    if trigger == "scheduled" and not plan["enabled"]:
        raise ValueError("计划已暂停")
    roots = [
        root
        for root in plan["roots"]
        if (root_id is None or root["id"] == root_id)
        and (retry_paths is None or root["id"] in retry_paths)
    ]
    if not roots:
        raise ValueError("扫描文件夹不存在")
    run_id = new_id("scan_run")
    started = _now()
    connection = connect(settings.database_path)
    connection.execute("BEGIN IMMEDIATE")
    try:
        active = connection.execute(
            """
            SELECT plan_name FROM scan_runs
            WHERE status IN ('preflighting', 'running', 'stopping')
            LIMIT 1
            """
        ).fetchone()
        if active is not None:
            raise ValueError(f"已有扫描任务正在运行：{active['plan_name']}")
        connection.execute(
            """
            INSERT INTO scan_runs(
                id, plan_id, plan_name, trigger_type, scope_type, scope_root_id,
                status, rules_json, stats_json, started_at, scheduled_for
            ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, '{}', ?, ?)
            """,
            (
                run_id,
                plan_id,
                plan["name"],
                trigger,
                "root" if root_id else "plan",
                root_id,
                _json(_token_payload(plan)),
                started,
                scheduled_for,
            ),
        )
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        connection.close()
        raise
    stats: Counter[str] = Counter()
    pending_missing: list[tuple[str, JsonDict]] = []
    try:
        for root_index, root in enumerate(roots, 1):
            if should_cancel is not None and should_cancel():
                break
            root_stats: Counter[str] = Counter()
            preview = _root_preview(
                connection,
                settings,
                plan,
                root,
                should_cancel=should_cancel,
                source_paths=retry_paths.get(str(root["id"])) if retry_paths else None,
            )
            authorization = preview["root"]["authorizationStatus"]
            if preview["blockers"]:
                stats["failed"] += 1
                connection.execute(
                    "UPDATE scan_plans SET enabled = 0, updated_at = ? WHERE id = ?",
                    (_now(), plan_id),
                )
                connection.execute(
                    """
                    UPDATE scan_plan_roots
                    SET authorization_status = ?, last_scanned_at = ?,
                        last_result_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        authorization,
                        _now(),
                        _json({"failed": 1, "reason": preview["blockers"][0]}),
                        _now(),
                        root["id"],
                    ),
                )
                for item in preview["items"]:
                    _insert_run_item(connection, run_id, str(root["id"]), item)
                continue
            for item in preview["items"]:
                if item["decision"] != "accepted":
                    if item["decision"] == "missing":
                        pending_missing.append((str(root["id"]), item))
                        continue
                    _insert_run_item(connection, run_id, str(root["id"]), item)
                    decision = str(item["decision"])
                    key = (
                        decision
                        if decision in {"failed", "cancelled", "missing"}
                        else str(item["reason"])
                    )
                    stats[key] += 1
                    root_stats[key] += 1
            if should_cancel is not None and should_cancel():
                break

            scoped_settings = replace(
                settings,
                max_file_bytes=int(plan["maxFileBytes"]),
                html_enabled=bool({"html", "html_zip"}.intersection(plan["formats"])),
                renderer="libreoffice",
            )

            def emit(
                event: JsonDict,
                *,
                current_root: JsonDict = root,
                current_root_index: int = root_index,
            ) -> None:
                enriched = {
                    **event,
                    "taskKind": "scan_plan",
                    "runId": run_id,
                    "planId": plan_id,
                    "rootId": current_root["id"],
                    "rootIndex": current_root_index,
                    "rootTotal": len(roots),
                }
                if on_progress is not None:
                    on_progress(enriched)

            before = time.monotonic()
            report = scan_and_import(
                connection,
                (
                    retry_paths.get(str(root["id"]), [])
                    if retry_paths
                    else [Path(str(root["path"]))]
                ),
                settings=scoped_settings,
                on_progress=emit,
                scan_rules=_rules(plan, root),
                missing_policy=MissingPolicy.KEEP,
                repair_existing_assets=bool(plan["repairPreviews"]),
                relocation_roots=[Path(str(plan_root["path"])) for plan_root in plan["roots"]],
                should_cancel=should_cancel,
            )
            failed_paths = {path: error for path, error in report.failed}
            cancelled_paths = set(report.cancelled)
            imported_by_path = {item.path: item for item in report.imported}
            elapsed_ms = int((time.monotonic() - before) * 1000)
            for preview_item in preview["items"]:
                if preview_item["decision"] != "accepted":
                    continue
                item_path = Path(str(preview_item["path"]))
                if item_path in failed_paths:
                    item = {
                        **preview_item,
                        "decision": "failed",
                        "action": "failed",
                        "reason": failed_paths[item_path],
                    }
                    stats["failed"] += 1
                    root_stats["failed"] += 1
                elif item_path in cancelled_paths:
                    item = {
                        **preview_item,
                        "decision": "cancelled",
                        "action": "cancelled",
                        "reason": "用户停止任务",
                    }
                    stats["cancelled"] += 1
                    root_stats["cancelled"] += 1
                else:
                    imported = imported_by_path.get(item_path)
                    preview_action = str(preview_item["action"])
                    action = imported.action if imported else preview_action
                    if preview_action == "repair_preview" and action == "unchanged":
                        action = preview_action
                    item = {
                        **preview_item,
                        "action": action,
                        "slideCount": imported.slide_count if imported else 0,
                    }
                    stats[action] += 1
                    root_stats[action] += 1
                _insert_run_item(
                    connection,
                    run_id,
                    str(root["id"]),
                    item,
                    elapsed_ms=elapsed_ms,
                )
            root_stats["discovered"] = report.discovered
            root_stats["removed"] = len(report.removed)
            connection.execute(
                """
                UPDATE scan_plan_roots
                SET authorization_status = 'ok', last_scanned_at = ?,
                    last_result_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (_now(), _json(dict(root_stats)), _now(), root["id"]),
            )
            if report.cancelled:
                break
        confirmed_missing_paths: list[Path] = []
        for missing_root_id, item in pending_missing:
            still_indexed = connection.execute(
                "SELECT 1 FROM decks WHERE canonical_path = ?",
                (item["path"],),
            ).fetchone()
            if still_indexed is None or Path(str(item["path"])).is_file():
                continue
            final_item = {
                **item,
                "action": ("removed" if plan["missingPolicy"] == "remove" else "missing"),
            }
            _insert_run_item(connection, run_id, missing_root_id, final_item)
            confirmed_missing_paths.append(Path(str(item["path"])))
            stats["missing"] += 1
            row = connection.execute(
                "SELECT last_result_json FROM scan_plan_roots WHERE id = ?",
                (missing_root_id,),
            ).fetchone()
            root_result = json.loads(row[0]) if row else {}
            root_result["missing"] = int(root_result.get("missing", 0)) + 1
            connection.execute(
                "UPDATE scan_plan_roots SET last_result_json = ? WHERE id = ?",
                (_json(root_result), missing_root_id),
            )
        cancelled = should_cancel is not None and should_cancel()
        if (
            retry_paths is None
            and plan["missingPolicy"] == "remove"
            and not stats["failed"]
            and not cancelled
        ):
            removed = sync_removed_source_paths(
                connection,
                confirmed_missing_paths,
                settings=settings,
            )
            stats["removed"] += len(removed)
        if cancelled:
            status = "cancelled"
        elif stats["failed"] and sum(
            stats[key] for key in ("created", "updated", "moved", "unchanged", "repair_preview")
        ):
            status = "partial"
        elif stats["failed"]:
            status = "failed"
        else:
            status = "completed"
        connection.execute(
            """
            UPDATE scan_runs
            SET status = ?, stats_json = ?, finished_at = ?
            WHERE id = ?
            """,
            (status, _json(dict(stats)), _now(), run_id),
        )
    except BaseException as error:
        connection.execute(
            """
            UPDATE scan_runs
            SET status = 'failed', stats_json = ?, error_message = ?, finished_at = ?
            WHERE id = ?
            """,
            (_json(dict(stats)), str(error), _now(), run_id),
        )
        raise
    finally:
        connection.close()
    return {
        "ok": status in {"completed", "partial", "cancelled"},
        "runId": run_id,
        "status": status,
        "stats": dict(stats),
    }


def retry_scan_run(
    settings: Settings,
    run_id: str,
    *,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> JsonDict:
    connection = connect(settings.database_path)
    try:
        run = connection.execute("SELECT plan_id FROM scan_runs WHERE id = ?", (run_id,)).fetchone()
        if run is None or run["plan_id"] is None:
            raise ValueError("原运行记录或扫描计划已不存在")
        rows = connection.execute(
            """
            SELECT root_id, path
            FROM scan_run_items
            WHERE run_id = ? AND decision IN ('failed', 'cancelled')
              AND root_id IS NOT NULL
            ORDER BY created_at, path
            """,
            (run_id,),
        ).fetchall()
    finally:
        connection.close()
    retry_paths: dict[str, list[Path]] = {}
    for row in rows:
        retry_paths.setdefault(str(row["root_id"]), []).append(Path(str(row["path"])))
    if not retry_paths:
        raise ValueError("该运行记录没有可重试的失败或取消项")
    return run_scan_plan(
        settings,
        str(run["plan_id"]),
        trigger="retry",
        retry_paths=retry_paths,
        on_progress=on_progress,
        should_cancel=should_cancel,
    )


def record_missed_scan_run(
    settings: Settings,
    plan_id: str,
    *,
    scheduled_for: str | None = None,
) -> JsonDict:
    plan = get_scan_plan(settings, plan_id)
    now = _now()
    started_at = now
    if scheduled_for:
        started_at = datetime.fromisoformat(scheduled_for.replace("Z", "+00:00")).isoformat()
    run_id = new_id("scan_run")
    connection = connect(settings.database_path)
    try:
        connection.execute(
            """
            INSERT INTO scan_runs(
                id, plan_id, plan_name, trigger_type, scope_type,
                status, rules_json, stats_json, error_message,
                started_at, finished_at, scheduled_for
            ) VALUES (?, ?, ?, 'scheduled', 'plan', 'missed', ?, '{}', ?, ?, ?, ?)
            """,
            (
                run_id,
                plan_id,
                plan["name"],
                _json(_token_payload(plan)),
                "已超出执行窗口，未在白天补跑",
                started_at,
                now,
                started_at,
            ),
        )
    finally:
        connection.close()
    return {"ok": True, "runId": run_id, "status": "missed"}


def recover_interrupted_runs(settings: Settings) -> int:
    connection = connect(settings.database_path)
    try:
        cursor = connection.execute(
            """
            UPDATE scan_runs
            SET status = 'interrupted', finished_at = ?,
                error_message = COALESCE(error_message, '应用在任务完成前退出')
            WHERE status IN ('preflighting', 'running', 'stopping')
            """,
            (_now(),),
        )
        return cursor.rowcount
    finally:
        connection.close()


def scan_run_history(settings: Settings, *, limit: int = 50) -> JsonDict:
    if limit < 1 or limit > 200:
        raise ValueError("历史记录数量需在 1-200 之间")
    connection = connect(settings.database_path)
    try:
        rows = connection.execute(
            "SELECT * FROM scan_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        runs = []
        for row in rows:
            items = connection.execute(
                "SELECT * FROM scan_run_items WHERE run_id = ? ORDER BY created_at, path",
                (row["id"],),
            ).fetchall()
            runs.append(
                {
                    "id": str(row["id"]),
                    "planId": row["plan_id"],
                    "planName": str(row["plan_name"]),
                    "triggerType": str(row["trigger_type"]),
                    "scopeType": str(row["scope_type"]),
                    "scopeRootId": row["scope_root_id"],
                    "status": str(row["status"]),
                    "stats": json.loads(row["stats_json"]),
                    "errorMessage": row["error_message"],
                    "startedAt": str(row["started_at"]),
                    "finishedAt": row["finished_at"],
                    "scheduledFor": row["scheduled_for"],
                    "items": [
                        {
                            "id": str(item["id"]),
                            "rootId": item["root_id"],
                            "path": str(item["path"]),
                            "decision": str(item["decision"]),
                            "action": str(item["action"]),
                            "reason": str(item["reason"]),
                            "sizeBytes": int(item["size_bytes"]),
                            "slideCount": int(item["slide_count"]),
                            "elapsedMs": int(item["elapsed_ms"]),
                        }
                        for item in items
                    ],
                }
            )
    finally:
        connection.close()
    return {"ok": True, "runs": runs}


def current_scan_run(settings: Settings) -> JsonDict:
    connection = connect(settings.database_path)
    try:
        row = connection.execute(
            """
            SELECT id, plan_id, plan_name, trigger_type, scope_type,
                   scope_root_id, status, stats_json, started_at, scheduled_for
            FROM scan_runs
            WHERE status IN ('preflighting', 'running', 'stopping')
            ORDER BY started_at DESC LIMIT 1
            """
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return {"ok": True, "run": None}
    return {
        "ok": True,
        "run": {
            "id": str(row["id"]),
            "planId": row["plan_id"],
            "planName": str(row["plan_name"]),
            "triggerType": str(row["trigger_type"]),
            "scopeType": str(row["scope_type"]),
            "scopeRootId": row["scope_root_id"],
            "status": str(row["status"]),
            "stats": json.loads(row["stats_json"]),
            "startedAt": str(row["started_at"]),
            "scheduledFor": row["scheduled_for"],
        },
    }
