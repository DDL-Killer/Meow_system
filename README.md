# 数字道场 · Digital Dojo

> 冷峻修身系统 — 每日规划、功过格积分、AI 情绪分析、古籍晨读

## 功能

### 核心模块
- **每日任务** — 短期/长期/半永久三种体系，按日期自动匹配
- **功过格积分** — 完成任务加分、失败扣分、连续7天奖励、境界升级
- **起寐记录** — 确认弹窗记录起床/入睡，5AM 分界逻辑
- **古籍晨读** — 每日一句随机古籍，节日/节气 AI 自动生成诗词
- **录音分析** — 上传语音 → DeepSeek AI 情绪分析 → 结构化反馈
- **管理员面板** — 长按境界 3 秒进入，可修改作息、撤销任务、管理录音

### 技术特性
- **农历 + 公历双显示** — 天干地支 + 二十四节气 + 传统节日
- **Tauri v2 桌面壳** — Rust HTTP 代理，零跨域，系统托盘
- **PWA 支持** — 可安装到主屏幕，离线缓存
- **DeepSeek AI** — 古籍萃取、语音分析、节日诗词生成
- **自动数据管线** — 一键从 GitHub 开源数据拉取 24 部经典入库

## 快速开始

```bash
# 本地开发
cd MeowSystem
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # 填入 DEEPSEEK_API_KEY
python main.py         # http://localhost:8000

# Docker 部署
docker-compose up -d
```

## 项目结构

```
├── main.py                    # FastAPI 入口
├── database.py                # SQLite schema + 迁移
├── routers/
│   ├── tasks.py               # 任务 CRUD + 完成/失败/撤销
│   ├── chronicle.py           # 起寐记录 (5AM分界)
│   ├── classics.py            # 古籍晨读 + AI节日诗词
│   ├── cultivation.py         # 功过格积分
│   ├── voice.py               # 录音上传 + AI分析
│   ├── analytics.py           # 内省看板
│   └── goals.py               # 长期目标
├── ai_ingest_parallel.py      # DeepSeek 并行 AI 萃取
├── auto_ingest_all_classics.py # 全自动 24 部经典入库
├── auto_reset_classics.py     # 数据库重置 + 同步上云
├── static/                    # 前端 SPA
├── src-tauri/                 # Tauri v2 桌面壳
├── docker-compose.yml         # Docker 编排
└── Dockerfile                 # Docker 镜像
```

## 古籍数据

来自 [chinese-poetry](https://github.com/chinese-poetry/chinese-poetry) 开源项目，包含：

| 经典 | 条数 |
|------|------|
| 论语 | 512 |
| 孟子 | 690 |
| 楚辞 | 2,273 |
| 诗经 | 1,319 |
| 大学 | 16 |
| 中庸 | 39 |
| 荀子/老子/庄子等 | 175 |
| **总计** | **5,024** |

## 桌面应用

```bash
# macOS (需要 Xcode 或 Command Line Tools)
cargo install tauri-cli
cargo tauri build --target aarch64-apple-darwin
```

## 部署

```bash
# 服务器
scp dojo_private.db root@SERVER:/opt/dojo/
ssh root@SERVER "cd /opt/dojo && docker-compose up -d"

# 或使用自动同步脚本
python auto_reset_classics.py --sync
```

## 技术栈

FastAPI · SQLite · DeepSeek AI · Tauri v2 · Rust · Chart.js · Docker · Tailwind CSS

## License

MIT
