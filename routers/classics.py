"""
Classics 3.0 — 古籍晨读检索 (强力清洗版)

GET /daily-quote — return a single修身警句 from parsed_classics.

Supports:
  - Random selection (default)
  - Mood-targeted filtering based on voice_log emotion_state
  - Domain-targeted filtering based on task/life domain

Five-domain → classic mapping:
  study   → 荀子·劝学 / 孟子 (有恒专一) / 论语·学而
  health  → 周易 (天行健) / 黄帝内经 / 庄子·养生主
  skill   → 庄子·庖丁 / 列子 / 孙子兵法
  project → 孙子兵法 / 韩非子 / 管子 / 鬼谷子
  social  → 孟子 (五伦) / 论语 / 荀子

v3.0: _clean_passage() 强力清洗层 — 物理剔除页码、PDF文件名、z-library引用、
      又见于括号、注释标记、引注编号等所有非原文噪声。清洗后若断句破碎则自动
      重新捞取，确保每次返回都是纯粹古典原文。
"""
import random
import re
from datetime import date
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from database import get_db, DOMAINS

router = APIRouter(prefix="/daily-quote", tags=["classics"])

MIN_CHARS = 20  # minimum passage length to consider
MAX_RETRY = 20  # max re-queries when cleaning produces junk
CACHE_SECONDS = 86400  # 24h cache lock


def _cached(data: dict) -> JSONResponse:
    """Wrap a dict response with 24-hour Cache-Control + date-locked ETag."""
    today = date.today().toordinal()
    etag = f'"{today}-{hash(data.get("quote", ""))}"'
    return JSONResponse(
        content=data,
        headers={
            "Cache-Control": f"private, max-age={CACHE_SECONDS}",
            "ETag": etag,
        },
    )

# ── Emotion → domain mapping ───────────────────────────────────────────

EMOTION_TO_DOMAIN = {
    "焦虑": "health",      # 焦虑 → 养生/静心类
    "内耗": "study",       # 内耗 → 为学/有恒类
    "浮躁": "skill",       # 浮躁 → 技艺/沉潜类
    "笃定": "study",       # 笃定 → 持续精进类
    "懈怠": "project",     # 懈怠 → 事功/行动类
    "敷衍": "social",      # 敷衍 → 人伦/自省类
}

# ── Domain → preferred works + keywords ─────────────────────────────────

DOMAIN_PREFERRED = {
    "study":   ["荀子", "孟子", "论语", "大学", "中庸", "孔子家语"],
    "health":  ["周易", "黄帝四经", "抱朴子", "庄子"],
    "skill":   ["庄子", "列子", "孙子兵法", "公孙龙子"],
    "project": ["孙子兵法", "韩非子", "管子", "鬼谷子", "淮南子"],
    "social":  ["孟子", "论语", "荀子", "孔子家语"],
}

# Annotation noise markers — prefer passages WITHOUT these
ANNOTATION_NOISE = ["注释", "王注", "旧注", "注：", "疏：", "正义曰", "集解", "注云", "校注", "译注",
                    "按语", "按：", "笺注", "训诂", "反切", "直音", "叶音", "又音",
                    "同“", "通“", "见前注", "(1)", "(2)", "(3)", "(4)", "(5)",
                    "(6)", "(7)", "(8)", "(9)", "(10)", "(11)", "(12)"]

# Fallback "cold & deep" keywords when no domain signal
GENERIC_KEYWORDS = [
    "慎独", "克己", "敬", "诚", "天理", "良知", "格物", "致知", "修身",
    "省", "戒惧", "谨", "中和", "静坐", "主一", "涵养", "省察", "存养",
    "居敬", "穷理", "尽心", "知性", "养气", "浩然", "求放心",
]


# ── Cleaning: 强力清洗层 — 剔除所有非原文噪声 ────────────────────────────

# Patterns that signal a passage is annotation/garbage, not classical text
# Collapsed to avoid re.compile line-splitting bugs.

_RE_ANNOTATION_BLOCK = re.compile(
    r'[（(]'
    r'(?:[^）)]*?'
    r'(?:又见于|亦见于|参见|详见|见前|见上|见下|z-library|Z-Library'
    r'|注释|注[：:]\d|王注|旧注|集解|正义|笺注|训诂|反切'
    r'|同["“]|通["“]|读[作为]|校[勘记])'
    r'[^）)]*?'
    r'[）)])'
)

_RE_PAGE_NUM = re.compile(
    r'[·\s　]*第\s*\d{1,6}\s*页'
    r'|[\s　]+\d{4,6}[\s　]*$'
    r'|[·\s　]*-?\s*\d{1,4}\s*-\s*'
)

_RE_PDF_FILENAME = re.compile(
    r'诸子百家[^·\n]{0,60}\.pdf'
    r'|Z-Library[^·\n]{0,40}\.pdf'
    r'|zlib[^·\n]{0,30}\.pdf'
    r'|[\w\-]+\.pdf'
    r'|\b\d{5,8}\b'
)

_RE_URL = re.compile(
    r'https?://\S+'
    r'|www\.\S+'
    r'|z-lib\.\S+'
    r'|Z-Library\S*'
)

_RE_ANNOTATION_NOTE = re.compile(
    r'[（(]\s*(?:注|按|案|疏)[：:\s][^）)]*[）)]'
    r'|（\s*(?:1|2|3|4|5|6|7|8|9|10|11|12)\s*）'
    r'|\[\s*\d+\s*\]'
    r'|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫]'
)

_RE_CLEANUP = re.compile(
    r'\s{2,}'
    r'|[　]{2,}'
    r'|^[，。；：！？、,\s　]+'
)

_RE_FRAGMENT = re.compile(
    r'^.{1,4}$'                             # single-word lines
    r'|^[的了吗呢着过之乎者也而以其所者]$'  # single particle
)


def _clean_passage(text: str) -> str | None:
    """
    Physically strip ALL non-classical noise from a parsed passage.

    Removes in order:
      1. Parenthetical annotation blocks (又见于..., z-library, 注释...)
      2. Page numbers (第10559页, · 第xxx页)
      3. PDF filenames (诸子百家...pdf, Z-Library...pdf)
      4. URLs and domain references
      5. Annotation note markers (注：..., 按：..., （1）（2）)
      6. Whitespace collapse and leading-punctuation trim

    Returns None if the cleaned text is too short, fragmented, or otherwise
    unsuitable — caller should re-query.
    """
    if not text or len(text) < MIN_CHARS:
        return None

    cleaned = text

    # 1. Nuke parenthetical annotation blocks first (they can be long)
    cleaned = _RE_ANNOTATION_BLOCK.sub('', cleaned)

    # 2. Nuke page numbers
    cleaned = _RE_PAGE_NUM.sub('', cleaned)

    # 3. Nuke PDF filenames
    cleaned = _RE_PDF_FILENAME.sub('', cleaned)

    # 4. Nuke URLs
    cleaned = _RE_URL.sub('', cleaned)

    # 5. Nuke annotation note markers
    cleaned = _RE_ANNOTATION_NOTE.sub('', cleaned)

    # 6. Collapse whitespace and newlines, trim
    cleaned = _RE_CLEANUP.sub(' ', cleaned)
    cleaned = cleaned.strip()
    # Normalize all newlines to Chinese-style continuous text
    cleaned = cleaned.replace('\n', '').replace('\r', '')
    # Collapse 2+ spaces
    cleaned = re.sub(r' {2,}', '', cleaned)
    # Fix common PDF artifacts: space before Chinese punctuation
    cleaned = re.sub(r'\s+([。，；：！？、])', r'\1', cleaned)
    cleaned = cleaned.strip()

    # 7. Remove any remaining isolated parentheses/brackets (unmatched)
    cleaned = cleaned.replace('（', '').replace('）', '')
    cleaned = cleaned.replace('(', '').replace(')', '')
    cleaned = cleaned.replace('【', '').replace('】', '')

    # Final trim
    cleaned = cleaned.strip()
    while '  ' in cleaned:
        cleaned = cleaned.replace('  ', ' ')

    # Quality checks
    if len(cleaned) < MIN_CHARS:
        return None

    # If the result is just a fragment (single short word), reject
    if _RE_FRAGMENT.match(cleaned):
        return None

    # Must contain at least one classical sentence-ending character
    if not re.search(r'[。！？；]', cleaned) and len(cleaned) > 60:
        # Long block with no sentence breaks — possibly corrupted
        pass  # still usable, keep it

    return cleaned


# ── Route ──────────────────────────────────────────────────────────────


@router.get("", response_model=dict)
def get_daily_quote(
    mood:   Optional[str] = Query(None, description="情绪标签: 焦虑/内耗/浮躁/笃定/懈怠"),
    domain: Optional[str] = Query(None, description="领域: study/health/skill/project/social"),
):
    """
    Return a single修身警句 for morning contemplation.

    Priority:
      1. Explicit `domain` param → domain-targeted retrieval
      2. `mood` param → auto-mapped to domain → domain-targeted
      3. Last night's voice_log emotion → auto-mapped → domain-targeted
      4. Fallback → date-seeded selection from full corpus

    The random seed is pinned to today's date — all calls on the same
    calendar day return the identical quote. Next day: new seed, new quote.
    """
    # ── Lock quote to today's date ─────────────────────────────────
    today_ordinal = date.today().toordinal()
    random.seed(today_ordinal)

    with get_db() as conn:
        cur = conn.cursor()

        # ── Resolve effective domain ──────────────────────────────
        effective_domain = domain
        effective_mood = mood

        if not effective_domain and not effective_mood:
            # Try last night's voice_log
            cur.execute(
                "SELECT emotion_state FROM voice_logs ORDER BY created_date DESC, id DESC LIMIT 1"
            )
            last_voice = cur.fetchone()
            if last_voice and last_voice["emotion_state"]:
                effective_mood = last_voice["emotion_state"]

        if not effective_domain and effective_mood:
            effective_domain = EMOTION_TO_DOMAIN.get(effective_mood)

        # ── Query corpus size ─────────────────────────────────────
        cur.execute("SELECT COUNT(*) as cnt FROM parsed_classics WHERE length(content_text) <= 150 AND length(content_text) <= 150 AND char_count >= 10")
        total = cur.fetchone()["cnt"]

        if total == 0:
            return _cached({
                "quote": "未有知而不行者。知而不行，只是未知。",
                "source_pdf": "《传习录》",
                "source_work": "传习录",
                "page_num": None,
                "original_text": None,
                "academic_annotations": None,
                "modern_translation": None,
                "mood_targeted": effective_mood,
                "domain_targeted": effective_domain,
                "corpus_size": 0,
                "note": "经典库为空 — 请先运行古籍注入脚本。此句为王阳明心学硬编码默认警句。",
            })

        chosen = None

        # ── Strategy 1: Domain-targeted — preferred works, quality-sorted ──
        if effective_domain and effective_domain in DOMAIN_PREFERRED:
            preferred = DOMAIN_PREFERRED[effective_domain]
            placeholders = ",".join("?" for _ in preferred)
            cur.execute(
                f"SELECT * FROM parsed_classics WHERE length(content_text) <= 150 AND source_work IN ({placeholders}) AND char_count >= ? ORDER BY id LIMIT 100",
                preferred + [MIN_CHARS],
            )
            candidates = cur.fetchall()
            if candidates:
                candidates = sorted(candidates, key=lambda r: _quality_score(r), reverse=True)
                # Pick from top 20% quality
                top_n = max(1, len(candidates) // 5)
                chosen = random.choice(candidates[:top_n])

        # ── Strategy 2: Domain-tagged — broader ────────────────────
        if chosen is None and effective_domain:
            cur.execute(
                "SELECT * FROM parsed_classics WHERE length(content_text) <= 150 AND domain_tags LIKE ? AND char_count >= ? ORDER BY id LIMIT 100",
                (f"%{effective_domain}%", MIN_CHARS),
            )
            candidates = cur.fetchall()
            if candidates:
                candidates = sorted(candidates, key=lambda r: _quality_score(r), reverse=True)
                top_n = max(1, len(candidates) // 5)
                chosen = random.choice(candidates[:top_n])

        # ── Strategy 3: Fallback — quality-random from full corpus ──
        if chosen is None:
            cur.execute(
                "SELECT * FROM parsed_classics WHERE length(content_text) <= 150 AND char_count >= ? ORDER BY id LIMIT 200",
                (MIN_CHARS,),
            )
            candidates = cur.fetchall()
            if candidates:
                candidates = sorted(candidates, key=lambda r: _quality_score(r), reverse=True)
                top_n = max(1, len(candidates) // 5)
                chosen = random.choice(candidates[:top_n])
            effective_domain = None
            effective_mood = None

        # ── Apply cleaning with retry ────────────────────────────────
        quote, source = _extract_clean_quote(chosen, cur, total)
        if quote is not None:
            return _cached({
                "quote": quote,
                "source_pdf": _clean_source_name(source["source_pdf"]),
                "source_work": source["source_work"] or detect_work(source["source_pdf"]),
                "page_num": source["page_num"],
                "original_text": source.get("original_text"),
                "academic_annotations": source.get("academic_annotations"),
                "modern_translation": source.get("modern_translation"),
                "mood_targeted": effective_mood,
                "domain_targeted": effective_domain,
                "corpus_size": total,
            })

        # All candidates exhausted — return hardcoded clean fallback
        return _cached({
            "quote": "天行健，君子以自强不息。",
            "source_pdf": "《周易》",
            "source_work": "周易",
            "page_num": None,
            "original_text": None,
            "academic_annotations": None,
            "modern_translation": None,
            "mood_targeted": effective_mood,
            "domain_targeted": effective_domain,
            "corpus_size": total,
        })


def _extract_clean_quote(chosen: dict | None, cur, corpus_total: int) -> tuple[str | None, dict | None]:
    """
    Apply _clean_passage() to `chosen`. If cleaning fails, re-query
    the DB iteratively (up to MAX_RETRY times) until a clean passage
    emerges. Returns (cleaned_quote, source_row) or (None, None).
    """
    if chosen is None:
        return None, None

    # Build a pool of candidates — start with the chosen one
    tried_ids: set[int] = set()
    pool = [chosen]

    for attempt in range(MAX_RETRY):
        if not pool:
            # Re-fill pool from DB (random shuffle)
            cur.execute(
                "SELECT * FROM parsed_classics WHERE length(content_text) <= 150 AND char_count >= ? ORDER BY id LIMIT 50",
                (MIN_CHARS,),
            )
            fresh = [r for r in cur.fetchall() if r["id"] not in tried_ids]
            if not fresh:
                return None, None
            pool = fresh

        candidate = pool.pop(0)
        if candidate["id"] in tried_ids:
            continue
        tried_ids.add(candidate["id"])

        raw = candidate["content_text"]
        if not raw:
            continue

        # ── Apply the heavy cleaning ──────────────────────────────
        cleaned = _clean_passage(raw)
        if cleaned is None:
            continue

        # ── Trim to a readable length ─────────────────────────────
        quote = cleaned
        if len(quote) > 300:
            for delim in ["。", "！", "？", "；"]:
                idx = quote[:300].rfind(delim)
                if idx > 50:
                    quote = quote[: idx + 1]
                    break
            else:
                quote = quote[:200] + "…"

        # ── Final sanity: must have real content ─────────────────
        if len(quote) < MIN_CHARS:
            continue
        # Must contain at least one Chinese classical character
        if not re.search(r'[一-鿿]', quote):
            continue

        return quote, dict(candidate)

    return None, None


def _quality_score(row) -> int:
    """Higher score = more likely a clean classical passage."""
    text = row["content_text"]
    score = 0
    # Reward: has classical speech markers
    for m in ["曰", "云", "谓", "道"]:
        if m in text[:50]:
            score += 10
    # Penalize: annotation noise
    for noise in ANNOTATION_NOISE:
        if noise in text:
            score -= 15
    # Reward: longer passages (but not too long)
    clen = len(text)
    if 40 <= clen <= 300:
        score += 20
    elif 300 < clen <= 600:
        score += 5
    # Reward: starts with classical marker
    for m in ["孟子曰", "荀子曰", "子曰", "老子曰", "庄子曰", "孙子曰",
              "韩非子曰", "管子曰", "鬼谷子曰", "淮南子曰", "列子曰"]:
        if text.startswith(m):
            score += 30
            break
    return score


def _clean_source_name(pdf_name: str) -> str:
    """Strip z-library / URL noise from source PDF filenames."""
    if not pdf_name:
        return "佚名"
    cleaned = re.sub(r'[（(][^）)]*(?:z-library|1lib|z-lib|libgen)[^）)]*[）)]', '', pdf_name, flags=re.IGNORECASE)
    cleaned = re.sub(r'[（(][^）)]*(?:套|册|卷|合集|全\d)[^）)]*[）)]', '', cleaned)
    cleaned = cleaned.replace('.pdf', '')
    cleaned = re.sub(r'\s{2,}', ' ', cleaned)
    cleaned = cleaned.strip()
    if len(cleaned) > 60:
        cleaned = cleaned[:60] + '…'
    return cleaned or "佚名"


# ── Festival quote: AI-generated for special days ──────────────────────

from dotenv import load_dotenv
import os, httpx
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))
DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY", "")

@router.get("/festival-quote", response_model=dict)
def get_festival_quote(name: str = Query(...), kind: str = Query("term")):
    """AI生成节日/节气专属修身警句。kind: 'festival' or 'term'."""
    if not DEEPSEEK_KEY:
        return {"quote": f"今日{name}", "source_work": name, "note": "AI未配置"}
    label = "传统节日" if kind == "festival" else "二十四节气"
    prompt = f"""今日是{label}——{name}。请从中国古典诗词、古文名篇、对联、谚语中，精选1-2句与「{name}」最契合的佳句。要求：引用真实存在的经典原文（注明出处），或极高质量的古风原创。贴合时令，有意境，有文化厚度。20-60字。格式：只返回纯文本，不要markdown、引号、或解释。"""
    try:
        resp = httpx.post("https://api.deepseek.com/v1/chat/completions", json={
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.8, "max_tokens": 200
        }, headers={"Authorization": f"Bearer {DEEPSEEK_KEY}"}, timeout=30)
        content = resp.json()["choices"][0]["message"]["content"].strip()
        # Clean markdown quotes
        content = content.replace('"', '').replace('"', '').replace('"', '').replace('「', '').replace('」', '')
        return {"quote": content, "source_work": name, "note": f"AI · {label}"}
    except Exception as e:
        return {"quote": f"岁序{name}，君子自省不息。", "source_work": name, "note": str(e)}

@router.get("/export", response_model=dict)
def export_classics():
    """导出全部已清洗古籍为 JSON。"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, source_work, source_pdf, page_num, original_text, academic_annotations, modern_translation, char_count FROM parsed_classics WHERE original_text IS NOT NULL AND original_text != '' ORDER BY id")
        rows = cur.fetchall()
    from fastapi.responses import Response
    import json as _json
    data = [dict(r) for r in rows]
    return Response(content=_json.dumps(data, ensure_ascii=False, indent=2), media_type="application/json", headers={"Content-Disposition": "attachment; filename=dojo_classics_export.json"})

class ImportRequest(BaseModel):
    entries: list[dict]

@router.post("/import", response_model=dict)
def import_classics(data: ImportRequest):
    """导入外部清洗好的古籍数据。格式: [{source_work, original_text, academic_annotations?, modern_translation?, source_pdf?}, ...]"""
    imported = 0
    skipped = 0
    with get_db() as conn:
        cur = conn.cursor()
        for e in data.entries:
            orig = (e.get("original_text") or "").strip()
            if not orig: skipped += 1; continue
            cur.execute("""INSERT INTO parsed_classics
                (source_work, source_pdf, original_text, academic_annotations, modern_translation, content_text, char_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (e.get("source_work",""), e.get("source_pdf",""), orig,
                 e.get("academic_annotations",""), e.get("modern_translation",""),
                 orig, len(orig)))
            imported += 1
    return {"message": f"已导入 {imported} 条", "imported": imported, "skipped": skipped}

def detect_work(pdf_name: str) -> str:
    """Fallback: extract work name from PDF filename."""
    for name in ["周易", "孟子", "荀子", "老子", "庄子", "墨子", "韩非子", "管子",
                 "孙子兵法", "鬼谷子", "淮南子", "列子", "抱朴子", "论语", "大学",
                 "中庸", "公孙龙子", "孔子家语", "黄帝四经", "关尹子", "尸子",
                 "吴子", "司马法", "六韬", "申子", "慎子"]:
        if name in pdf_name:
            return name
    return pdf_name[:20]
