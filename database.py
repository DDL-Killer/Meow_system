"""
Digital Dojo 2.0 — SQLite database layer.
Connection management, startup initialization, and dependency injection.
"""
import sqlite3
import os
from contextlib import contextmanager

# Docker 部署时通过 DOJO_DB_DIR 指定数据目录，本地开发默认项目根目录
_DB_DIR = os.getenv("DOJO_DB_DIR", os.path.dirname(os.path.abspath(__file__)))
os.makedirs(_DB_DIR, exist_ok=True)
DB_PATH = os.path.join(_DB_DIR, "dojo_private.db")

# ── Constants ───────────────────────────────────────────────────────────

REALM_THRESHOLDS = [
    (0,    "未入门"),
    (100,  "克己"),
    (300,  "慎独"),
    (600,  "主敬"),
    (1000, "存天理"),
]

DOMAINS = ["study", "health", "skill", "project", "social"]
DOMAIN_LABELS = {
    "study":   "修学",
    "health":  "养生",
    "skill":   "技艺",
    "project": "事功",
    "social":  "人伦",
}

SCORE_COMPLETE  = 10
SCORE_FAIL      = 15
SCORE_STREAK_7  = 30

# ── Connection helpers ─────────────────────────────────────────────────


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Startup initialisation ─────────────────────────────────────────────


def init_database() -> str:
    """Create / migrate all tables to 2.0 schema. Called once at startup."""
    conn = get_connection()
    cur = conn.cursor()

    # ── tasks (2.0: +domain) ─────────────────────────────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        title         TEXT    NOT NULL,
        type          TEXT    NOT NULL,   -- 'daily' | 'additional' | 'long_term' | 'goal_split'
        domain        TEXT    DEFAULT 'study',  -- v2.0: study|health|skill|project|social
        status        TEXT    DEFAULT 'pending',
        fail_reason   TEXT,
        goal_id       INTEGER,             -- v2.0: FK to long_term_goals (nullable)
        created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
        completed_at  DATETIME
    )
    """)
    # Migration: add domain column if upgrading from v1 schema
    _add_column_if_missing(cur, "tasks", "domain", "TEXT DEFAULT 'study'")
    _add_column_if_missing(cur, "tasks", "goal_id", "INTEGER")
    # v3.0: duration fields for time-axis tracking
    _add_column_if_missing(cur, "tasks", "duration_planned", "INTEGER")
    _add_column_if_missing(cur, "tasks", "duration_actual", "INTEGER")
    # v3.1: task scope & date range
    _add_column_if_missing(cur, "tasks", "task_scope", "TEXT DEFAULT 'short_term'")
    # short_term: today only | long_term: start_date→end_date | semi_permanent: start_date→∞
    _add_column_if_missing(cur, "tasks", "start_date", "DATE")
    _add_column_if_missing(cur, "tasks", "end_date", "DATE")

    # ── self_cultivation ─────────────────────────────────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS self_cultivation (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        score         INTEGER DEFAULT 0,
        streak_days   INTEGER DEFAULT 0,
        current_realm TEXT    DEFAULT '未入门',
        last_update   DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ── voice_logs ───────────────────────────────────────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS voice_logs (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        raw_text          TEXT,
        deepseek_analysis TEXT,
        emotion_state     TEXT,
        created_date      DATE DEFAULT CURRENT_DATE
    )
    """)

    # ── parsed_classics (v2.0: +source_work +domain_tags) ──────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS parsed_classics (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        source_pdf   TEXT,
        source_work  TEXT,            -- v2.0: e.g. '孟子', '荀子', '周易'
        page_num     INTEGER,
        content_text TEXT,
        domain_tags  TEXT,            -- v2.0: comma-separated, e.g. 'study,social'
        char_count   INTEGER DEFAULT 0
    )
    """)
    _add_column_if_missing(cur, "parsed_classics", "source_work", "TEXT")
    _add_column_if_missing(cur, "parsed_classics", "domain_tags", "TEXT")
    _add_column_if_missing(cur, "parsed_classics", "char_count", "INTEGER DEFAULT 0")
    # v3.0: structured three-field scholarly parsing
    _add_column_if_missing(cur, "parsed_classics", "original_text", "TEXT")
    _add_column_if_missing(cur, "parsed_classics", "academic_annotations", "TEXT")
    _add_column_if_missing(cur, "parsed_classics", "modern_translation", "TEXT")
    # Backfill: existing content_text → original_text for rows that lack it
    cur.execute("UPDATE parsed_classics SET original_text = content_text WHERE original_text IS NULL AND content_text IS NOT NULL")

    # ── sleep_tracker (v2.0 — 保留但废弃，数据迁移到 daily_chronicle) ──
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sleep_tracker (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        action_type  TEXT    NOT NULL,
        actual_time  DATETIME NOT NULL,
        is_late      BOOLEAN DEFAULT 0,
        created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ── daily_chronicle (v3.0 — 每日纪事，纯时间轴) ──────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS daily_chronicle (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        record_date DATE UNIQUE NOT NULL,
        wake_time   DATETIME,
        sleep_time  DATETIME,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    # One-time migration: backfill from sleep_tracker if daily_chronicle is empty
    cur.execute("SELECT COUNT(*) as cnt FROM daily_chronicle")
    if cur.fetchone()["cnt"] == 0:
        cur.execute("""
        INSERT OR IGNORE INTO daily_chronicle (record_date, wake_time, sleep_time)
        SELECT DATE(actual_time),
               MIN(CASE WHEN action_type='wake'  THEN actual_time END),
               MAX(CASE WHEN action_type='sleep' THEN actual_time END)
        FROM sleep_tracker
        GROUP BY DATE(actual_time)
        """)

    # ── task_daily_log (v3.2 — 半永久任务每日完成时间戳) ──────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS task_daily_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id      INTEGER NOT NULL,
        log_date     DATE    NOT NULL,
        completed_at DATETIME NOT NULL,
        UNIQUE(task_id, log_date),
        FOREIGN KEY(task_id) REFERENCES tasks(id)
    )
    """)

    # ── long_term_goals (v2.0) ───────────────────────────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS long_term_goals (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        title            TEXT    NOT NULL,
        domain           TEXT    DEFAULT 'study',
        target_date      DATE    NOT NULL,
        total_progress   INTEGER DEFAULT 100,
        current_progress INTEGER DEFAULT 0,
        is_active        BOOLEAN DEFAULT 1,
        created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Seeds
    cur.execute("""
    INSERT INTO self_cultivation (score, streak_days, current_realm)
    SELECT 0, 0, '未入门'
    WHERE NOT EXISTS (SELECT 1 FROM self_cultivation)
    """)

    conn.commit()
    conn.close()
    return "【2.0 核心构建成功】SQLite 数据库已升级至 v2.0 schema。"


def _add_column_if_missing(cur, table: str, column: str, col_def: str):
    """Add a column to an existing table if it doesn't already exist (migration helper)."""
    cur.execute(f"PRAGMA table_info({table})")
    existing = {r[1] for r in cur.fetchall()}
    if column not in existing:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")


# ── Realm helpers ──────────────────────────────────────────────────────


def compute_realm(score: int) -> str:
    realm = "未入门"
    for threshold, name in REALM_THRESHOLDS:
        if score >= threshold:
            realm = name
    return realm


# ── Goal back-calculation ──────────────────────────────────────────────


def compute_goal_daily_tasks(conn) -> list[dict]:
    """
    Read all active long_term_goals and compute daily-split tasks.

    v3.0 — 增强引擎:
      - daily_needed_floor: 今日底线进度 (向上取整)
      - urgency: "critical" (<3天) | "warning" (<7天) | "safe"
      - 逾期目标 days_left=1 紧急标记

    Returns a list of dicts ready to be merged into today's task list.
    """
    from datetime import date, datetime
    import math

    cur = conn.cursor()
    cur.execute("""
    SELECT * FROM long_term_goals
    WHERE is_active = 1 AND current_progress < total_progress
    ORDER BY target_date ASC
    """)
    goals = cur.fetchall()
    today = date.today()
    splits = []

    for g in goals:
        target = datetime.strptime(g["target_date"], "%Y-%m-%d").date()
        days_left = (target - today).days

        # Urgency tier
        if days_left <= 0:
            days_left = 1
            urgency = "critical"
        elif days_left <= 3:
            urgency = "critical"
        elif days_left <= 7:
            urgency = "warning"
        else:
            urgency = "safe"

        progress_gap = g["total_progress"] - g["current_progress"]
        daily_needed = progress_gap / max(days_left, 1)
        daily_needed_floor = math.ceil(daily_needed)

        # Only generate a split if there's meaningful daily work
        if daily_needed <= 0:
            continue

        # Check if today already has a split task for this goal (robust dedup)
        today_str = today.isoformat()
        cur.execute("""
        SELECT COUNT(*) as cnt FROM tasks
        WHERE goal_id = ? AND type = 'goal_split'
          AND (DATE(created_at) = ? OR start_date = ?)
        """, (g["id"], today_str, today_str))
        existing = cur.fetchone()["cnt"]
        if existing > 0:
            continue

        unit = "页" if g["domain"] == "study" else "单元" if g["domain"] == "skill" else "%"
        urgency_marker = {"critical": "⚡", "warning": "🔶", "safe": ""}[urgency]
        title = (
            f'{urgency_marker}「{g["title"]}」底线 {daily_needed_floor}{unit}/日 '
            f'(剩余{days_left}天 · 还需{progress_gap}{unit})'
        )
        splits.append({
            "title": title,
            "domain": g["domain"],
            "goal_id": g["id"],
            "daily_needed": round(daily_needed, 1),
            "daily_needed_floor": daily_needed_floor,
            "days_remaining": days_left,
            "urgency": urgency,
            "type": "goal_split",
        })

    return splits
