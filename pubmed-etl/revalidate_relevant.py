"""一次性脚本：删除旧 RELEVANT 判定，然后用新提示词重跑验证"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from config.settings import DB_PATH
import sqlite3

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

total = cur.execute('SELECT COUNT(1) FROM llm_validation').fetchone()[0]
relevant = cur.execute("SELECT COUNT(1) FROM llm_validation WHERE llm_verdict='RELEVANT'").fetchone()[0]
not_rel = cur.execute("SELECT COUNT(1) FROM llm_validation WHERE llm_verdict='NOT_RELEVANT'").fetchone()[0]

print(f'llm_validation 表总计: {total} 条')
to_delete = cur.execute(
    "SELECT COUNT(1) FROM llm_validation WHERE llm_verdict='RELEVANT' AND human_review IS NULL"
).fetchone()[0]
print(f'  RELEVANT: {relevant} 条（待复核 {to_delete} 条将被删除后重跑, '
      f'已复核 {relevant - to_delete} 条跳过）')
print(f'  NOT_RELEVANT: {not_rel} 条（保留）')

if to_delete == 0:
    print('没有需要重跑的 RELEVANT 待复核记录，退出')
    sys.exit(0)

print(f'\n正在删除 {to_delete} 条 RELEVANT 待复核记录...')
cur.execute("DELETE FROM llm_validation WHERE llm_verdict='RELEVANT' AND human_review IS NULL")
conn.commit()
print('删除完成')

remaining = cur.execute('SELECT COUNT(1) FROM llm_validation').fetchone()[0]
print(f'llm_validation 表现有 {remaining} 条（均为 NOT_RELEVANT）')
conn.close()
print('\n请运行: python main.py --step validate')
