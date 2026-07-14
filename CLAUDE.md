数字道场 · Digital Dojo

冷峻修身系统 — 每日规划、功过格积分、AI 情绪分析、古籍晨读。

## 启动

```bash
cd /Users/meowzart/Desktop/MeowSystem

# 后端 (端口 8000)
.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000

# 前端就是后端 — 浏览器打开
open http://localhost:8000
```

## 架构

```
MeowSystem/
├── main.py                  # FastAPI 入口, CORS, router 注册, 鉴权中间件
├── database.py              # SQLite schema + 迁移 + compute_goal_daily_tasks
├── routers/
│   ├── tasks.py             # CRUD + GET /tasks/today + 完成/失败/撤销/软删除
│   ├── cultivation.py       # 功过格积分 GET /cultivation
│   ├── chronicle.py         # 每日纪事 POST/GET wake/sleep (5AM分界, 支持指定时间)
│   ├── classics.py          # 古籍晨读 GET /daily-quote + GET /festival-quote (AI诗词)
│   ├── analytics.py         # 内省看板 GET /analytics?days=N
│   ├── voice.py             # 录音上传 + DeepSeek 分析 + 删除
│   ├── goals.py             # 长期目标 CRUD + urgency
│   └── sleep.py             # 旧版, 已被 chronicle 替代 (保留数据)
├── ai_ingest_parallel.py    # DeepSeek 并行结构化萃取 (v4.0, 10线程)
├── dojo_mcp.py              # MCP Server
├── static/index.html        # SPA 前端 (水墨风, 农历+公历, 确认弹窗, 管理后台)
├── dojo_private.db          # SQLite 数据库 (29K+ 条古籍)
├── classics_library/        # PDF 典籍
├── _archive/                # 旧版脚本 (v2, v3 清洗)
├── src-tauri/               # Tauri v2 桌面壳 (macOS/Linux/Android)
│   ├── tauri.conf.json
│   ├── Cargo.toml
│   └── src/lib.rs           # 后端自启 + 系统托盘
├── dojo_client/             # Flutter 移动端 (独立项目)
├── docker-compose.yml       # Docker 部署编排
├── Dockerfile               # Docker 镜像
└── .env                     # DEEPSEEK_API_KEY
```

## 数据库表

| 表 | 用途 |
|---|---|
| `tasks` | 任务 (task_scope: short_term/long_term/semi_permanent, 5 domain) |
| `daily_chronicle` | 每日纪事 (wake_time, sleep_time) |
| `self_cultivation` | 功过格积分 (score, streak, realm) |
| `parsed_classics` | 古籍 (original_text, academic_annotations, modern_translation) — 29,075条 |
| `long_term_goals` | 长期目标 (倒推引擎) |
| `voice_logs` | 录音记录 + DeepSeek 分析 |
| `sleep_tracker` | 旧版作息表 (已废弃, 保留数据) |

## 三种任务体系

- **short_term**: 今日创建, 明日自动消失
- **long_term**: 选 start_date→end_date, 时间段内每日出现
- **semi_permanent**: 创建后每日出现, 直到手动完成/失败

`GET /tasks/today` 按日期过滤返回当日应出现的所有 pending 任务。

## 古籍锁定机制

`GET /daily-quote` 使用 `random.seed(date.today().toordinal())` 确保全天同一句。
节日/节气自动调用 DeepSeek 生成诗词名句 (`GET /daily-quote/festival-quote`)。
前端 localStorage 缓存, 跨日自动刷新。

## 目标倒推引擎

`compute_goal_daily_tasks()` 在 `database.py`:
- 计算 daily_needed = progress_gap / days_left
- urgency: critical(≤3天) / warning(≤7天) / safe
- 自动注入 goal_split 任务到每日列表

## AI 古籍萃取

`ai_ingest_parallel.py --all --workers 10` — 并行 10 线程调用 DeepSeek API。
每 chunk 3 行, prompt 强制拆分为 30-200 字短段落, 过滤现代学术导论。
结果: 29,075 条, 含注释 7,857 (27%), 含译文 2,457 (8.5%)。

## 前端页面

| 页签 | 路由 | 内容 |
|---|---|---|
| 主页 | #home | 农历+公历日期, 古籍/AI诗词, 起寐(确认弹窗, 5AM分界), 今日任务 |
| 看板 | #analytics | Chart.js 图表, 时间范围 7/30/90/365天 |
| 录音 | #voice | 录音按钮 + AI 分析卡片 + 历史 |
| 目标 | #goals | 长期目标管理 + 进度条 |

底部 + 事件按钮打开创建弹窗。
点击时间条打开确认弹窗记录起床/入睡 (每日仅一次)。
长按「境界」3 秒进入管理者面板。

## 管理者面板

长按首页「境界 · X」3秒进入:
- 修改任意日期起/寐时间
- 撤销已完成任务 (退回待办, 扣回分数)
- 管理/删除录音记录
- 重算功过格 / 导出数据

## 桌面应用

Tauri v2 构建:
```bash
cargo tauri build --target aarch64-apple-darwin   # macOS .app + .dmg
cargo tauri android build --apk                     # Android (需 SDK/NDK)
```

## 部署

```bash
# Docker
docker-compose up -d --build

# 直接部署 (需 systemd)
sudo cp digital_dojo.service /etc/systemd/system/
sudo systemctl enable --now digital_dojo
```

## 已知问题

- Android APK 构建: Gradle 下载超时 (需手动下载 gradle-8.14.3 到 ~/.gradle)
- Linux 桌面应用未构建 (需交叉编译或 Docker)
- 古籍剩余 167 条长段落 + 17 条空白页
- 移动端适配未完成
