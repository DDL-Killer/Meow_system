"""
Analytics 2.0 — 内省看板数据

GET /analytics  —  7-day completion rate | score trend | emotion distribution
                   | sleep timestamps | per-domain breakdown | fail_reasons
"""
from fastapi import APIRouter, Query
from database import get_db, SCORE_COMPLETE, SCORE_FAIL, DOMAINS, DOMAIN_LABELS

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("", response_model=dict)
def get_analytics(days: int = Query(7, ge=1, le=365, description="统计天数范围")):
    with get_db() as conn:
        cur = conn.cursor()

        # ── 1. Daily task stats ─────────────────────────────────────
        cur.execute(f"""
        SELECT
          DATE(created_at) AS day,
          COUNT(*) AS total,
          SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
          SUM(CASE WHEN status = 'failed'    THEN 1 ELSE 0 END) AS failed
        FROM tasks
        WHERE DATE(created_at) >= DATE('now', '-{days} days')
        GROUP BY DATE(created_at)
        ORDER BY day
        """)
        daily_stats = [
            {
                "day": r["day"], "total": r["total"],
                "completed": r["completed"], "failed": r["failed"],
                "rate": round(r["completed"] / max(r["total"], 1), 3),
            }
            for r in cur.fetchall()
        ]

        # ── 2. Score trend — cumulative from task activity ────────
        cur.execute(f"""
        SELECT
          DATE(COALESCE(completed_at, created_at)) AS day,
          SUM(CASE WHEN status = 'completed' THEN ? WHEN status = 'failed' THEN -? ELSE 0 END) AS daily_net
        FROM tasks WHERE status IN ('completed','failed')
          AND DATE(COALESCE(completed_at, created_at)) >= DATE('now', '-{days} days')
        GROUP BY DATE(COALESCE(completed_at, created_at))
        ORDER BY day
        """, (SCORE_COMPLETE, SCORE_FAIL))
        cumulative = 0
        score_trend = []
        for r in cur.fetchall():
            cumulative += r["daily_net"]
            score_trend.append({"day": r["day"], "daily_net": r["daily_net"], "cumulative": cumulative})

        cur.execute("SELECT score FROM self_cultivation WHERE id = 1")
        cult = cur.fetchone()
        current_score = cult["score"] if cult else 0

        # ── 3. Emotion distribution ──────────────────────────────
        cur.execute("""
        SELECT COALESCE(emotion_state,'未标记') AS emotion, COUNT(*) AS cnt
        FROM voice_logs GROUP BY emotion_state ORDER BY cnt DESC
        """)
        emotion_dist = [{"emotion": r["emotion"], "count": r["cnt"]} for r in cur.fetchall()]

        # ── 3.5. Completion trend — tasks resolved per day by completed_at ──
        cur.execute(f"""
        SELECT
          DATE(completed_at) AS day,
          SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
          SUM(CASE WHEN status = 'failed'    THEN 1 ELSE 0 END) AS failed
        FROM tasks
        WHERE status IN ('completed','failed')
          AND completed_at IS NOT NULL
          AND DATE(completed_at) >= DATE('now', '-{days} days')
        GROUP BY DATE(completed_at)
        ORDER BY day
        """)
        completion_trend = [
            {"day": r["day"], "completed": r["completed"], "failed": r["failed"]}
            for r in cur.fetchall()
        ]

        # ── 4. Sleep/wake timestamps (v3.0 — from daily_chronicle) ──
        cur.execute(f"""
        SELECT record_date as day, wake_time, sleep_time
        FROM daily_chronicle
        WHERE record_date >= DATE('now', '-{days} days')
        ORDER BY record_date ASC
        """)
        sleep_trend = [dict(r) for r in cur.fetchall()]

        # ── 5. Per-domain completion breakdown (v2.0) ─────────────
        cur.execute("""
        SELECT
          domain,
          COUNT(*) AS total,
          SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
          SUM(CASE WHEN status='failed'    THEN 1 ELSE 0 END) AS failed
        FROM tasks
        WHERE domain IS NOT NULL
        GROUP BY domain
        ORDER BY domain
        """)
        domain_breakdown = [
            {
                "domain": r["domain"],
                "label": DOMAIN_LABELS.get(r["domain"], r["domain"]),
                "total": r["total"],
                "completed": r["completed"],
                "failed": r["failed"],
                "rate": round(r["completed"] / max(r["total"], 1), 3),
            }
            for r in cur.fetchall()
        ]

        # ── 6. Fail reasons ──────────────────────────────────────
        cur.execute("""
        SELECT id, title, fail_reason, completed_at AS failed_at
        FROM tasks WHERE status='failed' AND fail_reason IS NOT NULL AND fail_reason != ''
        ORDER BY completed_at DESC LIMIT 50
        """)
        fail_reasons = [
            {"id": r["id"], "title": r["title"], "fail_reason": r["fail_reason"], "failed_at": r["failed_at"]}
            for r in cur.fetchall()
        ]

    return {
        "current_score": current_score,
        "daily_stats": daily_stats,
        "score_trend": score_trend,
        "completion_trend": completion_trend,
        "emotion_distribution": emotion_dist,
        "sleep_trend": sleep_trend,
        "domain_breakdown": domain_breakdown,
        "fail_reasons": fail_reasons,
    }
