# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Digital Dojo · 数字道场 — 云端部署 Dockerfile
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Target: 腾讯云轻量服务器 (Tokyo, 43.163.207.116)
# Base:  Python 3.11-slim (轻量, ~120MB)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FROM python:3.11-slim

# ── 元数据 ────────────────────────────────────────────────────
LABEL maintainer="meowzart"
LABEL description="Digital Dojo FastAPI Backend — 冷峻修身系统"
LABEL version="2.0.0"

# ── 环境变量 ──────────────────────────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ── 工作目录 ──────────────────────────────────────────────────
WORKDIR /app

# ── 系统依赖 (uvicorn standard 需要 uvloop/httpptools) ───────
RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ── Python 依赖 ───────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── 应用代码 ──────────────────────────────────────────────────
# 只复制运行时需要的文件，排除 .venv, __pycache__, .git 等
COPY main.py .
COPY database.py .
COPY routers/ ./routers/
COPY static/ ./static/
COPY ai_ingest_parallel.py .
COPY dojo_mcp.py .

# ── 数据目录 (挂载点) ────────────────────────────────────────
# SQLite 数据库文件将存于此目录，通过 docker-compose volume 挂载
RUN mkdir -p /app/data
ENV DOJO_DB_DIR=/app/data

# ── 健康检查 ──────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

# ── 端口 ──────────────────────────────────────────────────────
EXPOSE 8000

# ── 启动 ──────────────────────────────────────────────────────
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
