"""
Self-cultivation router — 功过格积分 & 境界查询.

GET /cultivation — current score, streak, realm (with visual progress).
"""
from fastapi import APIRouter
from database import get_db, REALM_THRESHOLDS

router = APIRouter(prefix="/cultivation", tags=["cultivation"])


@router.get("", response_model=dict)
def get_cultivation():
    """
    Return the current cultivation snapshot:
      - score, streak_days, current_realm
      - realm_progress: how close to the next realm (0.0 – 1.0)
      - realm_index: ordinal position among the 5 realms
      - all_realms: ordered list with threshold info
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM self_cultivation WHERE id = 1")
        row = cur.fetchone()

    if not row:
        return {
            "score": 0,
            "streak_days": 0,
            "current_realm": "未入门",
            "realm_index": 0,
            "realm_progress": 0.0,
            "all_realms": [{"name": name, "threshold": thr} for thr, name in REALM_THRESHOLDS],
        }

    row = dict(row)
    score = row["score"]
    current_realm = row["current_realm"]

    realm_index = 0
    next_threshold = None
    current_threshold = 0

    for i, (thr, name) in enumerate(REALM_THRESHOLDS):
        if name == current_realm:
            realm_index = i
            current_threshold = thr
            if i + 1 < len(REALM_THRESHOLDS):
                next_threshold = REALM_THRESHOLDS[i + 1][0]
            break

    if next_threshold is not None:
        progress_range = next_threshold - current_threshold
        progress = min(1.0, max(0.0, (score - current_threshold) / progress_range))
    else:
        progress = 1.0

    return {
        "score": score,
        "streak_days": row["streak_days"],
        "current_realm": current_realm,
        "realm_index": realm_index,
        "realm_progress": round(progress, 3),
        "next_realm": REALM_THRESHOLDS[realm_index + 1][1] if realm_index + 1 < len(REALM_THRESHOLDS) else None,
        "next_threshold": next_threshold,
        "all_realms": [{"name": name, "threshold": thr} for thr, name in REALM_THRESHOLDS],
    }
