"""
Voice router — 睡前录音上传 & AI 分析.

POST /voice/upload   — accept audio file → DeepSeek-R1 analysis → persist
GET  /voice/logs     — list recent voice-log entries

DeepSeek integration follows `get_deepseek_analysis_config` from mcp.py:
  - endpoint: https://api.deepseek.com/v1/chat/completions
  - model:   deepseek-reasoning
  - prompt:  冷峻旁观者结构化分析
  - auth:    Bearer token from os.getenv("DEEPSEEK_API_KEY")
"""
import json
import os
import re
import shutil
import uuid
from datetime import date
from typing import Optional

import httpx
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from database import get_db

router = APIRouter(prefix="/voice", tags=["voice"])

# ── Config (mirrored from get_deepseek_analysis_config) ────────────────

DEEPSEEK_ENDPOINT    = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL       = "deepseek-v4-pro"
DEEPSEEK_TEMPERATURE = 0.2
DEEPSEEK_SYSTEM_PROMPT = (
    "你是一个冷峻、严厉的古典修身旁观者。请对用户今晚的睡前语音日记进行解剖。"
    "忽略废话，提取出以下严格的结构化 JSON 字段：\n"
    "{\n"
    '  "emotion_state": "情绪标签(如:焦虑/内耗/笃定/浮躁)",\n'
    '  "core_event": "今日遭遇核心事件摘要",\n'
    '  "slack_tendency": "是否存在偷懒或懈怠倾向(True/False)",\n'
    '  "reproof": "一句极具警醒感的冷峻古风评语"\n'
    "}\n"
    "注意：严禁任何温和的夸奖或心理按摩，必须保持绝对客观与内省鞭策。"
    "请只返回JSON，不要包含任何其他文字、markdown标记或解释。"
)

AUDIO_TMP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audio_uploads")
os.makedirs(AUDIO_TMP_DIR, exist_ok=True)

# ── Schemas ────────────────────────────────────────────────────────────


class VoiceLogOut(BaseModel):
    id: int
    raw_text: Optional[str] = None
    deepseek_analysis: Optional[str] = None
    emotion_state: Optional[str] = None
    created_date: Optional[str] = None


class AnalysisResponse(BaseModel):
    log_id: int
    raw_text: str
    analysis: dict
    emotion_state: Optional[str] = None
    model_used: str = DEEPSEEK_MODEL


# ── Helpers ────────────────────────────────────────────────────────────


def _clean_json(text: str) -> str:
    """Strip markdown fences and extract the first JSON object from DeepSeek's response."""
    # Remove ```json ... ``` fences
    text = re.sub(r'```(?:json)?\s*', '', text)
    text = re.sub(r'```\s*$', '', text)
    text = text.strip()
    # Try to extract the first { ... } block
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        return match.group(0)
    return text


# ── Routes ─────────────────────────────────────────────────────────────


@router.post("/upload", response_model=AnalysisResponse)
async def upload_voice(file: UploadFile = File(...)):
    """
    Upload 睡前录音 (audio file).

    Pipeline:
      1. Save uploaded audio to disk
      2. Transcribe audio → text (Whisper / STT service)
      3. Send transcribed text → DeepSeek-R1 for cold analysis
      4. Parse the structured JSON response
      5. Persist to voice_logs table
      6. Return the analysis to the frontend
    """
    # ── 1. Save upload ───────────────────────────────────────────
    if not file.filename:
        raise HTTPException(400, "文件名不能为空")

    ext = os.path.splitext(file.filename)[1] or ".audio"
    safe_name = f"{uuid.uuid4().hex}{ext}"
    disk_path = os.path.join(AUDIO_TMP_DIR, safe_name)

    try:
        with open(disk_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(500, f"文件保存失败: {str(e)}")

    # ── 2. Transcription ─────────────────────────────────────────
    # STUB: 此处接入语音转文字服务 (Whisper / OpenAI STT / 阿里云ASR)
    # 当前使用文件原文占位 — 替换为真实转录逻辑后即可全链路跑通
    raw_text = f"[语音转录待接入 — 文件 {safe_name} 已保存]"

    # ── 3. DeepSeek-R1 analysis (LIVE) ────────────────────────────
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise HTTPException(500, "DEEPSEEK_API_KEY 未配置 — 请在 .env 或环境变量中设置")

    analysis_json = None
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                DEEPSEEK_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": DEEPSEEK_MODEL,
                    "temperature": DEEPSEEK_TEMPERATURE,
                    "messages": [
                        {"role": "system", "content": DEEPSEEK_SYSTEM_PROMPT},
                        {"role": "user", "content": raw_text},
                    ],
                },
            )
            resp.raise_for_status()
            payload = resp.json()
            analysis_raw = payload["choices"][0]["message"]["content"]
            analysis_json = json.loads(_clean_json(analysis_raw))

    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"DeepSeek API 返回错误 {e.response.status_code}: {e.response.text[:200]}")
    except httpx.RequestError as e:
        raise HTTPException(502, f"无法连接 DeepSeek API: {str(e)}")
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        raise HTTPException(502, f"DeepSeek 返回格式异常: {str(e)}")

    # ── 4. Persist ───────────────────────────────────────────────
    emotion = analysis_json.get("emotion_state", None)

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO voice_logs (raw_text, deepseek_analysis, emotion_state, created_date)
               VALUES (?, ?, ?, ?)""",
            (raw_text, json.dumps(analysis_json, ensure_ascii=False), emotion, date.today().isoformat()),
        )
        log_id = cur.lastrowid

    return AnalysisResponse(
        log_id=log_id,
        raw_text=raw_text,
        analysis=analysis_json,
        emotion_state=emotion,
        model_used=DEEPSEEK_MODEL,
    )


@router.get("/logs", response_model=list[VoiceLogOut])
def list_voice_logs(
    limit: int = Query(10, ge=1, le=100),
    date_filter: Optional[str] = Query(None, alias="date", description="YYYY-MM-DD"),
):
    """List recent voice-log entries, newest first."""
    query = "SELECT * FROM voice_logs"
    params: list = []

    if date_filter:
        query += " WHERE created_date = ?"
        params.append(date_filter)

    query += " ORDER BY created_date DESC, id DESC LIMIT ?"
    params.append(limit)

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(query, params)
        rows = cur.fetchall()

    return [VoiceLogOut(**dict(r)) for r in rows]

@router.delete("/{log_id}", response_model=dict)
def delete_voice_log(log_id: int):
    """管理者：删除指定录音记录。"""
    from database import get_db
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM voice_logs WHERE id = ?", (log_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="录音不存在")
        cur.execute("DELETE FROM voice_logs WHERE id = ?", (log_id,))
        return {"message": "已删除", "log_id": log_id}
