"""
Digital Dojo — FastAPI Backend
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

每日精进·功过格·境界跃迁·AI 冷峻分析·古籍晨读

Run:
    uvicorn main:app --reload --port 8000
"""
import os
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv

# ── 时区：统一使用 Asia/Shanghai (UTC+8) ──────────────────────────
os.environ['TZ'] = 'Asia/Shanghai'
time.tzset()

# ── 加载 .env 环境变量（必须在 app 创建之前）────────────────────────
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from database import init_database
from routers import tasks, cultivation, voice, classics, analytics, sleep, chronicle
# from routers import goals  # v1.1: 长期目标已隐藏

# ── Constants ───────────────────────────────────────────────────────────

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)


# ── Lifespan (startup / shutdown) ──────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """On startup: init DB. On shutdown: clean up (no-op for SQLite)."""
    msg = init_database()
    print(msg)
    yield


# ── App ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Digital Dojo · 数字道场",
    description="冷峻修身系统 — 每日规划、功过格积分、AI 情绪分析、古籍晨读",
    version="2.0.0",
    lifespan=lifespan,
)

# ═════════════════════════════════════════════════════════════════════════
# CORS — 彻底放开跨域，确保 Flutter Web / 浏览器 / 移动端 POST/GET 100% 穿透
# ═════════════════════════════════════════════════════════════════════════
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],            # 所有来源均可跨域
    allow_credentials=False,        # allow_origins=["*"] 时不可设为 True
    allow_methods=["*"],            # GET / POST / PATCH / DELETE / OPTIONS 全放行
    allow_headers=["*"],            # Content-Type / Authorization / 自定义头 全放行
    expose_headers=["*"],           # 允许客户端读取所有响应头
    max_age=86400,                  # 预检缓存 24h，减少 OPTIONS 请求
)

# ═════════════════════════════════════════════════════════════════════════
# 硬核鉴权中间件 — Bearer Token
# ═════════════════════════════════════════════════════════════════════════
# 设置环境变量 DOJO_API_TOKEN 即开启鉴权。
# 客户端必须在请求头中携带: Authorization: Bearer <token>
# 未设置 DOJO_API_TOKEN 时 → 所有请求放行 (兼容本地开发)。
# ═════════════════════════════════════════════════════════════════════════
DOJO_API_TOKEN = os.getenv("DOJO_API_TOKEN", "").strip()

# 无需鉴权的公开路径前缀 (GET 只读)
# 注意: 个人数据接口 (/tasks /voice /chronicle /cultivation /analytics) 一律需要 token,
#       两个 App 的 Rust 代理层所有请求都自带 Bearer token, 不受影响。
PUBLIC_GET_PREFIXES = [
    "/daily-quote",          # 古籍晨读 (无个人数据)
    "/static",               # 前端静态资源 (登录页需要)
    "/",                     # 根路径 SPA (登录页)
]

# 健康检查端点始终放行
PUBLIC_EXACT = {"/health", "/favicon.ico"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """极简硬核鉴权: Bearer Token 校验."""
    # ── 未配置 token → 完全放行 (开发模式) ─────────────────────
    if not DOJO_API_TOKEN:
        return await call_next(request)

    path = request.url.path

    # ── 公开路径: GET 请求放行 ─────────────────────────────────
    if path in PUBLIC_EXACT:
        return await call_next(request)

    if request.method == "GET":
        for prefix in PUBLIC_GET_PREFIXES:
            if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "?"):
                return await call_next(request)

    # ── 鉴权校验 ──────────────────────────────────────────────
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"detail": "Missing or malformed Authorization header. Use: Bearer <token>"})

    token = auth_header[7:]  # strip "Bearer "
    if token != DOJO_API_TOKEN:
        return JSONResponse(status_code=403, content={"detail": "Invalid API token"})

    return await call_next(request)


# ── Health check (always public) ──────────────────────────────────────────

@app.get("/health", tags=["system"])
async def health_check():
    """Kubernetes / Docker 健康检查端点."""
    return {"status": "ok", "service": "digital-dojo", "version": "2.0.0"}


# ── Static files ────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── API Routers ─────────────────────────────────────────────────────────

app.include_router(tasks.router)
app.include_router(cultivation.router)
app.include_router(voice.router)
app.include_router(classics.router)
app.include_router(analytics.router)
# app.include_router(goals.router)  # v1.1: 长期目标已隐藏
app.include_router(sleep.router)
app.include_router(chronicle.router)


# ── Root — serve the SPA ────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse, tags=["ui"])
async def root():
    """Serve the Digital Dojo single-page application."""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>index.html not found — place it in static/</h1>", status_code=404)
