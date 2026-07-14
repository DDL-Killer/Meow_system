"""
Long-term goals router — 目标管理 & 倒推拆分.

GET    /goals            — list all active goals
POST   /goals            — create a new long-term goal
PATCH  /goals/{id}/progress — update current_progress
"""
from datetime import date
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from database import get_db, DOMAINS
from typing import Optional

router = APIRouter(prefix="/goals", tags=["goals"])


class GoalCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=128)
    domain: str = Field("study", pattern=r"^(study|health|skill|project|social)$")
    target_date: str  # YYYY-MM-DD
    total_progress: int = Field(100, ge=1, le=10000)
    current_progress: int = Field(0, ge=0)


class GoalOut(BaseModel):
    id: int
    title: str
    domain: str
    target_date: str
    total_progress: int
    current_progress: int
    is_active: bool
    days_remaining: Optional[int] = None
    daily_needed: Optional[float] = None
    urgency: Optional[str] = None  # "safe" | "warning" | "critical"


class ProgressUpdate(BaseModel):
    current_progress: int = Field(..., ge=0)


@router.get("", response_model=list[GoalOut])
def list_goals():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM long_term_goals WHERE is_active=1 ORDER BY target_date ASC")
        rows = cur.fetchall()

    today = date.today()
    results = []
    for g in rows:
        gd = dict(g)
        target = date.fromisoformat(gd["target_date"])
        days_left = max((target - today).days, 1)
        gap = max(gd["total_progress"] - gd["current_progress"], 0)
        daily = round(gap / days_left, 1)

        if days_left <= 3:
            urgency = "critical"
        elif days_left <= 7:
            urgency = "warning"
        else:
            urgency = "safe"

        gd["days_remaining"] = days_left
        gd["daily_needed"] = daily if gap > 0 else 0
        gd["urgency"] = urgency
        results.append(GoalOut(**gd))
    return results


@router.post("", response_model=GoalOut, status_code=201)
def create_goal(body: GoalCreate):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO long_term_goals (title, domain, target_date, total_progress, current_progress) VALUES (?,?,?,?,?)",
            (body.title.strip(), body.domain, body.target_date, body.total_progress, body.current_progress),
        )
        gid = cur.lastrowid
        cur.execute("SELECT * FROM long_term_goals WHERE id=?", (gid,))
        row = dict(cur.fetchone())
    return GoalOut(**row)


@router.patch("/{goal_id}/progress", response_model=GoalOut)
def update_progress(goal_id: int, body: ProgressUpdate):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM long_term_goals WHERE id=?", (goal_id,))
        g = cur.fetchone()
        if not g:
            raise HTTPException(404, "目标不存在")
        cur.execute("UPDATE long_term_goals SET current_progress=? WHERE id=?", (body.current_progress, goal_id))
        cur.execute("SELECT * FROM long_term_goals WHERE id=?", (goal_id,))
        row = dict(cur.fetchone())
    return GoalOut(**row)
