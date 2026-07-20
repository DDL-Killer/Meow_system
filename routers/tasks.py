"""
Daily planning & check-in router.

POST   /tasks              — create one or more tasks
GET    /tasks              — list / filter tasks
PATCH  /tasks/{id}/complete — mark a task as completed (+score, +streak)
PATCH  /tasks/{id}/fail    — mark a task as failed  (–score, streak→0, realm→未入门)

CRITICAL: when status is 'failed', `fail_reason` is MANDATORY.
The endpoint rejects the request with 422 if it is missing or empty.
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import date, timedelta, datetime, timezone

from database import (
    get_db,
    compute_realm,
    # compute_goal_daily_tasks,  # v1.1: 长期目标已隐藏
    DOMAINS,
    SCORE_COMPLETE,
    SCORE_FAIL,
    SCORE_STREAK_7,
    now_cst_iso,
    dojo_today_str,
    dojo_today,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


# ── Schemas ────────────────────────────────────────────────────────────


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=256, description="Task title")
    type: str = Field("daily", pattern=r"^(daily|additional|long_term|goal_split)$")
    domain: str = Field("study", pattern=r"^(study|health|skill|project|social)$")
    duration_planned: int | None = Field(None, ge=1, le=1440, description="预估耗时(分钟)")
    task_scope: str = Field("short_term", pattern=r"^(short_term|long_term|semi_permanent)$")
    start_date: str | None = Field(None, description="开始日期 YYYY-MM-DD")
    end_date: str | None = Field(None, description="结束日期 YYYY-MM-DD (长期任务必填)")


class TaskBatchCreate(BaseModel):
    tasks: List[TaskCreate] = Field(..., min_length=1, max_length=20)


class TaskOut(BaseModel):
    id: int
    title: str
    type: str
    domain: str = "study"
    status: str
    fail_reason: Optional[str] = None
    goal_id: Optional[int] = None
    duration_planned: Optional[int] = None
    duration_actual: Optional[int] = None
    task_scope: str = "short_term"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    missed_yesterday: bool = False  # semi_permanent tasks not done yesterday


class FailRequest(BaseModel):
    fail_reason: str = Field(..., min_length=1, max_length=1024,
                             description="REQUIRED when marking a task as failed — "
                                         "冷峻自省，必须写明失败原因")

    @field_validator("fail_reason")
    @classmethod
    def not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("fail_reason 不得为空字符串 — 修身者必须直面懈怠之因")
        return stripped


# ── Cultivation helper (in-router to avoid circular imports) ────────────


def _apply_cultivation(conn, delta: int, reset_streak: bool):
    """
    Update the singleton self_cultivation row.
      delta         — score change (positive for complete, negative for fail)
      reset_streak  — if True, streak → 0 and realm → 未入门
    """
    cur = conn.cursor()
    cur.execute("SELECT * FROM self_cultivation WHERE id = 1")
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO self_cultivation (score, streak_days, current_realm) VALUES (0,0,'未入门')")
        conn.commit()
        cur.execute("SELECT * FROM self_cultivation WHERE id = 1")
        row = cur.fetchone()

    new_score = row["score"] + delta
    if new_score < 0:
        new_score = 0

    if reset_streak:
        new_streak = 0
        new_realm = "未入门"
    else:
        new_streak = row["streak_days"] + 1
        # Streak milestone bonus (every 7 days)
        if new_streak > 0 and new_streak % 7 == 0:
            new_score += SCORE_STREAK_7
        new_realm = compute_realm(new_score)

    cur.execute("""
    UPDATE self_cultivation
    SET score = ?, streak_days = ?, current_realm = ?, last_update = ?
    WHERE id = 1
    """, (new_score, new_streak, new_realm, now_cst_iso()))
    conn.commit()


# ── Routes ─────────────────────────────────────────────────────────────


@router.post("", response_model=List[TaskOut], status_code=201)
def create_tasks(batch: TaskBatchCreate):
    """Create one or more tasks for the day (or long-term backlog)."""
    with get_db() as conn:
        cur = conn.cursor()
        created = []
        for t in batch.tasks:
            cur.execute(
                "INSERT INTO tasks (title, type, domain, duration_planned, task_scope, start_date, end_date, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (t.title.strip(), t.type, t.domain, t.duration_planned, t.task_scope, t.start_date, t.end_date, now_cst_iso()),
            )
            tid = cur.lastrowid
            created.append(
                TaskOut(
                    id=tid, title=t.title.strip(), type=t.type, domain=t.domain,
                    status="pending", duration_planned=t.duration_planned,
                    task_scope=t.task_scope, start_date=t.start_date, end_date=t.end_date,
                )
            )
        return created


@router.get("/today", response_model=List[TaskOut])
def list_today_tasks():
    """
    Return tasks visible TODAY:
      - semi_permanent: always (start_date <= today, no end or end >= today)
      - long_term: today in [start_date, end_date]
      - short_term: created today (or start_date = today)
      - goal_split: auto-generated from active goals

    Uses 5AM cutoff: "today" = dojo date, not calendar date.
    """
    today_str = dojo_today_str()
    # For short_term matching: accept both dojo_today and calendar_today
    # (handles CST-stored new tasks + UTC-stored legacy tasks)
    cal_today = date.today().isoformat()

    with get_db() as conn:
        cur = conn.cursor()

        # ── Auto-fail: yesterday's uncompleted short_term tasks ────
        cur.execute("""
            UPDATE tasks SET status = 'failed',
                   fail_reason = '昨日未完成，自动标记失败',
                   completed_at = ?
            WHERE status = 'pending'
              AND task_scope = 'short_term'
              AND DATE(created_at) < ?
              AND type != 'goal_split'
        """, (now_cst_iso(), today_str))

        # v3.2: LEFT JOIN task_daily_log — semi_permanent tasks with a log
        # entry for today are excluded (they're already done today)
        cur.execute("""
        SELECT t.* FROM tasks t
        LEFT JOIN task_daily_log dl ON t.id = dl.task_id AND dl.log_date = ?
        WHERE t.status = 'pending' AND (
          (t.task_scope = 'semi_permanent' AND dl.id IS NULL)
          OR (t.task_scope = 'long_term' AND t.start_date <= ? AND t.end_date >= ?)
          OR (t.task_scope = 'short_term' AND (DATE(t.created_at) = ? OR DATE(t.created_at) = ?))
          OR (t.task_scope = 'short_term' AND (t.start_date = ? OR t.start_date = ?))
          OR (t.type = 'goal_split' AND (DATE(t.created_at) = ? OR DATE(t.created_at) = ?))
        )
        ORDER BY t.created_at DESC
        """, (today_str,
              today_str, today_str,
              today_str, cal_today,
              today_str, cal_today,
              today_str, cal_today))
        rows = cur.fetchall()

    tasks = [TaskOut(**dict(r)) for r in rows]

    # v3.3: detect semi_permanent tasks missed yesterday
    semi_ids = [t.id for t in tasks if t.task_scope == "semi_permanent"]
    if semi_ids:
        yesterday = (dojo_today() - timedelta(days=1)).isoformat()
        with get_db() as conn:
            cur = conn.cursor()
            placeholders = ",".join("?" for _ in semi_ids)
            cur.execute(
                f"SELECT task_id FROM task_daily_log WHERE task_id IN ({placeholders}) AND log_date = ?",
                semi_ids + [yesterday],
            )
            done_yesterday = {r[0] for r in cur.fetchall()}
        for t in tasks:
            if t.task_scope == "semi_permanent" and t.id not in done_yesterday:
                t.missed_yesterday = True

    return tasks


@router.get("", response_model=List[TaskOut])
def list_tasks(
    type: Optional[str] = Query(None, pattern=r"^(daily|additional|long_term|goal_split)$"),
    status: Optional[str] = Query(None, pattern=r"^(pending|completed|failed)$"),
    domain: Optional[str] = Query(None, pattern=r"^(study|health|skill|project|social)$"),
    date: Optional[str] = Query(None, description="Filter by created_at date (YYYY-MM-DD)"),
):
    """List tasks with optional filters. Auto-generates goal-split tasks from long_term_goals."""
    query = "SELECT * FROM tasks WHERE 1=1"
    params: list = []

    if type:
        query += " AND type = ?"
        params.append(type)
    if status:
        query += " AND status = ?"
        params.append(status)
    if domain:
        query += " AND domain = ?"
        params.append(domain)
    if date:
        query += " AND DATE(created_at) = ?"
        params.append(date)

    query += " ORDER BY created_at DESC"

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(query, params)
        rows = cur.fetchall()

    tasks = [TaskOut(**dict(r)) for r in rows]

    # v1.1: 长期目标功能已隐藏
    # if date is not None and (type is None or type == "goal_split"):
    #     with get_db() as conn:
    #         splits = compute_goal_daily_tasks(conn)
    #     ...

    return tasks


@router.patch("/{task_id}/complete", response_model=dict)
def complete_task(task_id: int):
    """
    Mark a task as completed.
    Effects: score +10, streak_days +1, realm recalculated.

    v3.2: semi_permanent tasks write a daily log entry instead of
    changing status — they reappear tomorrow.
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        task = cur.fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")

        today_str = dojo_today_str()
        now = now_cst_iso()

        # ── Semi-permanent: daily log instead of status change ──────
        if task["task_scope"] == "semi_permanent":
            cur.execute(
                "SELECT id FROM task_daily_log WHERE task_id = ? AND log_date = ?",
                (task_id, today_str),
            )
            if cur.fetchone():
                raise HTTPException(409, "今日已打卡此半永久任务")

            cur.execute(
                "INSERT INTO task_daily_log (task_id, log_date, completed_at) VALUES (?, ?, ?)",
                (task_id, today_str, now),
            )

            # 功过格：完成 + 积分 (same as regular task)
            _apply_cultivation(conn, delta=SCORE_COMPLETE, reset_streak=False)

            cur.execute("SELECT * FROM self_cultivation WHERE id = 1")
            cult = dict(cur.fetchone())

            return {
                "message": "✅ 打卡成功 — 日日精进，功夫不欺",
                "task_id": task_id,
                "task_scope": "semi_permanent",
                "log_date": today_str,
                "score": cult["score"],
                "streak_days": cult["streak_days"],
                "current_realm": cult["current_realm"],
            }

        # ── Regular task: existing logic ────────────────────────────
        if task["status"] == "completed":
            raise HTTPException(409, "该任务已经完成，无需重复打卡")

        cur.execute(
            "UPDATE tasks SET status = 'completed', completed_at = ? WHERE id = ?",
            (now, task_id),
        )

        # 功过格：完成 + 积分
        _apply_cultivation(conn, delta=SCORE_COMPLETE, reset_streak=False)

        # v2.0: if this is a goal_split task, advance goal progress
        if task["goal_id"]:
            cur.execute("""
            UPDATE long_term_goals
            SET current_progress = current_progress + 1
            WHERE id = ? AND current_progress < total_progress
            """, (task["goal_id"],))

        # Return updated cultivation snapshot
        cur.execute("SELECT * FROM self_cultivation WHERE id = 1")
        cult = dict(cur.fetchone())

    return {
        "message": "✅ 打卡成功 — 精进一分，功夫不欺",
        "task_id": task_id,
        "score": cult["score"],
        "streak_days": cult["streak_days"],
        "current_realm": cult["current_realm"],
    }


@router.patch("/{task_id}/fail", response_model=dict)
def fail_task(task_id: int, body: FailRequest):
    """
    Mark a task as failed — **must** include a non-empty fail_reason.

    Effects:
      score –15, streak → 0, realm → 未入门 (境界立刻清零).

    v3.2: semi_permanent tasks cannot be failed — baseline routines
    carry over to the next day if not done.
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        task = cur.fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")

        if task["task_scope"] == "semi_permanent":
            raise HTTPException(400, "半永久任务不支持失败记录 — 日常功夫不可懈怠，明日继续")

        if task["status"] == "failed":
            raise HTTPException(409, "该任务已标记为失败")

        now = now_cst_iso()
        cur.execute(
            "UPDATE tasks SET status = 'failed', fail_reason = ?, completed_at = ? WHERE id = ?",
            (body.fail_reason.strip(), now, task_id),
        )

        # 功过格：失败 → 扣分 + 断更 + 境界清零
        _apply_cultivation(conn, delta=-SCORE_FAIL, reset_streak=True)

        cur.execute("SELECT * FROM self_cultivation WHERE id = 1")
        cult = dict(cur.fetchone())

    return {
        "message": "💀 断更 — 境界清零，回归未入门。知耻后勇，明日再战。",
        "task_id": task_id,
        "fail_reason": body.fail_reason.strip(),
        "score": cult["score"],
        "streak_days": 0,
        "current_realm": "未入门",
    }


@router.patch("/{task_id}/undo", response_model=dict)
def undo_task(task_id: int):
    """管理者：撤销已完成/已失败的任务，回到待办状态。扣除之前加的分数。"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        task = cur.fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")
        if task["status"] == "pending":
            raise HTTPException(400, "任务已经是待办状态")

        old_status = task["status"]
        cur.execute("UPDATE tasks SET status = 'pending', completed_at = NULL, fail_reason = NULL WHERE id = ?", (task_id,))

        # Reverse cultivation: if was completed, subtract score; if failed, add back
        delta = -SCORE_COMPLETE if old_status == "completed" else SCORE_FAIL
        if delta != 0:
            cur.execute("SELECT * FROM self_cultivation WHERE id = 1")
            cult = cur.fetchone()
            if cult:
                new_score = max(0, cult["score"] + delta)
                cur.execute("UPDATE self_cultivation SET score = ? WHERE id = 1", (new_score,))

        cur.execute("SELECT * FROM self_cultivation WHERE id = 1")
        cult = dict(cur.fetchone())
    return {"message": "已撤销", "task_id": task_id, "score": cult["score"], "current_realm": cult["current_realm"]}


@router.delete("/{task_id}", response_model=dict)
def delete_task(task_id: int):
    """Soft-delete a task — 保留数据，仅标记 status='deleted' 不再显示。"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        task = cur.fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")

        cur.execute("UPDATE tasks SET status = 'deleted' WHERE id = ?", (task_id,))
        return {"message": "🗑️ 已归档", "task_id": task_id}
