#!/usr/bin/env python3
"""
AI Smart Ingest — 并行版 (v4.0)
ThreadPool 并发调用 DeepSeek API，10x 提速。

Usage:
  python ai_ingest_parallel.py                  # 测试: 前20条
  python ai_ingest_parallel.py --all            # 全量
  python ai_ingest_parallel.py --limit 500      # 指定条数
  python ai_ingest_parallel.py --workers 15     # 自定义并发数
"""
import os
import sys
import json
import sqlite3
import argparse
import re
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from dotenv import load_dotenv

# ── Config ───────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

DB_PATH = BASE_DIR / "dojo_private.db"
PAGES_PER_CHUNK = 3        # 每 chunk 3 行 (并行版增大 chunk 减少 API 调用)
MAX_CHUNK_CHARS = 6000     # 单次 API 调用的最大字符数
DEFAULT_WORKERS = 10       # 默认并发数

SYSTEM_PROMPT = """你是一个顶级的古籍整理学者。请分析以下 OCR 文本。

【任务】
自动识别真正的古籍原文，严格拆分为短段落：
  1. original_text      — 文言文正文（仅限先秦至明清的古典原文）
  2. annotations        — 学术校勘/注释/训诂/反切/王注/集解/按语
  3. modern_translation — 现代白话文翻译（如有）

【分段铁律 — 每条原文不超过200字】
- 每条 original_text 控制在 20-150 字符之间，严禁超过150字
- 按自然句读拆分：对话回合、章节段落、语义单元
- 长章必须切成多条，不可整章糊在一起
- 示例（公孙丑上应拆为）：
  → "孟子曰：'否。我四十不动心。'"
  → "曰：'若是，则夫子过孟贲远矣。'"
  → "曰：'是不难。告子先我不动心。'"
  每条独立，而非合并成一大段

【去噪铁律 — 只收真正的古籍原文】
- 现代学术导论、作者生平介绍、版本考据、出版说明 → 全部丢弃（不收入 original_text）
- 以下关键词的现代段落必须过滤：生卒年、出版社、ISBN、博士论文、研究、版本、刻本、学者
- 网页 URL、'第x页'、PDF文件名、z-library引用 → 丢弃
- 如果一整段都是现代白话学术文章 → 不出条（不生成条目）
- OCR 乱码 / 无意义字符 → 丢弃

【输出格式】
返回纯 JSON 数组：
[
  {
    "original_text": "孟子曰：人皆有不忍人之心。",
    "annotations": "朱熹注：天地以生物为心，而所生之物，因各得夫天地生物之心以为心。",
    "modern_translation": "孟子说：每个人都有不忍他人受苦的心。"
  }
]
不要 markdown 代码块，不要解释，只输出纯 JSON 数组。"""

# ── DB Helpers ───────────────────────────────────────────────────────────
db_write_lock = threading.Lock()

def get_pending_rows(limit: int | None = None) -> list[dict]:
    """Fetch rows where academic_annotations is NULL."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    query = """
    SELECT id, page_num, source_pdf, source_work, content_text
    FROM parsed_classics
    WHERE content_text IS NOT NULL
      AND content_text != ''
      AND academic_annotations IS NULL
    ORDER BY id ASC
    """
    if limit:
        query += f" LIMIT {limit}"
    cur.execute(query)
    rows = cur.fetchall()
    conn.close()
    return [
        {"id": r["id"], "page_num": r["page_num"], "source_pdf": r["source_pdf"],
         "source_work": r["source_work"], "text": r["content_text"]}
        for r in rows
    ]


def chunk_pages(pages: list[dict], pages_per_chunk: int = PAGES_PER_CHUNK) -> list[dict]:
    """Group pages into multi-page chunks."""
    chunks = []
    for i in range(0, len(pages), pages_per_chunk):
        group = pages[i : i + pages_per_chunk]
        merged_text = "\n\n---\n\n".join(p["text"] for p in group if p.get("text"))
        if len(merged_text) > MAX_CHUNK_CHARS:
            merged_text = merged_text[:MAX_CHUNK_CHARS] + "\n…[截断]"
        ids = [p.get("id") for p in group if p.get("id")]
        start_page = group[0].get("page_num", i + 1)
        end_page = group[-1].get("page_num", i + len(group))
        source_pdf = group[0].get("source_pdf", "")
        source_work = group[0].get("source_work", "")
        chunks.append({
            "ids": ids,
            "start_page": start_page,
            "end_page": end_page,
            "source_pdf": source_pdf,
            "source_work": source_work,
            "text": merged_text,
        })
    return chunks


# ── DeepSeek API ─────────────────────────────────────────────────────────

def _parse_deepseek_json(raw: str) -> list[dict]:
    """Robust JSON parser — handles fences, wrappers, bare objects."""
    if not raw or not raw.strip():
        return []
    text = raw.strip()
    text = re.sub(r'^```(?:json)?\s*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)
    text = text.strip()

    try:
        parsed = json.loads(text)
        return _normalize_parsed(parsed)
    except json.JSONDecodeError:
        pass

    m = re.search(r'(\[.*\]|\{.*\})', text, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(1))
            return _normalize_parsed(parsed)
        except json.JSONDecodeError:
            pass
    return []


def _normalize_parsed(parsed) -> list[dict]:
    """Accept array or object, return flat list of three-field dicts."""
    if isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, dict):
        for key in ("results", "data", "passages", "entries", "items"):
            if key in parsed and isinstance(parsed[key], list):
                items = parsed[key]
                break
        else:
            if "original_text" in parsed:
                items = [parsed]
            else:
                items = []
                for v in parsed.values():
                    if isinstance(v, list):
                        items.extend(v)
    else:
        return []

    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        orig = str(item.get("original_text", "")).strip()
        if not orig:
            continue
        result.append({
            "original_text": orig,
            "annotations": str(item.get("annotations", "")).strip(),
            "modern_translation": str(item.get("modern_translation", "")).strip(),
        })
    return result


def call_deepseek(chunk_text: str, source_info: str) -> list[dict]:
    """Send chunk to DeepSeek, return structured passages."""
    if not DEEPSEEK_API_KEY:
        return []

    user_prompt = f"【来源】{source_info}\n\n【待分析 OCR 文本】\n{chunk_text}"
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 4096,
    }

    for attempt in range(3):
        try:
            resp = httpx.post(
                DEEPSEEK_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                timeout=180.0,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            tokens = data.get("usage", {}).get("total_tokens", "?")
            return _parse_deepseek_json(content)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429 and attempt < 2:
                time.sleep(2 ** attempt * 2)
                continue
            return []
        except (KeyError, json.JSONDecodeError, httpx.RequestError):
            if attempt < 2:
                time.sleep(1)
                continue
            return []
    return []


# ── DB Write-back ────────────────────────────────────────────────────────

def update_structured_rows(db_ids: list[int], passages: list[dict], source_work: str) -> int:
    """UPDATE rows with structured fields, INSERT extras."""
    with db_write_lock:
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.cursor()
        updated = 0
        for i, p in enumerate(passages):
            if i < len(db_ids):
                cur.execute(
                    """UPDATE parsed_classics
                       SET original_text = ?, academic_annotations = ?, modern_translation = ?,
                           source_work = COALESCE(NULLIF(source_work,''), ?)
                       WHERE id = ?""",
                    (p["original_text"], p["annotations"], p["modern_translation"],
                     source_work, db_ids[i]),
                )
                updated += 1
            else:
                cur.execute(
                    """INSERT INTO parsed_classics
                       (source_pdf, source_work, content_text,
                        original_text, academic_annotations, modern_translation, char_count)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    ("db-reprocess", source_work, p["original_text"],
                     p["original_text"], p["annotations"], p["modern_translation"],
                     len(p["original_text"])),
                )
                updated += 1
        conn.commit()
        conn.close()
    return updated


# ── Worker ───────────────────────────────────────────────────────────────

_progress_lock = threading.Lock()
_progress = {"done": 0, "total": 0, "updated": 0, "passages": 0}

def process_chunk(chunk: dict, index: int, total: int) -> dict:
    """Process one chunk: call API → write DB. Returns stats."""
    source_info = f"{chunk.get('source_pdf','?')[:80]} 页{chunk['start_page']}-{chunk['end_page']}"
    short_src = f"页{chunk['start_page']}-{chunk['end_page']}"

    passages = call_deepseek(chunk["text"], source_info)

    updated = 0
    if passages:
        updated = update_structured_rows(
            chunk["ids"], passages, chunk.get("source_work", "")
        )

    with _progress_lock:
        _progress["done"] += 1
        _progress["updated"] += updated
        _progress["passages"] += len(passages)
        pct = _progress["done"] * 100 / _progress["total"]
        print(f"  [{_progress['done']:4d}/{total}] {short_src:20s} "
              f"→ {len(passages):2d}段 写{updated:2d}行 "
              f"({pct:5.1f}%)", flush=True)

    return {"chunk_index": index, "passages": len(passages), "updated": updated}


# ── Main Pipeline ────────────────────────────────────────────────────────

def run_pipeline(limit: int | None = None, workers: int = DEFAULT_WORKERS):
    """Main: load → chunk → parallel API → write."""
    t0 = time.time()

    # Load
    pages = get_pending_rows(limit=limit)
    if not pages:
        print("✅ 无待处理数据")
        return

    chunks = chunk_pages(pages)
    _progress["total"] = len(chunks)

    print(f"📊 待处理: {len(pages)} 行 → {len(chunks)} chunks")
    print(f"⚡ 并发数: {workers}")
    print(f"📐 每 chunk: {PAGES_PER_CHUNK} 行, max {MAX_CHUNK_CHARS} 字符")
    print(f"{'='*60}")

    # Parallel API calls
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(process_chunk, chunk, i, len(chunks)): i
            for i, chunk in enumerate(chunks)
        }
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"  ❌ chunk 异常: {e}", flush=True)

    # Summary
    elapsed = time.time() - t0
    remaining = len(get_pending_rows()) if limit is None else 0

    print(f"\n{'='*60}")
    print(f"📊 汇总")
    print(f"  Chunks: {_progress['done']} → 段落: {_progress['passages']} → 写入: {_progress['updated']}")
    print(f"  耗时: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    if _progress['done'] > 0:
        rate = _progress['done'] / elapsed * 60
        print(f"  速率: {rate:.1f} chunks/min")
    if limit is None:
        print(f"  剩余待结构化: {remaining} 条")
        if remaining > 0:
            print(f"  继续: python ai_ingest_parallel.py --all")


def main():
    parser = argparse.ArgumentParser(description="AI Ingest Parallel — 多线程古籍萃取")
    parser.add_argument("--all", action="store_true", help="全量处理")
    parser.add_argument("--limit", type=int, default=None, help="限制条数 (默认20测试)")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"并发线程数 (默认{DEFAULT_WORKERS})")
    args = parser.parse_args()

    limit = None if args.all else (args.limit or 20)

    if not DEEPSEEK_API_KEY:
        print("❌ DEEPSEEK_API_KEY 未在 .env 中设置")
        sys.exit(1)

    if args.all:
        print(f"🚀 全量模式 — {DEFAULT_WORKERS} 并发")
    else:
        print(f"🧪 测试模式 — 前 {limit} 条, {args.workers} 并发")

    run_pipeline(limit=limit, workers=args.workers)


if __name__ == "__main__":
    main()
