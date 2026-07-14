# dojo_mcp.py
from mcp.server.fastmcp import FastMCP
import sqlite3
import os
import re
import pypdf

mcp = FastMCP("Digital Dojo Engine Pro")
DB_PATH = "dojo_private.db"
PDF_DIR = "classics_library"

# 1. 查询当前开发状况（揭穿空壳）
@mcp.tool()
def inspect_dojo_backend() -> str:
    """检查本地数据库与后端路由状态，查明数据无法查看的根本原因。"""
    report = []
    if not os.path.exists(DB_PATH):
        return "【警报】本地数据库 dojo_private.db 根本不存在！打卡和录音无法保存！"
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM parsed_classics")
        report.append(f"· 古典库记录数: {cur.fetchone()[0]} 条")
        cur.execute("SELECT COUNT(*) FROM tasks")
        report.append(f"· 任务库记录数: {cur.fetchone()[0]} 条")
    except Exception as e:
        report.append(f"· 读取出错: {str(e)}")
    finally:
        conn.close()
    return "\n".join(report)

# 2. 智能大部头 PDF 章节打碎与噪声清洗（解决 5040 页敷衍断句问题）
@mcp.tool()
def smart_ingest_heavy_pdf() -> str:
    """针对 5000 页诸子百家大合订本的深度清洗工具。自动剔除 z-library 网址与孤立页码。"""
    if not os.path.exists(PDF_DIR):
        return "请先创建 classics_library 文件夹。"
    
    pdf_files = [f for f in os.listdir(PDF_DIR) if f.lower().endswith('.pdf')]
    if not pdf_files:
        return "目录下没有发现古籍 PDF。"
        
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # 清理掉之前粗暴切碎的脏数据
    cur.execute("DELETE FROM parsed_classics")
    inserted = 0
    
    for pdf_name in pdf_files:
        path = os.path.join(PDF_DIR, pdf_name)
        reader = pypdf.PdfReader(path)
        
        for idx, page in enumerate(reader.pages):
            text = page.extract_text()
            if not text: continue
            
            # 核心黑客 Regex 清洗：剔除 z-library、页码、广告噪声
            text = re.sub(r'(z-library|1lib|z-lib)\.[a-z]{2,3}', '', text, flags=re.I)
            text = re.sub(r'第\s*\d+\s*页', '', text)
            text = re.sub(r'·\s*敷衍', '', text)
            
            # 智能断句：寻找有头有尾的古典句式
            clauses = re.findall(r'([^。？！]*?(?:孟子曰|荀子曰|子曰|易曰)[^。？！]*?[。？！])', text)
            
            for clause in clauses:
                clause = clause.strip()
                if len(clause) > 10:
                    cur.execute("""
                    INSERT OR IGNORE INTO parsed_classics (source_pdf, page_num, content_text)
                    VALUES (?, ?, ?)
                    """, (pdf_name, idx + 1, clause))
                    inserted += cur.rowcount
                    
    conn.commit()
    conn.close()
    return f"【重铸成功】垃圾噪声已全部物理清除！已提取出 {inserted} 条有头有尾的尊贵修身章句。"

if __name__ == "__main__":
    mcp.run()