"""
Sleep tracker router — 作息记录.

POST /sleep/log    — log a wake or sleep event
GET  /sleep/history — past N days of sleep data
"""
from datetime import date, datetime
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from database import get_db

router = APIRouter(prefix="/sleep", tags=["sleep"])


class SleepLogIn(BaseModel):
    action_type: str = Field(..., pattern=r"^(wake|sleep)$")
    actual_time: str  # ISO datetime string, e.g. "2026-07-11T07:30:00"
    is_late: bool = False


class SleepLogOut(BaseModel):
    id: int
    action_type: str
    actual_time: str
    is_late: bool
    created_at: str | None = None


@router.post("/log", response_model=SleepLogOut, status_code=201)
def log_sleep(body: SleepLogIn):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO sleep_tracker (action_type, actual_time, is_late) VALUES (?,?,?)",
            (body.action_type, body.actual_time, body.is_late),
        )
        sid = cur.lastrowid
        cur.execute("SELECT * FROM sleep_tracker WHERE id=?", (sid,))
        return SleepLogOut(**dict(cur.fetchone()))


@router.get("/history", response_model=list[SleepLogOut])
def sleep_history(days: int = Query(7, ge=1, le=90)):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
        SELECT * FROM sleep_tracker
        WHERE DATE(actual_time) >= DATE('now', ?)
        ORDER BY actual_time DESC
        """, (f'-{days} days',))
        return [SleepLogOut(**dict(r)) for r in cur.fetchall()]


@router.get("/today", response_model=dict)
def sleep_today():
    """Return today's wake/sleep events."""
    today_str = date.today().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM sleep_tracker WHERE DATE(actual_time)=? ORDER BY actual_time ASC", (today_str,))
        rows = cur.fetchall()
    wake = next((dict(r) for r in rows if r["action_type"] == "wake"), None)
    sleep = next((dict(r) for r in rows if r["action_type"] == "sleep"), None)
    return {"date": today_str, "wake": wake, "sleep": sleep}
