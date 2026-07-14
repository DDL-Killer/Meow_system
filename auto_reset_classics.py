#!/usr/bin/env python3
"""
自动清洗脚本 — 清空数据库, 从开源项目拉取纯净古籍, 批量写入, 同步到服务器.

数据源: chinese-poetry/chinese-poetry (GitHub)
  - 论语 (20篇)
  - 孟子 (14篇)
  - 大学 (1篇)
  - 中庸 (1篇)
  - 诗经 (305篇)

Usage:
  python auto_reset_classics.py          # 本地执行
  python auto_reset_classics.py --sync   # 本地执行 + 同步到服务器
"""
import sqlite3
import json
import os
import sys
import argparse
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "dojo_private.db"
SERVER = "43.163.207.116"
SSH_KEY = BASE_DIR / "Meowzart_1.pem"

# ── 数据源 ───────────────────────────────────────────────────────
SOURCES = [
    {
        "name": "论语",
        "url": "https://raw.githubusercontent.com/chinese-poetry/chinese-poetry/master/%E8%AE%BA%E8%AF%AD/lunyu.json",
        "key": "paragraphs",
    },
    {
        "name": "孟子",
        "url": "https://raw.githubusercontent.com/chinese-poetry/chinese-poetry/master/%E5%9B%9B%E4%B9%A6%E4%BA%94%E7%BB%8F/mengzi.json",
        "key": "paragraphs",
    },
    {
        "name": "大学",
        "url": "https://raw.githubusercontent.com/chinese-poetry/chinese-poetry/master/%E5%9B%9B%E4%B9%A6%E4%BA%94%E7%BB%8F/daxue.json",
        "key": "paragraphs",
        "list_chapters": True,
    },
    {
        "name": "中庸",
        "url": "https://raw.githubusercontent.com/chinese-poetry/chinese-poetry/master/%E5%9B%9B%E4%B9%A6%E4%BA%94%E7%BB%8F/zhongyong.json",
        "key": "paragraphs",
        "list_chapters": True,
    },
    {
        "name": "诗经",
        "url": "https://raw.githubusercontent.com/chinese-poetry/chinese-poetry/master/%E8%AF%97%E7%BB%8F/shijing.json",
        "key": "content",
        "list_chapters": False,
    },
]

def fetch_json(url: str) -> list:
    """Download and parse JSON from URL."""
    req = urllib.request.Request(url, headers={"User-Agent": "DojoAutoReset/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))

def reset_and_import():
    """Step 1: Clear DB and import clean classics."""
    print("=" * 60)
    print("  道场 · 自动清洗")
    print("=" * 60)

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # ── 清空 ──
    cur.execute("SELECT COUNT(*) FROM parsed_classics")
    before = cur.fetchone()[0]
    print(f"\n📊 清空前: {before} 条")

    cur.execute("DELETE FROM parsed_classics")
    conn.commit()
    print(f"🗑️  已清空")

    # ── 拉取 & 导入 ──
    total = 0
    for src in SOURCES:
        try:
            print(f"\n📥 下载 {src['name']}...")
            data = fetch_json(src["url"])
            chapters = len(data) if isinstance(data, list) else 1
            inserted = 0
            is_list = src.get("list_chapters", False)
            items = data if isinstance(data, list) else [data]

            for item in items:
                paragraphs = []
                if isinstance(item, str):
                    # Plain string entry
                    paragraphs = [item]
                elif isinstance(item, dict):
                    paragraphs = item.get(src["key"], [])
                    if isinstance(paragraphs, str):
                        paragraphs = [paragraphs]
                elif isinstance(item, list):
                    # Nested list
                    for sub in item:
                        if isinstance(sub, str):
                            paragraphs.append(sub)
                        elif isinstance(sub, dict):
                            ps = sub.get(src["key"], [])
                            paragraphs.extend(ps if isinstance(ps, list) else [ps])

                for para in paragraphs:
                    text = para.strip() if isinstance(para, str) else str(para).strip()
                    if not text or len(text) < 2:
                        continue
                    cur.execute("""
                        INSERT INTO parsed_classics
                        (source_work, source_pdf, content_text, original_text,
                         academic_annotations, modern_translation, char_count)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (src["name"], src["name"], text, text, "", "", len(text)))
                    inserted += 1
            print(f"   ✅ {src['name']}: {chapters}条 → {inserted} 段")
            total += inserted
        except Exception as e:
            print(f"   ❌ {src['name']} 下载失败: {e}")
            continue

    conn.commit()
    cur.execute("SELECT COUNT(*) FROM parsed_classics")
    after = cur.fetchone()[0]
    conn.close()

    print(f"\n{'='*60}")
    print(f"📊 清空后: {after} 条")
    print(f"📊 本次导入: {total} 条")
    print(f"📊 数据源: chinese-poetry/chinese-poetry (GitHub)")
    print(f"{'='*60}")
    return total

def sync_to_server():
    """Step 2: Upload DB to server and restart."""
    print(f"\n🚀 同步到服务器 {SERVER}...")

    # Upload DB
    remote_path = f"root@{SERVER}:/opt/dojo/dojo_private.db"
    cmd = f"scp -i {SSH_KEY} -o StrictHostKeyChecking=no {DB_PATH} {remote_path}"
    print(f"   📤 {cmd}")
    ret = os.system(cmd)
    if ret != 0:
        print("   ❌ SCP 失败")
        return False

    # Restart container
    ssh_cmd = f'ssh -i {SSH_KEY} -o StrictHostKeyChecking=no root@{SERVER} "docker cp /opt/dojo/dojo_private.db digital-dojo:/app/data/ && docker restart digital-dojo"'
    print(f"   🔄 重启容器...")
    ret = os.system(ssh_cmd)
    if ret != 0:
        print("   ❌ 重启失败")
        return False

    # Verify
    print(f"\n✅ 同步完成！查询服务器数据...")
    import subprocess
    result = subprocess.run([
        "ssh", "-i", str(SSH_KEY), "-o", "StrictHostKeyChecking=no",
        f"root@{SERVER}",
        'docker exec digital-dojo python3 -c "import sqlite3;c=sqlite3.connect(\"/app/data/dojo_private.db\").cursor();c.execute(\"SELECT COUNT(*) FROM parsed_classics\");print(f\"云端: {c.fetchone()[0]} 条\")"'
    ], capture_output=True, text=True)
    print(result.stdout.strip())
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="道场自动清洗脚本")
    parser.add_argument("--sync", action="store_true", help="本地清洗后同步到服务器")
    args = parser.parse_args()

    total = reset_and_import()

    if args.sync:
        sync_to_server()
    else:
        print("\n💡 运行 python auto_reset_classics.py --sync 同步到服务器")
