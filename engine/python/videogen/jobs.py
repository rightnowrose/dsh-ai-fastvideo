"""SQLite 任务队列 —— 让「出片」这种几十秒的活可以异步、可恢复、可查。

表结构就一张 jobs。状态机：
    pending -> running -> done | failed
进程重启时把卡在 running 的记录改回 pending（reset_stale），worker 会重跑。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .paths import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    platform    TEXT DEFAULT '',
    chat_id     TEXT DEFAULT '',
    user_id     TEXT DEFAULT '',
    umo         TEXT DEFAULT '',
    style       TEXT DEFAULT '',
    spec_json   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    stage       TEXT DEFAULT '',
    progress    REAL DEFAULT 0,
    out_path    TEXT DEFAULT '',
    duration    REAL DEFAULT 0,
    size        INTEGER DEFAULT 0,
    err         TEXT DEFAULT '',
    attempts    INTEGER DEFAULT 0,
    started_at  TEXT DEFAULT '',
    finished_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, id);
CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id, created_at);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def connect() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def enqueue(
    spec_dict: dict[str, Any],
    *,
    platform: str = "",
    chat_id: str = "",
    user_id: str = "",
    umo: str = "",
) -> int:
    init()
    now = _now()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO jobs(created_at, updated_at, platform, chat_id, user_id, umo, style, spec_json)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (now, now, platform, str(chat_id), str(user_id), umo,
             str(spec_dict.get("style") or ""), json.dumps(spec_dict, ensure_ascii=False)),
        )
        return int(cur.lastrowid)


def update(job_id: int, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = _now()
    keys = ", ".join(f"{k}=?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE jobs SET {keys} WHERE id=?", (*fields.values(), job_id))


def get(job_id: int) -> dict[str, Any] | None:
    init()
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def recent(limit: int = 10, status: str = "") -> list[dict[str, Any]]:
    init()
    with connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def spec_of(job: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(job.get("spec_json") or "{}")
    except ValueError:
        return {}


def claim_next() -> dict[str, Any] | None:
    """原子地把最早的一条 pending 改成 running 并返回。"""
    init()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM jobs WHERE status='pending' ORDER BY id LIMIT 1"
        ).fetchone()
        if not row:
            conn.execute("COMMIT")
            return None
        now = _now()
        conn.execute(
            "UPDATE jobs SET status='running', stage='claim', progress=0, attempts=attempts+1,"
            " started_at=?, updated_at=? WHERE id=?",
            (now, now, row["id"]),
        )
        conn.execute("COMMIT")
    return dict(row)


def reset_stale(older_than_seconds: int = 1800) -> int:
    """把跑了太久还卡在 running 的任务放回队列（进程被杀/重启后恢复）。"""
    init()
    cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - older_than_seconds))
    with connect() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status='pending', stage='requeued', updated_at=?"
            " WHERE status='running' AND updated_at < ?",
            (_now(), cutoff),
        )
        return int(cur.rowcount or 0)


def count_for_user(user_id: str, within_minutes: int = 60) -> int:
    init()
    cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - within_minutes * 60))
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE user_id=? AND created_at>=?", (str(user_id), cutoff)
        ).fetchone()
    return int(row["n"] if row else 0)


def stats() -> dict[str, int]:
    init()
    with connect() as conn:
        rows = conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
    return {r["status"]: int(r["n"]) for r in rows}
