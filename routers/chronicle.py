"""
Daily Chronicle router — 每日纪事时间轴 (v3.0)

Replaces the old sleep_tracker action-log model with a natural
wake_time / sleep_time per-day record.

POST /chronicle/wake   — upsert today's wake_time
POST /chronicle/sleep  — upsert today's sleep_time
GET  /chronicle/today  — return today's record
GET  /chronicle/history — past N days
"""
from datetime import timedelta
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from database import get_db, dojo_today_str, now_cst_iso, now_cst

router = APIRouter(prefix="/chronicle", tags=["chronicle"])


class ChronicleOut(BaseModel):
    id: int
    record_date: str
    wake_time: str | None = None
    sleep_time: str | None = None


# ── Helpers ────────────────────────────────────────────────────────────


def _upsert_today(cur, field: str, value: str):
    """Ensure today's dojo-date daily_chronicle row exists, then set field."""
    today = dojo_today_str()
    cur.execute("SELECT id FROM daily_chronicle WHERE record_date = ?", (today,))
    row = cur.fetchone()
    if row:
        cur.execute(
            f"UPDATE daily_chronicle SET {field} = ? WHERE record_date = ?",
            (value, today),
        )
    else:
        cur.execute(
            f"INSERT INTO daily_chronicle (record_date, {field}) VALUES (?, ?)",
            (today, value),
        )


# ── Routes ─────────────────────────────────────────────────────────────

class ChronoRequest(BaseModel):
    time: str | None = None         # "HH:MM"
    record_date: str | None = None  # "YYYY-MM-DD" — frontend handles 5AM cutoff

@router.post("/wake", response_model=dict, status_code=201)
def wake_up(data: ChronoRequest | None = None):
    """早起打卡 — 支持指定时间和日期。可重复调用，以最后一次为准。"""
    record_date = data.record_date if data and data.record_date else dojo_today_str()
    default_time = now_cst().strftime("%H:%M:%S") if not (data and data.time) else None
    ts = record_date + "T" + (data.time + ":00" if data and data.time else default_time)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM daily_chronicle WHERE record_date = ?", (record_date,))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE daily_chronicle SET wake_time = ? WHERE record_date = ?", (ts, record_date))
        else:
            cur.execute("INSERT INTO daily_chronicle (record_date, wake_time) VALUES (?, ?)", (record_date, ts))
        cur.execute("SELECT * FROM daily_chronicle WHERE record_date = ?", (record_date,))
        record = cur.fetchone()
    return {
        "record_date": record_date,
        "wake_time": ts,
        "sleep_time": record["sleep_time"] if record else None,
    }


@router.post("/sleep", response_model=dict, status_code=201)
def go_to_sleep(data: ChronoRequest | None = None):
    """入睡打卡 — 5AM分界：未指定日期时，00:00-04:59自动记录为前一天。可重复调用。"""
    # 5AM cutoff: if no explicit date and current Shanghai hour < 5, use yesterday
    if not (data and data.record_date):
        now = now_cst()
        if now.hour < 5:
            record_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            record_date = dojo_today_str()
    else:
        record_date = data.record_date
    default_time = now_cst().strftime("%H:%M:%S") if not (data and data.time) else None
    ts = record_date + "T" + (data.time + ":00" if data and data.time else default_time)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM daily_chronicle WHERE record_date = ?", (record_date,))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE daily_chronicle SET sleep_time = ? WHERE record_date = ?", (ts, record_date))
        else:
            cur.execute("INSERT INTO daily_chronicle (record_date, sleep_time) VALUES (?, ?)", (record_date, ts))
        cur.execute("SELECT * FROM daily_chronicle WHERE record_date = ?", (record_date,))
        record = cur.fetchone()
    return {
        "record_date": record_date,
        "wake_time": record["wake_time"] if record else None,
        "sleep_time": ts,
    }


@router.get("/today", response_model=dict)
def get_today():
    """返回今日作息记录。"""
    today = dojo_today_str()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM daily_chronicle WHERE record_date = ?", (today,))
        row = cur.fetchone()

    if not row:
        return {"record_date": today, "wake_time": None, "sleep_time": None}
    return dict(row)


@router.get("/history", response_model=list[dict])
def get_history(days: int = Query(7, ge=1, le=90)):
    """返回过去 N 天的作息时间轴。"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM daily_chronicle WHERE record_date >= DATE('now', ?) ORDER BY record_date DESC",
            (f"-{days} days",),
        )
        return [dict(r) for r in cur.fetchall()]
