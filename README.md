# 数字道场 · Digital Dojo

> 冷峻修身系统 — 每日规划、功过格积分、AI 情绪分析、古籍晨读

水墨风 SPA 全栈应用，支持 macOS / Android 桌面壳 + Docker 云部署。

## 功能

- **每日任务** — 短期 / 长期 / 半永久三种体系，自动匹配日期
- **功过格积分** — 完成任务 +10，失败 -15，连签 7 天奖励 +30，五境界升级
- **起寐记录** — 确认弹窗记录起床/入睡，5AM 分界
- **古籍晨读** — 每日一句，节日/节气 AI 生成诗词
- **语音日记** — 录音 → Whisper 转录 → DeepSeek 情绪分析 → 冷峻古风评语
- **内省看板** — Chart.js 可视化，7/30/90/365 天趋势
- **管理员面板** — 长按境界 3 秒进入，可修改作息、撤销任务、管理录音

## 快速开始

```bash
git clone https://github.com/DDL-Killer/Meow_system.git
cd Meow_system

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 编辑 .env 填入 DEEPSEEK_API_KEY
python main.py          # 浏览器打开 http://localhost:8000
```

## 部署

### Docker

```bash
docker-compose up -d
```

### 桌面应用

```bash
# macOS
cargo tauri build --target aarch64-apple-darwin

# Android (需 SDK/NDK)
cargo tauri android build --apk
```

**构建前修改两处服务器地址：**

1. `static/index.html` — `SERVER_HOST` 和 `API_TOKEN`
2. `src-tauri/src/lib.rs` — `SERVER` 常量

## 架构

```
浏览器 / Tauri WebView
        │
        ▼
┌─────────────────────────────────┐
│  FastAPI (Python 3.11)          │
│  routers/                       │
│  ├── tasks.py      任务管理      │
│  ├── voice.py      录音+AI分析   │
│  ├── cultivation.py 功过格       │
│  ├── chronicle.py   起寐记录     │
│  ├── classics.py    古籍晨读     │
│  └── analytics.py   内省看板     │
├─────────────────────────────────┤
│  SQLite (dojo_private.db)       │
│  faster-whisper (语音转录)       │
│  DeepSeek API (AI 分析)          │
└─────────────────────────────────┘
```

## 语音系统

```
录音 → MediaRecorder → WebM → AudioContext.decode → PCM → WAV
上传 → base64 → Tauri IPC → Rust multipart → 服务器
回放 → Tauri IPC → Vec<u8> → Uint8Array → Blob URL → <Audio>
```

> WKWebView (macOS) 不支持 WebM/MP4 音频播放，所以客户端转 WAV。

## 古籍数据

来自 [chinese-poetry](https://github.com/chinese-poetry/chinese-poetry)，24 部经典，5,024 条。

自动入库：
```bash
python auto_ingest_all_classics.py --all --workers 10
```

## 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `DEEPSEEK_API_KEY` | 是 | DeepSeek API 密钥 |
| `DOJO_API_TOKEN` | 否 | 设即开启 Bearer Token 鉴权，留空=开发模式 |

## 技术栈

FastAPI · SQLite · faster-whisper · DeepSeek · Tauri v2 · Rust · Chart.js · Docker · WKWebView

## License

MIT
