from __future__ import annotations

import argparse
import io
import json
import mimetypes
import os
import posixpath
import re
import secrets
import sqlite3
import sys
import traceback
import urllib.parse
import warnings
import zipfile
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

warnings.filterwarnings("ignore", category=DeprecationWarning, message="'cgi' is deprecated.*")
import cgi


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DATA_DIR = Path(os.environ.get("LTMS_DATA_DIR", str(ROOT / "data"))).resolve()
DB_PATH = DATA_DIR / "tickets.db"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123456")
ADMIN_SESSIONS: set[str] = set()
SESSION_COOKIE = "ltms_admin_session"
FEEDBACK_STATUSES = {
    "pending": "未处理",
    "resolved": "已处理",
    "rejected": "已驳回",
}

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF_MIME = "application/pdf"
ZIP_MIME = "application/zip"
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
COLLEGE_HEADER_TEMPLATE = ROOT / "学院抬头文件.doc"
FALLBACK_COLLEGE_HEADER_LINES = [
    "华南理工大学电子与信息学院",
    "SCHOOL OF ELECTRONIC AND INFORMATION ENGINEERING",
    "SOUTH CHINA UNIVERSITY OF TECHNOLOGY",
]
PDF_PAGE_WIDTH = 595.28
PDF_PAGE_HEIGHT = 841.89
PDF_MARGIN = 54
_COLLEGE_HEADER_LINES: list[str] | None = None

TICKET_REQUIREMENTS: dict[str, int] = {
    "本博创新班-研究生阶段": 20,
    "学术型直博生": 20,
    "专业型直博生": 0,
    "学术型博士": 15,
    "学术型硕士": 10,
    "本硕创新班-研究生阶段": 10,
    "全日制专业学位硕士": 5,
    "工程博士": 0,
}

STUDENT_ID_ALIASES = ["学号", "学生学号", "学生编号", "student_id", "studentid"]
STUDENT_NAME_ALIASES = ["姓名", "学生姓名", "学生名称", "name", "student_name"]
LEVEL_ALIASES = [
    "学历层次",
    "层次",
    "讲座票要求",
    "讲座票要求类别",
    "票要求类别",
    "培养层次",
    "培养层次代码显示值",
    "学生类别",
    "学生类别代码显示值",
    "学生分类",
    "学生分类代码显示值",
    "学位类型",
    "学位类型代码显示值",
    "学位类别",
]
ACTIVITY_NAME_ALIASES = ["活动名称", "讲座名称", "活动", "讲座", "activity_name"]
ACTIVITY_TIME_ALIASES = ["活动时间", "讲座时间", "时间", "举办时间", "activity_time"]

INFERENCE_FIELDS = [
    "学历层次",
    "培养层次",
    "培养层次代码显示值",
    "学生类别",
    "学生类别代码显示值",
    "学生分类",
    "学生分类代码显示值",
    "学位类型",
    "学位类型代码显示值",
    "学位类别",
    "是否专业学位",
    "学习方式代码显示值",
    "培养方式代码显示值",
    "备注",
]


class AppError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def backup_database(reason: str) -> str | None:
    if not DB_PATH.exists():
        return None
    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    safe_reason = re.sub(r"[^a-zA-Z0-9_-]+", "_", reason).strip("_") or "backup"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = backup_dir / f"tickets_{stamp}_{safe_reason}.db"
    source = sqlite3.connect(DB_PATH)
    target = sqlite3.connect(backup_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    backups = sorted(backup_dir.glob("tickets_*.db"), key=lambda path: path.stat().st_mtime, reverse=True)
    for old_backup in backups[50:]:
        try:
            old_backup.unlink()
        except OSError:
            pass
    return backup_path.name


def json_default(value: Any) -> str:
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return str(value)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\u3000", " ").strip()
    return re.sub(r"\s+", " ", text)


def normalize_name(value: Any) -> str:
    return re.sub(r"\s+", "", clean_text(value)).lower()


def normalize_student_id(value: Any) -> str:
    text = clean_text(value)
    text = text.replace(" ", "").replace("\u3000", "")
    if re.fullmatch(r"\d+\.0+", text):
        return text.split(".", 1)[0]
    return text


def normalize_header(value: Any) -> str:
    text = clean_text(value).lower()
    text = re.sub(r"[\s:：/_\-（）()\[\]【】]+", "", text)
    return text


def normalize_level_key(value: Any) -> str:
    text = clean_text(value)
    text = text.replace("－", "-").replace("—", "-").replace("–", "-")
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"\s+", "", text)
    return text


def requirement_for_level(level: str) -> int:
    return TICKET_REQUIREMENTS[level]


def infer_education_level(level_value: Any = "", row: dict[str, Any] | None = None) -> str:
    direct = normalize_level_key(level_value)
    if direct:
        for level in TICKET_REQUIREMENTS:
            if normalize_level_key(level) == direct:
                return level
        for level in TICKET_REQUIREMENTS:
            key = normalize_level_key(level)
            if key in direct or direct in key:
                return level

    fragments: list[str] = []
    if direct:
        fragments.append(direct)
    if row:
        for field in INFERENCE_FIELDS:
            value = row.get(field)
            if value:
                fragments.append(normalize_level_key(value))
    merged = "".join(fragments)

    if not merged:
        raise ValueError("缺少学历层次")
    if "本博" in merged:
        return "本博创新班-研究生阶段"
    if "本硕" in merged:
        return "本硕创新班-研究生阶段"
    if "工程博士" in merged or ("工程" in merged and "博士" in merged and "专业学位" in merged):
        return "工程博士"

    has_direct_phd = "直博" in merged or "直接攻博" in merged
    is_professional = any(token in merged for token in ["专业型", "专业学位", "专硕", "专博"])
    is_academic = any(token in merged for token in ["学术型", "学术学位", "学术"])
    is_phd = "博士" in merged
    is_master = "硕士" in merged

    if has_direct_phd and is_professional:
        return "专业型直博生"
    if has_direct_phd:
        return "学术型直博生"
    if is_phd and is_academic:
        return "学术型博士"
    if is_master and is_professional:
        return "全日制专业学位硕士"
    if is_master and is_academic:
        return "学术型硕士"

    raise ValueError(f"无法识别学历层次：{clean_text(level_value) or merged}")


def init_db() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                education_level TEXT NOT NULL,
                requirement INTEGER NOT NULL,
                current_tickets INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS ticket_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL,
                student_name TEXT NOT NULL,
                activity_name TEXT NOT NULL,
                activity_time TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                source_row INTEGER,
                FOREIGN KEY(student_id) REFERENCES students(student_id) ON DELETE CASCADE,
                UNIQUE(student_id, activity_name, activity_time)
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_ticket_events_student ON ticket_events(student_id)")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL,
                activity_name TEXT NOT NULL,
                activity_time TEXT NOT NULL,
                message TEXT NOT NULL,
                contact TEXT,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                admin_note TEXT,
                handled_at TEXT,
                ticket_granted INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(student_id) REFERENCES students(student_id) ON DELETE CASCADE
            )
            """
        )
        ensure_column(db, "feedback", "status", "TEXT NOT NULL DEFAULT 'pending'")
        ensure_column(db, "feedback", "admin_note", "TEXT")
        ensure_column(db, "feedback", "handled_at", "TEXT")
        ensure_column(db, "feedback", "ticket_granted", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "feedback", "admin_deleted", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "feedback", "deleted_at", "TEXT")
        db.execute("CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_feedback_activity ON feedback(activity_name, activity_time)")
        try:
            db.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_once
                    ON feedback(student_id, activity_name, activity_time)
                """
            )
        except sqlite3.IntegrityError:
            # Existing duplicate feedback is left intact; submit_feedback still blocks new duplicates.
            pass
        db.commit()


def connect_db() -> sqlite3.Connection:
    init_db()
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def connect_readonly_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise AppError("数据库尚未初始化", 404)
    db = sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def ensure_column(db: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def col_letters_to_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref.upper())
    if not letters:
        return 1
    total = 0
    for ch in letters.group(0):
        total = total * 26 + (ord(ch) - ord("A") + 1)
    return total


def index_to_col_letters(index: int) -> str:
    letters = []
    while index:
        index, remainder = divmod(index - 1, 26)
        letters.append(chr(ord("A") + remainder))
    return "".join(reversed(letters))


def parse_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for item in root.findall(f"{{{MAIN_NS}}}si"):
        strings.append("".join(t.text or "" for t in item.findall(f".//{{{MAIN_NS}}}t")))
    return strings


def parse_date_styles(archive: zipfile.ZipFile) -> set[int]:
    if "xl/styles.xml" not in archive.namelist():
        return set()

    built_in_date_ids = {
        14,
        15,
        16,
        17,
        18,
        19,
        20,
        21,
        22,
        27,
        28,
        29,
        30,
        31,
        32,
        33,
        34,
        35,
        36,
        45,
        46,
        47,
        50,
        51,
        52,
        53,
        54,
        55,
        56,
        57,
        58,
    }
    root = ET.fromstring(archive.read("xl/styles.xml"))
    custom_date_ids: set[int] = set()
    for num_fmt in root.findall(f".//{{{MAIN_NS}}}numFmt"):
        fmt_id = int(num_fmt.get("numFmtId", "0"))
        code = num_fmt.get("formatCode", "").lower()
        code = re.sub(r'"[^"]*"', "", code)
        if any(token in code for token in ["yy", "yyyy", "dd", "hh", "ss"]):
            custom_date_ids.add(fmt_id)

    date_styles: set[int] = set()
    cell_xfs = root.find(f"{{{MAIN_NS}}}cellXfs")
    if cell_xfs is None:
        return date_styles
    for index, xf in enumerate(cell_xfs.findall(f"{{{MAIN_NS}}}xf")):
        fmt_id = int(xf.get("numFmtId", "0"))
        if fmt_id in built_in_date_ids or fmt_id in custom_date_ids:
            date_styles.add(index)
    return date_styles


def excel_number_text(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    if re.fullmatch(r"-?\d+\.0+", text):
        return text.split(".", 1)[0]
    if re.fullmatch(r"-?\d+\.\d+", text):
        return text.rstrip("0").rstrip(".")
    if re.fullmatch(r"-?\d+(\.\d+)?[eE][+\-]?\d+", text):
        try:
            value = Decimal(text)
        except InvalidOperation:
            return text
        if value == value.to_integral_value():
            return format(value.quantize(Decimal(1)), "f")
        return format(value.normalize(), "f").rstrip("0").rstrip(".")
    return text


def excel_date_text(raw: str) -> str:
    try:
        serial = float(raw)
    except ValueError:
        return excel_number_text(raw)
    dt = datetime(1899, 12, 30) + timedelta(days=serial)
    if abs(serial - round(serial)) < 0.000001:
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d %H:%M")


def cell_text(cell: ET.Element, shared_strings: list[str], date_styles: set[int]) -> str:
    cell_type = cell.get("t", "")
    style = int(cell.get("s", "0") or "0")

    if cell_type == "inlineStr":
        return clean_text("".join(t.text or "" for t in cell.findall(f".//{{{MAIN_NS}}}t")))

    value_node = cell.find(f"{{{MAIN_NS}}}v")
    raw = "" if value_node is None or value_node.text is None else value_node.text

    if cell_type == "s":
        try:
            return clean_text(shared_strings[int(raw)])
        except (ValueError, IndexError):
            return ""
    if cell_type == "b":
        return "是" if raw == "1" else "否"
    if style in date_styles and raw:
        return clean_text(excel_date_text(raw))
    return clean_text(excel_number_text(raw))


def first_sheet_path(archive: zipfile.ZipFile) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rel_targets = {
        rel.get("Id"): rel.get("Target", "")
        for rel in rels.findall(f"{{{PKG_REL_NS}}}Relationship")
    }
    sheet = workbook.find(f".//{{{MAIN_NS}}}sheet")
    if sheet is None:
        raise AppError("Excel 文件中没有工作表")
    rel_id = sheet.get(f"{{{REL_NS}}}id")
    target = rel_targets.get(rel_id, "")
    if not target:
        raise AppError("无法读取 Excel 工作表")
    if target.startswith("/"):
        path = target.lstrip("/")
    else:
        path = posixpath.normpath(posixpath.join("xl", target))
    return path


def read_xlsx_rows(data: bytes) -> list[list[str]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise AppError("请上传 .xlsx 格式的 Excel 文件") from exc

    with archive:
        shared_strings = parse_shared_strings(archive)
        date_styles = parse_date_styles(archive)
        sheet_path = first_sheet_path(archive)
        if sheet_path not in archive.namelist():
            raise AppError("无法找到 Excel 第一张工作表")
        root = ET.fromstring(archive.read(sheet_path))
        rows: list[list[str]] = []
        for row in root.findall(f".//{{{MAIN_NS}}}row"):
            values: list[str] = []
            for cell in row.findall(f"{{{MAIN_NS}}}c"):
                ref = cell.get("r", "A1")
                col_index = col_letters_to_index(ref)
                while len(values) < col_index - 1:
                    values.append("")
                values.append(cell_text(cell, shared_strings, date_styles))
            rows.append(values)
        return rows


def find_header(headers: list[str], aliases: list[str]) -> str | None:
    normalized = {normalize_header(header): header for header in headers if clean_text(header)}
    for alias in aliases:
        found = normalized.get(normalize_header(alias))
        if found is not None:
            return found
    return None


def records_from_xlsx(data: bytes, required_groups: list[list[str]]) -> tuple[list[dict[str, str]], list[str]]:
    rows = read_xlsx_rows(data)
    for header_index, row in enumerate(rows[:30]):
        headers = [clean_text(value) for value in row]
        if all(find_header(headers, group) for group in required_groups):
            records: list[dict[str, str]] = []
            for row_number, values in enumerate(rows[header_index + 1 :], start=header_index + 2):
                if not any(clean_text(value) for value in values):
                    continue
                record: dict[str, str] = {"_row_number": str(row_number)}
                for index, header in enumerate(headers):
                    if header:
                        record[header] = clean_text(values[index] if index < len(values) else "")
                records.append(record)
            return records, headers
    raise AppError("没有找到符合要求的表头，请检查 Excel 第一张表是否包含必要列")


def row_value(record: dict[str, str], header: str | None) -> str:
    if not header:
        return ""
    return clean_text(record.get(header, ""))


def import_students(data: bytes) -> dict[str, Any]:
    records, headers = records_from_xlsx(data, [STUDENT_ID_ALIASES, STUDENT_NAME_ALIASES])
    id_header = find_header(headers, STUDENT_ID_ALIASES)
    name_header = find_header(headers, STUDENT_NAME_ALIASES)
    level_header = find_header(headers, LEVEL_ALIASES)

    failures: list[dict[str, str]] = []
    imported = 0
    updated = 0
    seen_ids: set[str] = set()
    timestamp = now_text()
    backup_name = backup_database("import_students")

    with connect_db() as db:
        for record in records:
            row_number = record["_row_number"]
            student_id = normalize_student_id(row_value(record, id_header))
            name = clean_text(row_value(record, name_header))
            if not student_id or not name:
                failures.append({"row": row_number, "reason": "缺少姓名或学号"})
                continue
            if student_id in seen_ids:
                failures.append({"row": row_number, "student_id": student_id, "name": name, "reason": "文件内学号重复"})
                continue
            seen_ids.add(student_id)

            try:
                level = infer_education_level(row_value(record, level_header), record)
            except ValueError as exc:
                failures.append({"row": row_number, "student_id": student_id, "name": name, "reason": str(exc)})
                continue

            requirement = requirement_for_level(level)
            exists = db.execute(
                "SELECT student_id FROM students WHERE student_id = ?",
                (student_id,),
            ).fetchone()
            if exists:
                db.execute(
                    """
                    UPDATE students
                       SET name = ?, education_level = ?, requirement = ?, updated_at = ?
                     WHERE student_id = ?
                    """,
                    (name, level, requirement, timestamp, student_id),
                )
                updated += 1
            else:
                db.execute(
                    """
                    INSERT INTO students
                        (student_id, name, education_level, requirement, current_tickets, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 0, ?, ?)
                    """,
                    (student_id, name, level, requirement, timestamp, timestamp),
                )
                imported += 1
        db.commit()

    return {
        "imported": imported,
        "updated": updated,
        "failed": len(failures),
        "failures": failures,
        "backup": backup_name,
        "message": f"新增 {imported} 人，更新 {updated} 人，失败 {len(failures)} 行",
    }


def import_events(data: bytes) -> dict[str, Any]:
    records, headers = records_from_xlsx(
        data,
        [STUDENT_ID_ALIASES, STUDENT_NAME_ALIASES, ACTIVITY_NAME_ALIASES, ACTIVITY_TIME_ALIASES],
    )
    id_header = find_header(headers, STUDENT_ID_ALIASES)
    name_header = find_header(headers, STUDENT_NAME_ALIASES)
    activity_header = find_header(headers, ACTIVITY_NAME_ALIASES)
    time_header = find_header(headers, ACTIVITY_TIME_ALIASES)

    failures: list[dict[str, str]] = []
    success = 0
    timestamp = now_text()
    seen_events: set[tuple[str, str, str]] = set()
    backup_name = backup_database("import_events")

    with connect_db() as db:
        for record in records:
            row_number = record["_row_number"]
            student_id = normalize_student_id(row_value(record, id_header))
            name = clean_text(row_value(record, name_header))
            activity_name = clean_text(row_value(record, activity_header))
            activity_time = clean_text(row_value(record, time_header))

            failure_base = {"row": row_number, "student_id": student_id, "name": name}
            if not student_id or not name:
                failures.append({**failure_base, "reason": "缺少姓名或学号"})
                continue
            if not activity_name or not activity_time:
                failures.append({**failure_base, "reason": "缺少活动名称或活动时间"})
                continue

            student = db.execute(
                "SELECT student_id, name FROM students WHERE student_id = ?",
                (student_id,),
            ).fetchone()
            if student is None:
                failures.append({**failure_base, "reason": "学生名单中没有该学号"})
                continue
            if normalize_name(student["name"]) != normalize_name(name):
                failures.append(
                    {
                        **failure_base,
                        "reason": f"姓名与学号不匹配，系统记录为：{student['name']}",
                    }
                )
                continue

            event_key = (student_id, activity_name, activity_time)
            if event_key in seen_events:
                failures.append({**failure_base, "reason": "导入文件内重复活动记录"})
                continue
            seen_events.add(event_key)

            try:
                db.execute(
                    """
                    INSERT INTO ticket_events
                        (student_id, student_name, activity_name, activity_time, imported_at, source_row)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (student_id, student["name"], activity_name, activity_time, timestamp, int(row_number)),
                )
            except sqlite3.IntegrityError:
                failures.append({**failure_base, "reason": "该学生的同一活动记录已导入过"})
                continue

            db.execute(
                """
                UPDATE students
                   SET current_tickets = current_tickets + 1, updated_at = ?
                 WHERE student_id = ?
                """,
                (timestamp, student_id),
            )
            success += 1
        db.commit()

    return {
        "imported": success,
        "failed": len(failures),
        "failures": failures,
        "backup": backup_name,
        "message": f"成功计入 {success} 条讲座票，失败 {len(failures)} 行",
    }


def student_summary(db: sqlite3.Connection) -> dict[str, int]:
    row = db.execute(
        """
        SELECT COUNT(*) AS total,
               COALESCE(SUM(current_tickets), 0) AS tickets,
               SUM(CASE WHEN current_tickets >= requirement THEN 1 ELSE 0 END) AS completed,
               SUM(CASE WHEN current_tickets < requirement THEN 1 ELSE 0 END) AS unfinished
          FROM students
        """
    ).fetchone()
    events = db.execute("SELECT COUNT(*) AS total FROM ticket_events").fetchone()
    return {
        "students": int(row["total"] or 0),
        "tickets": int(row["tickets"] or 0),
        "completed": int(row["completed"] or 0),
        "unfinished": int(row["unfinished"] or 0),
        "events": int(events["total"] or 0),
    }


def list_students(query: dict[str, list[str]]) -> dict[str, Any]:
    search = clean_text(query.get("search", [""])[0])
    status = clean_text(query.get("status", ["all"])[0])
    where: list[str] = []
    params: list[Any] = []
    if search:
        where.append("(student_id LIKE ? OR name LIKE ? OR education_level LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])
    if status == "completed":
        where.append("current_tickets >= requirement")
    elif status == "unfinished":
        where.append("current_tickets < requirement")

    sql = """
        SELECT student_id, name, education_level, requirement, current_tickets, updated_at
          FROM students
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY student_id COLLATE NOCASE LIMIT 1000"

    with connect_db() as db:
        rows = [dict(row) for row in db.execute(sql, params).fetchall()]
        for row in rows:
            requirement = int(row["requirement"])
            current = int(row["current_tickets"])
            row["grade"] = grade_from_student_id(row["student_id"])
            row["progress_text"] = f"{current}/{requirement}"
            row["complete"] = current >= requirement
        return {"summary": student_summary(db), "students": rows, "rules": TICKET_REQUIREMENTS, "grades": list_student_grades(db)}


def query_student_ticket(query: dict[str, list[str]]) -> dict[str, Any]:
    student_id = normalize_student_id(query.get("student_id", [""])[0])
    if not student_id:
        raise AppError("请输入学号")

    db = connect_readonly_db()
    try:
        student = db.execute(
            """
            SELECT student_id, education_level, requirement, current_tickets, updated_at
              FROM students
             WHERE student_id = ?
            """,
            (student_id,),
        ).fetchone()
        if student is None:
            raise AppError("没有找到该学号", 404)

        events = [
            dict(row)
            for row in db.execute(
                """
                SELECT activity_name, activity_time, imported_at
                  FROM ticket_events
                 WHERE student_id = ?
                 ORDER BY activity_time DESC, id DESC
                 LIMIT 200
                """,
                (student_id,),
            ).fetchall()
        ]
        feedback_rows = [
            dict(row)
            for row in db.execute(
                """
                SELECT activity_name,
                       activity_time,
                       message,
                       created_at,
                       COALESCE(status, 'pending') AS status,
                       COALESCE(admin_note, '') AS admin_note,
                       COALESCE(handled_at, '') AS handled_at,
                       COALESCE(ticket_granted, 0) AS ticket_granted
                  FROM feedback
                 WHERE student_id = ?
                 ORDER BY created_at DESC, id DESC
                 LIMIT 100
                """,
                (student_id,),
            ).fetchall()
        ]
    finally:
        db.close()

    current = int(student["current_tickets"])
    requirement = int(student["requirement"])
    remaining = max(requirement - current, 0)
    percent = 100 if requirement == 0 else min(100, round(current / requirement * 100))
    return {
        "student": {
            "student_id": student["student_id"],
            "education_level": student["education_level"],
            "current_tickets": current,
            "requirement": requirement,
            "progress_text": f"{current}/{requirement}",
            "remaining": remaining,
            "complete": current >= requirement,
            "progress_percent": percent,
            "updated_at": student["updated_at"],
        },
        "events": events,
        "feedback": [
            {
                **row,
                "status_label": FEEDBACK_STATUSES.get(row["status"], row["status"]),
                "ticket_granted": bool(row["ticket_granted"]),
            }
            for row in feedback_rows
        ],
    }


def list_activity_options() -> dict[str, Any]:
    if not DB_PATH.exists():
        return {"activities": []}
    db = connect_readonly_db()
    try:
        rows = [
            dict(row)
            for row in db.execute(
                """
                SELECT MIN(id) AS activity_id,
                       activity_name,
                       activity_time,
                       COUNT(*) AS record_count
                  FROM ticket_events
                 GROUP BY activity_name, activity_time
                 ORDER BY activity_time DESC, activity_name COLLATE NOCASE
                """
            ).fetchall()
        ]
    finally:
        db.close()
    for row in rows:
        row["label"] = f"{row['activity_name']}｜{row['activity_time']}"
    return {"activities": rows}


def submit_feedback(payload: dict[str, Any]) -> dict[str, Any]:
    student_id = normalize_student_id(payload.get("student_id"))
    if not student_id:
        raise AppError("缺少学号")
    try:
        activity_id = int(payload.get("activity_id"))
    except (TypeError, ValueError) as exc:
        raise AppError("请选择讲座活动") from exc

    message = clean_text(payload.get("message"))
    contact = clean_text(payload.get("contact"))
    if not message:
        raise AppError("请填写反馈意见")
    if len(message) > 500:
        raise AppError("反馈意见不能超过 500 字")
    if len(contact) > 80:
        raise AppError("联系方式不能超过 80 字")

    timestamp = now_text()
    with connect_db() as db:
        student = db.execute(
            "SELECT student_id FROM students WHERE student_id = ?",
            (student_id,),
        ).fetchone()
        if student is None:
            raise AppError("没有找到该学号", 404)

        activity = db.execute(
            """
            SELECT activity_name, activity_time
              FROM ticket_events
             WHERE id = ?
            """,
            (activity_id,),
        ).fetchone()
        if activity is None:
            raise AppError("请选择后台已录入过的讲座活动")

        existing = db.execute(
            """
            SELECT id
              FROM feedback
             WHERE student_id = ?
               AND activity_name = ?
               AND activity_time = ?
             LIMIT 1
            """,
            (student_id, activity["activity_name"], activity["activity_time"]),
        ).fetchone()
        if existing is not None:
            raise AppError("同一学号对同一讲座活动只能提交一次反馈")

        try:
            cur = db.execute(
                """
                INSERT INTO feedback
                    (student_id, activity_name, activity_time, message, contact, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    student_id,
                    activity["activity_name"],
                    activity["activity_time"],
                    message,
                    contact,
                    timestamp,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise AppError("同一学号对同一讲座活动只能提交一次反馈") from exc
        db.commit()
        return {"ok": True, "id": cur.lastrowid, "message": "反馈已提交"}


def list_feedback() -> dict[str, Any]:
    with connect_db() as db:
        rows = [
            dict(row)
            for row in db.execute(
                """
                SELECT f.id,
                       f.student_id,
                       COALESCE(s.name, '') AS student_name,
                       COALESCE(s.education_level, '') AS education_level,
                       f.activity_name,
                       f.activity_time,
                       f.message,
                       COALESCE(f.contact, '') AS contact,
                       f.created_at,
                       COALESCE(f.status, 'pending') AS status,
                       COALESCE(f.admin_note, '') AS admin_note,
                       COALESCE(f.handled_at, '') AS handled_at,
                       COALESCE(f.ticket_granted, 0) AS ticket_granted,
                       CASE WHEN EXISTS (
                           SELECT 1
                             FROM ticket_events e
                            WHERE e.student_id = f.student_id
                              AND e.activity_name = f.activity_name
                              AND e.activity_time = f.activity_time
                       ) THEN 1 ELSE 0 END AS ticket_exists
                    FROM feedback f
               LEFT JOIN students s ON s.student_id = f.student_id
                   WHERE COALESCE(f.admin_deleted, 0) = 0
                   ORDER BY f.created_at DESC, f.id DESC
                   LIMIT 500
                  """
            ).fetchall()
        ]
    for row in rows:
        row["status_label"] = FEEDBACK_STATUSES.get(row["status"], row["status"])
        row["ticket_granted"] = bool(row["ticket_granted"])
        row["ticket_exists"] = bool(row["ticket_exists"])
    return {"feedback": rows, "count": len(rows), "statuses": FEEDBACK_STATUSES}


def update_feedback_legacy(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        feedback_id = int(payload.get("id"))
    except (TypeError, ValueError) as exc:
        raise AppError("缺少反馈编号") from exc

    status = clean_text(payload.get("status") or "pending")
    if status not in FEEDBACK_STATUSES:
        raise AppError("反馈状态不正确")
    admin_note = clean_text(payload.get("admin_note"))
    if len(admin_note) > 500:
        raise AppError("管理员备注不能超过 500 字")
    grant_ticket = bool(payload.get("grant_ticket"))
    timestamp = now_text()
    backup_name = backup_database("feedback_update")

    with connect_db() as db:
        feedback = db.execute(
            """
            SELECT id, student_id, activity_name, activity_time
              FROM feedback
             WHERE id = ?
            """,
            (feedback_id,),
        ).fetchone()
        if feedback is None:
            raise AppError("没有找到该反馈", 404)

        ticket_added = False
        if grant_ticket:
            student = db.execute(
                "SELECT student_id, name FROM students WHERE student_id = ?",
                (feedback["student_id"],),
            ).fetchone()
            if student is None:
                raise AppError("没有找到反馈对应的学生", 404)

            exists = db.execute(
                """
                SELECT id
                  FROM ticket_events
                 WHERE student_id = ?
                   AND activity_name = ?
                   AND activity_time = ?
                 LIMIT 1
                """,
                (feedback["student_id"], feedback["activity_name"], feedback["activity_time"]),
            ).fetchone()
            if exists is not None:
                raise AppError("该学生的这次讲座票已经计入，不能重复补票")

            try:
                db.execute(
                    """
                    INSERT INTO ticket_events
                        (student_id, student_name, activity_name, activity_time, imported_at, source_row)
                    VALUES (?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        feedback["student_id"],
                        student["name"],
                        feedback["activity_name"],
                        feedback["activity_time"],
                        timestamp,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise AppError("该学生的这次讲座票已经计入，不能重复补票") from exc

            db.execute(
                """
                UPDATE students
                   SET current_tickets = current_tickets + 1,
                       updated_at = ?
                 WHERE student_id = ?
                """,
                (timestamp, feedback["student_id"]),
            )
            ticket_added = True
            status = "resolved"

        handled_at = timestamp if status in {"resolved", "rejected"} or ticket_added else None
        db.execute(
            """
            UPDATE feedback
               SET status = ?,
                   admin_note = ?,
                   handled_at = ?,
                   ticket_granted = CASE WHEN ? THEN 1 ELSE ticket_granted END
             WHERE id = ?
            """,
            (status, admin_note, handled_at, int(ticket_added), feedback_id),
        )
        db.commit()

    return {
        "ok": True,
        "message": "已补票并更新反馈状态" if ticket_added else "反馈状态已更新",
        "ticket_added": ticket_added,
        "backup": backup_name,
    }


def update_feedback(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        feedback_id = int(payload.get("id"))
    except (TypeError, ValueError) as exc:
        raise AppError("缺少反馈编号") from exc

    admin_note = clean_text(payload.get("admin_note"))
    if len(admin_note) > 500:
        raise AppError("管理员备注不能超过 500 字")

    action = clean_text(payload.get("action"))
    if not action:
        if payload.get("grant_ticket"):
            action = "grant"
        elif clean_text(payload.get("status")) == "rejected":
            action = "reject"
        else:
            action = "note"
    if action not in {"note", "grant", "reject", "delete"}:
        raise AppError("反馈处理操作不正确")

    timestamp = now_text()
    backup_name = backup_database("feedback_update")

    with connect_db() as db:
        feedback = db.execute(
            """
            SELECT id,
                   student_id,
                   activity_name,
                   activity_time,
                   COALESCE(status, 'pending') AS status,
                   COALESCE(handled_at, '') AS handled_at,
                   COALESCE(ticket_granted, 0) AS ticket_granted,
                   COALESCE(admin_deleted, 0) AS admin_deleted
              FROM feedback
             WHERE id = ?
            """,
            (feedback_id,),
        ).fetchone()
        if feedback is None:
            raise AppError("没有找到该反馈", 404)

        status = feedback["status"]
        handled_at = feedback["handled_at"] or None
        ticket_granted = int(feedback["ticket_granted"] or 0)
        ticket_added = False
        ticket_removed = False

        if action == "delete":
            if status == "pending":
                raise AppError("只能删除已处理反馈记录")
            db.execute(
                """
                UPDATE feedback
                   SET admin_deleted = 1,
                       deleted_at = ?,
                       admin_note = ?
                 WHERE id = ?
                """,
                (timestamp, admin_note, feedback_id),
            )
            db.commit()
            return {
                "ok": True,
                "message": "反馈记录已删除",
                "ticket_added": False,
                "ticket_removed": False,
                "status": status,
                "deleted": True,
                "backup": backup_name,
            }

        if action == "grant":
            student = db.execute(
                "SELECT student_id, name FROM students WHERE student_id = ?",
                (feedback["student_id"],),
            ).fetchone()
            if student is None:
                raise AppError("没有找到反馈对应的学生", 404)

            exists = db.execute(
                """
                SELECT id
                  FROM ticket_events
                 WHERE student_id = ?
                   AND activity_name = ?
                   AND activity_time = ?
                 LIMIT 1
                """,
                (feedback["student_id"], feedback["activity_name"], feedback["activity_time"]),
            ).fetchone()

            if exists is None:
                try:
                    db.execute(
                        """
                        INSERT INTO ticket_events
                            (student_id, student_name, activity_name, activity_time, imported_at, source_row)
                        VALUES (?, ?, ?, ?, ?, NULL)
                        """,
                        (
                            feedback["student_id"],
                            student["name"],
                            feedback["activity_name"],
                            feedback["activity_time"],
                            timestamp,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise AppError("该学生的这次讲座票已经计入，不能重复补票") from exc

                db.execute(
                    """
                    UPDATE students
                       SET current_tickets = current_tickets + 1,
                           updated_at = ?
                     WHERE student_id = ?
                    """,
                    (timestamp, feedback["student_id"]),
                )
                ticket_added = True
                ticket_granted = 1

            status = "resolved"
            handled_at = timestamp

        elif action == "reject":
            if ticket_granted:
                cur = db.execute(
                    """
                    DELETE FROM ticket_events
                     WHERE student_id = ?
                       AND activity_name = ?
                       AND activity_time = ?
                       AND source_row IS NULL
                    """,
                    (feedback["student_id"], feedback["activity_name"], feedback["activity_time"]),
                )
                if cur.rowcount:
                    db.execute(
                        """
                        UPDATE students
                           SET current_tickets = CASE
                                   WHEN current_tickets > 0 THEN current_tickets - 1
                                   ELSE 0
                               END,
                               updated_at = ?
                         WHERE student_id = ?
                        """,
                        (timestamp, feedback["student_id"]),
                    )
                    ticket_removed = True
                ticket_granted = 0
            status = "rejected"
            handled_at = timestamp

        db.execute(
            """
            UPDATE feedback
               SET status = ?,
                   admin_note = ?,
                   handled_at = ?,
                   ticket_granted = ?
             WHERE id = ?
            """,
            (status, admin_note, handled_at, int(ticket_granted), feedback_id),
        )
        db.commit()

    return {
        "ok": True,
        "message": "反馈已处理",
        "ticket_added": ticket_added,
        "ticket_removed": ticket_removed,
        "status": status,
        "backup": backup_name,
    }


def update_student(payload: dict[str, Any]) -> dict[str, Any]:
    student_id = normalize_student_id(payload.get("student_id"))
    if not student_id:
        raise AppError("缺少学号")
    fields: list[str] = []
    params: list[Any] = []

    if "current_tickets" in payload:
        raise AppError("后台不支持手动修改讲座票数量，请通过讲座导入或确认补票调整")

    if "education_level" in payload:
        level = infer_education_level(payload.get("education_level"))
        fields.extend(["education_level = ?", "requirement = ?"])
        params.extend([level, requirement_for_level(level)])

    if not fields:
        raise AppError("没有需要更新的字段")

    fields.append("updated_at = ?")
    params.append(now_text())
    params.append(student_id)
    backup_name = backup_database("update_student")

    with connect_db() as db:
        cur = db.execute(f"UPDATE students SET {', '.join(fields)} WHERE student_id = ?", params)
        db.commit()
        if cur.rowcount == 0:
            raise AppError("没有找到该学生", 404)
    return {"ok": True, "backup": backup_name}


def build_export_rows() -> list[list[Any]]:
    rows: list[list[Any]] = [["学号", "学历层次", "讲座票数量", "现有讲座票数量（变量）/讲座票要求"]]
    with connect_db() as db:
        for student in db.execute(
            """
            SELECT student_id, education_level, current_tickets, requirement
              FROM students
             ORDER BY student_id COLLATE NOCASE
            """
        ):
            current = int(student["current_tickets"])
            requirement = int(student["requirement"])
            rows.append(
                [
                    student["student_id"],
                    student["education_level"],
                    current,
                    f"{current}/{requirement}",
                ]
            )
    return rows


def grade_from_student_id(value: Any) -> str:
    match = re.match(r"^(\d{4})", normalize_student_id(value))
    return match.group(1) if match else ""


def list_student_grades(db: sqlite3.Connection) -> list[str]:
    grades = {
        grade
        for row in db.execute("SELECT student_id FROM students")
        if (grade := grade_from_student_id(row["student_id"]))
    }
    return sorted(grades, reverse=True)


def safe_filename_part(value: Any, fallback: str = "未命名") -> str:
    text = clean_text(value) or fallback
    text = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", text)
    text = re.sub(r"\s+", "_", text).strip("._ ")
    return text[:80] or fallback


def first_payload_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key, "")
    if isinstance(value, list):
        value = value[0] if value else ""
    return clean_text(value)


def student_ids_from_payload(value: Any) -> list[str]:
    raw_values: list[Any]
    if isinstance(value, list):
        raw_values = value
    elif value is None:
        raw_values = []
    else:
        raw_values = [value]

    student_ids: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        parts = str(raw).split(",")
        for part in parts:
            student_id = normalize_student_id(part)
            if student_id and student_id not in seen:
                seen.add(student_id)
                student_ids.append(student_id)
    return student_ids


def chunked(values: list[str], size: int = 800) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def collect_student_ticket_reports(student_ids: list[str], grade: str = "") -> list[dict[str, Any]]:
    where = ""
    params: list[Any] = []
    if grade:
        if not re.fullmatch(r"\d{4}", grade):
            raise AppError("年级格式应为 4 位年份，例如 2024")
        where = "WHERE student_id LIKE ?"
        params.append(f"{grade}%")
    elif student_ids:
        placeholders = ",".join("?" for _ in student_ids)
        where = f"WHERE student_id IN ({placeholders})"
        params.extend(student_ids)
    else:
        raise AppError("请先选择学生或年级")

    db = connect_db()
    try:
        students = [
            dict(row)
            for row in db.execute(
                f"""
                SELECT student_id, name, education_level, requirement, current_tickets, updated_at
                  FROM students
                  {where}
                 ORDER BY student_id COLLATE NOCASE
                """,
                params,
            ).fetchall()
        ]

        if not students:
            raise AppError("没有找到可导出的学生")

        events_by_student: dict[str, list[dict[str, Any]]] = {
            student["student_id"]: [] for student in students
        }
        report_ids = [student["student_id"] for student in students]
        for id_chunk in chunked(report_ids):
            placeholders = ",".join("?" for _ in id_chunk)
            for row in db.execute(
                f"""
                SELECT student_id, activity_name, activity_time, imported_at
                  FROM ticket_events
                 WHERE student_id IN ({placeholders})
                 ORDER BY student_id COLLATE NOCASE, activity_time DESC, id DESC
                """,
                id_chunk,
            ).fetchall():
                events_by_student[row["student_id"]].append(dict(row))
    finally:
        db.close()

    reports: list[dict[str, Any]] = []
    for student in students:
        current = int(student["current_tickets"])
        requirement = int(student["requirement"])
        reports.append(
            {
                "student": student,
                "events": events_by_student.get(student["student_id"], []),
                "current": current,
                "requirement": requirement,
                "complete": current >= requirement,
                "progress_text": f"{current}/{requirement}",
            }
        )
    return reports


def pdf_text_hex(value: Any) -> str:
    return clean_text(value).encode("utf-16-be").hex().upper()


def pdf_text_command(
    value: Any,
    x: float,
    y: float,
    size: float = 12,
    color: tuple[float, float, float] = (0, 0, 0),
) -> str:
    r, g, b = color
    return f"BT /F1 {size:.2f} Tf {r:.3f} {g:.3f} {b:.3f} rg {x:.2f} {y:.2f} Td <{pdf_text_hex(value)}> Tj ET"


def pdf_line_command(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    width: float = 0.8,
    color: tuple[float, float, float] = (0, 0, 0),
) -> str:
    r, g, b = color
    return f"{r:.3f} {g:.3f} {b:.3f} RG {width:.2f} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S"


def approximate_text_width(value: Any, size: float) -> float:
    text = clean_text(value)
    return len(text) * size


def centered_text_command(
    value: Any,
    y: float,
    size: float = 12,
    color: tuple[float, float, float] = (0, 0, 0),
    max_width: float | None = None,
) -> str:
    if max_width:
        width = approximate_text_width(value, size)
        if width > max_width:
            size = max(6.5, size * max_width / width)
    width = approximate_text_width(value, size)
    x = max(PDF_MARGIN, (PDF_PAGE_WIDTH - width) / 2)
    return pdf_text_command(value, x, y, size, color)


def college_header_lines() -> list[str]:
    global _COLLEGE_HEADER_LINES
    if _COLLEGE_HEADER_LINES is not None:
        return _COLLEGE_HEADER_LINES

    lines = FALLBACK_COLLEGE_HEADER_LINES[:]
    if COLLEGE_HEADER_TEMPLATE.exists():
        try:
            text = COLLEGE_HEADER_TEMPLATE.read_bytes().decode("utf-16le", errors="ignore")
            found = [line for line in FALLBACK_COLLEGE_HEADER_LINES if line in text]
            if found:
                lines = found[:3]
        except OSError:
            pass
    _COLLEGE_HEADER_LINES = lines
    return lines


def wrap_text(value: Any, max_units: int) -> list[str]:
    text = clean_text(value)
    if not text:
        return [""]

    lines: list[str] = []
    line = ""
    line_units = 0
    for ch in text:
        ch_units = 2 if ord(ch) > 127 else 1
        if line and line_units + ch_units > max_units:
            lines.append(line)
            line = ch
            line_units = ch_units
        else:
            line += ch
            line_units += ch_units
    if line:
        lines.append(line)
    return lines


def make_pdf_document(page_commands: list[list[str]]) -> bytes:
    page_ids = [6 + index * 2 for index in range(len(page_commands))]
    content_ids = [page_id + 1 for page_id in page_ids]
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_commands)} >>".encode("ascii"),
        (
            b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light "
            b"/Encoding /UniGB-UCS2-H /DescendantFonts [4 0 R] >>"
        ),
        (
            b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light "
            b"/CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 2 >> "
            b"/FontDescriptor 5 0 R /DW 1000 >>"
        ),
        (
            b"<< /Type /FontDescriptor /FontName /STSong-Light /Flags 4 "
            b"/FontBBox [-25 -254 1000 880] /ItalicAngle 0 /Ascent 880 "
            b"/Descent -120 /CapHeight 700 /StemV 80 >>"
        ),
    ]

    for page_id, content_id, commands in zip(page_ids, content_ids, page_commands):
        page = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PDF_PAGE_WIDTH:.2f} {PDF_PAGE_HEIGHT:.2f}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
        )
        content = ("\n".join(commands) + "\n").encode("ascii")
        stream = b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"endstream"
        objects.append(page.encode("ascii"))
        objects.append(stream)

    data = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(data))
        data.extend(f"{index} 0 obj\n".encode("ascii"))
        data.extend(obj)
        data.extend(b"\nendobj\n")

    xref_start = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    data.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    data.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(data)


def make_student_ticket_pdf(report: dict[str, Any]) -> bytes:
    student = report["student"]
    events = report["events"]
    current = report["current"]
    requirement = report["requirement"]
    status_text = "已满足" if report["complete"] else "未满足"
    black = (0, 0, 0)
    status_color = (0.0, 0.45, 0.18) if report["complete"] else (0.82, 0.05, 0.02)
    header_lines = college_header_lines()

    pages: list[list[str]] = []
    y = PDF_PAGE_HEIGHT - PDF_MARGIN

    def add_header() -> None:
        nonlocal y
        y = PDF_PAGE_HEIGHT - 48
        for index, line in enumerate(header_lines):
            size = 17 if index == 0 else 8.8
            gap = 8 if index == 0 else 5
            max_width = PDF_PAGE_WIDTH - PDF_MARGIN * 2 - 12
            pages[-1].append(centered_text_command(line, y, size=size, color=black, max_width=max_width))
            y -= size + gap
        y -= 10
        pages[-1].append(pdf_line_command(PDF_MARGIN, y, PDF_PAGE_WIDTH - PDF_MARGIN, y, 0.9, black))
        y -= 30

    def new_page() -> None:
        pages.append([])
        add_header()

    def ensure_space(space: float) -> None:
        nonlocal y
        if y - space < PDF_MARGIN:
            new_page()

    def add_line(
        value: Any,
        size: float = 12,
        color: tuple[float, float, float] = (0, 0, 0),
        x: float = PDF_MARGIN,
        gap: float = 6,
    ) -> None:
        nonlocal y
        line_height = size + gap
        ensure_space(line_height)
        pages[-1].append(pdf_text_command(value, x, y, size, color))
        y -= line_height

    def add_wrapped(
        value: Any,
        size: float = 12,
        color: tuple[float, float, float] = (0, 0, 0),
        x: float = PDF_MARGIN,
        max_units: int = 78,
        gap: float = 5,
    ) -> None:
        for line in wrap_text(value, max_units):
            add_line(line, size=size, color=color, x=x, gap=gap)

    def add_space(space: float) -> None:
        nonlocal y
        ensure_space(space)
        y -= space

    def add_rule(gap_before: float = 16, gap_after: float = 30) -> None:
        nonlocal y
        add_space(gap_before)
        pages[-1].append(pdf_line_command(PDF_MARGIN, y, PDF_PAGE_WIDTH - PDF_MARGIN, y, 0.6, black))
        y -= gap_after

    new_page()
    pages[-1].append(centered_text_command("讲座票获得信息", y, size=18, color=black, max_width=PDF_PAGE_WIDTH - PDF_MARGIN * 2))
    y -= 34
    add_line(f"学生姓名：{student['name']}    学号：{student['student_id']}", size=13)
    add_line(f"学历层次：{student['education_level']}", size=13)
    add_line(f"导出时间：{now_text()}", size=11, gap=10)
    add_rule(gap_before=16, gap_after=30)
    add_line(f"已获得讲座票数：{current} / 要求 {requirement}", size=16, gap=10)
    add_line(f"是否已经满足要求：{status_text}", size=22, color=status_color, gap=12)
    add_rule(gap_before=18, gap_after=30)
    add_line("详细活动列表", size=15, gap=8)

    if events:
        for index, event in enumerate(events, start=1):
            add_wrapped(f"{index}. {event['activity_name']}", size=12, max_units=72)
            add_wrapped(f"活动时间：{event['activity_time']}", size=11, x=PDF_MARGIN + 18, max_units=70)
            add_space(4)
    else:
        add_line("暂无已获得讲座票记录", size=12)

    total_pages = len(pages)
    for index, page in enumerate(pages, start=1):
        page.append(pdf_text_command(f"第 {index} / {total_pages} 页", PDF_PAGE_WIDTH - 120, 28, 9, black))
    return make_pdf_document(pages)


def student_pdf_filename(report: dict[str, Any]) -> str:
    student = report["student"]
    return f"{safe_filename_part(student['student_id'])}_{safe_filename_part(student['name'])}_讲座票明细.pdf"


def build_student_pdfs_export(payload: dict[str, Any]) -> tuple[bytes, str, str]:
    grade = first_payload_text(payload, "grade")
    student_ids = student_ids_from_payload(payload.get("student_ids"))
    if not student_ids:
        student_ids = student_ids_from_payload(payload.get("student_id"))

    reports = collect_student_ticket_reports(student_ids, grade)
    if len(reports) == 1:
        return make_student_ticket_pdf(reports[0]), student_pdf_filename(reports[0]), PDF_MIME

    buffer = io.BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for report in reports:
            filename = student_pdf_filename(report)
            if filename in used_names:
                stem = filename.removesuffix(".pdf")
                counter = 2
                while f"{stem}_{counter}.pdf" in used_names:
                    counter += 1
                filename = f"{stem}_{counter}.pdf"
            used_names.add(filename)
            archive.writestr(filename, make_student_ticket_pdf(report))

    label = f"{grade}级" if grade else f"所选{len(reports)}人"
    return buffer.getvalue(), f"{label}讲座票明细.zip", ZIP_MIME


def worksheet_xml(rows: list[list[Any]], sheet_name: str = "Sheet1") -> str:
    max_cols = max((len(row) for row in rows), default=1)
    widths = []
    for col in range(max_cols):
        max_len = 8
        for row in rows[:200]:
            if col < len(row):
                text = str(row[col])
                max_len = max(max_len, len(text.encode("utf-8")) // 2 + 2)
        widths.append(min(max(max_len, 10), 36))

    col_xml = "".join(
        f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>'
        for i, width in enumerate(widths, start=1)
    )
    row_xml: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        cells: list[str] = []
        for col_index, value in enumerate(row, start=1):
            ref = f"{index_to_col_letters(col_index)}{row_index}"
            if isinstance(value, int) and not isinstance(value, bool):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                text = escape("" if value is None else str(value))
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    freeze = (
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{MAIN_NS}" xmlns:r="{REL_NS}">'
        f"{freeze}<cols>{col_xml}</cols><sheetData>{''.join(row_xml)}</sheetData>"
        "</worksheet>"
    )


def make_xlsx(rows: list[list[Any]], sheet_name: str = "Sheet1") -> bytes:
    created = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    sheet_name_xml = escape(sheet_name[:31] or "Sheet1")
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""
    workbook = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="{MAIN_NS}" xmlns:r="{REL_NS}">
  <sheets><sheet name="{sheet_name_xml}" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
    core = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:creator>Lecture Ticket Management System</dc:creator>
  <cp:lastModifiedBy>Lecture Ticket Management System</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{created}</dcterms:modified>
</cp:coreProperties>"""
    app = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Lecture Ticket Management System</Application>
</Properties>"""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet_xml(rows, sheet_name))
        archive.writestr("docProps/core.xml", core)
        archive.writestr("docProps/app.xml", app)
    return buffer.getvalue()


def content_disposition(filename: str) -> str:
    quoted = urllib.parse.quote(filename)
    return f"attachment; filename*=UTF-8''{quoted}"


def parse_cookies(cookie_header: str | None) -> dict[str, str]:
    cookies: dict[str, str] = {}
    if not cookie_header:
        return cookies
    for item in cookie_header.split(";"):
        if "=" not in item:
            continue
        name, value = item.split("=", 1)
        cookies[name.strip()] = urllib.parse.unquote(value.strip())
    return cookies


def new_admin_session() -> str:
    token = secrets.token_urlsafe(32)
    ADMIN_SESSIONS.add(token)
    return token


def remove_admin_session(token: str) -> None:
    ADMIN_SESSIONS.discard(token)


class TicketHandler(SimpleHTTPRequestHandler):
    server_version = "LectureTicketHTTP/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), format % args))

    def send_json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def is_admin_authenticated(self) -> bool:
        token = parse_cookies(self.headers.get("Cookie")).get(SESSION_COOKIE, "")
        return token in ADMIN_SESSIONS

    def require_admin(self) -> bool:
        if self.is_admin_authenticated():
            return True
        accept = self.headers.get("Accept", "")
        if not self.path.startswith("/api/") or "text/html" in accept:
            next_path = urllib.parse.quote(self.path or "/")
            self.redirect(f"/admin/login?next={next_path}")
        else:
            self.send_json({"error": "请先登录后台"}, 401)
        return False

    def login_admin(self) -> None:
        payload = self.read_json_body()
        password = str(payload.get("password", ""))
        if not secrets.compare_digest(password, ADMIN_PASSWORD):
            raise AppError("后台密码错误", 401)
        token = new_admin_session()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie", f"{SESSION_COOKIE}={urllib.parse.quote(token)}; Path=/; HttpOnly; SameSite=Lax")
        data = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def logout_admin(self) -> None:
        token = parse_cookies(self.headers.get("Cookie")).get(SESSION_COOKIE, "")
        if token:
            remove_admin_session(token)
        data = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_download(self, data: bytes, filename: str, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", content_disposition(filename))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_xlsx(self, data: bytes, filename: str) -> None:
        self.send_download(data, filename, XLSX_MIME)

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        data = self.rfile.read(length)
        try:
            payload = json.loads(data.decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise AppError("JSON 请求格式错误") from exc
        if not isinstance(payload, dict):
            raise AppError("JSON 请求必须是对象")
        return payload

    def read_upload(self) -> bytes:
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            },
        )
        field = form["file"] if "file" in form else None
        if field is None or not getattr(field, "filename", ""):
            raise AppError("请选择要上传的 Excel 文件")
        data = field.file.read()
        if not data:
            raise AppError("上传文件为空")
        return data

    def handle_error(self, exc: Exception) -> None:
        if isinstance(exc, AppError):
            self.send_json({"error": exc.message}, exc.status)
            return
        traceback.print_exc()
        self.send_json({"error": "服务器处理失败，请查看终端日志"}, 500)

    def do_GET(self) -> None:
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            if path == "/api/student-query":
                self.send_json(query_student_ticket(query))
                return
            if path == "/api/activities":
                self.send_json(list_activity_options())
                return
            if path in {"/admin/login", "/login"}:
                if self.is_admin_authenticated():
                    self.redirect("/")
                    return
                self.serve_file(STATIC_DIR / "login.html")
                return
            if path == "/student" or path == "/student.html":
                self.serve_file(STATIC_DIR / "student.html")
                return
            if path.startswith("/static/"):
                rel = path.removeprefix("/static/")
                file_path = (STATIC_DIR / rel).resolve()
                if not str(file_path).startswith(str(STATIC_DIR.resolve())):
                    raise AppError("非法路径", 403)
                self.serve_file(file_path)
                return
            if not self.require_admin():
                return
            if path == "/api/feedback":
                self.send_json(list_feedback())
                return
            if path == "/api/students":
                self.send_json(list_students(query))
                return
            if path == "/api/export":
                self.send_xlsx(make_xlsx(build_export_rows(), "讲座票汇总"), "讲座票汇总.xlsx")
                return
            if path == "/api/export/student-pdfs":
                data, filename, content_type = build_student_pdfs_export(query)
                self.send_download(data, filename, content_type)
                return
            if path == "/api/templates/students":
                rows = [["学号", "姓名", "学历层次"]]
                self.send_xlsx(make_xlsx(rows, "学生名单模板"), "学生名单导入模板.xlsx")
                return
            if path == "/api/templates/events":
                rows = [["姓名", "学号", "活动名称", "活动时间"]]
                self.send_xlsx(make_xlsx(rows, "讲座活动模板"), "讲座活动导入模板.xlsx")
                return
            if path == "/student" or path == "/student.html":
                self.serve_file(STATIC_DIR / "student.html")
                return
            if path == "/" or path == "/index.html":
                self.serve_file(STATIC_DIR / "index.html")
                return
            if path.startswith("/static/"):
                rel = path.removeprefix("/static/")
                file_path = (STATIC_DIR / rel).resolve()
                if not str(file_path).startswith(str(STATIC_DIR.resolve())):
                    raise AppError("非法路径", 403)
                self.serve_file(file_path)
                return
            raise AppError("页面不存在", 404)
        except Exception as exc:
            self.handle_error(exc)

    def do_POST(self) -> None:
        try:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/api/admin/login":
                self.login_admin()
                return
            if parsed.path == "/api/admin/logout":
                self.logout_admin()
                return
            if parsed.path == "/api/feedback":
                self.send_json(submit_feedback(self.read_json_body()))
                return
            if not self.require_admin():
                return
            if parsed.path == "/api/import/students":
                self.send_json(import_students(self.read_upload()))
                return
            if parsed.path == "/api/import/events":
                self.send_json(import_events(self.read_upload()))
                return
            if parsed.path == "/api/export/student-pdfs":
                data, filename, content_type = build_student_pdfs_export(self.read_json_body())
                self.send_download(data, filename, content_type)
                return
            if parsed.path == "/api/students/update":
                self.send_json(update_student(self.read_json_body()))
                return
            if parsed.path == "/api/feedback/update":
                self.send_json(update_feedback(self.read_json_body()))
                return
            raise AppError("接口不存在", 404)
        except Exception as exc:
            self.handle_error(exc)

    def serve_file(self, file_path: Path) -> None:
        if not file_path.exists() or not file_path.is_file():
            raise AppError("文件不存在", 404)
        data = file_path.read_bytes()
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        if file_path.suffix.lower() in {".html", ".css", ".js"}:
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Lecture ticket management system")
    default_host = os.environ.get("HOST", "0.0.0.0" if os.environ.get("PORT") or os.environ.get("RENDER") else "127.0.0.1")
    default_port = int(os.environ.get("PORT", "8000"))
    parser.add_argument("--host", default=default_host)
    parser.add_argument("--port", default=default_port, type=int)
    args = parser.parse_args()

    init_db()
    server = ThreadingHTTPServer((args.host, args.port), TicketHandler)
    print(f"讲座票管理系统已启动：http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
