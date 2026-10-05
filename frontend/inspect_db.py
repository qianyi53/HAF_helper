# -*- coding: utf-8 -*-
"""Inspect a SQLite database: tables, DDL, row counts, samples."""
import os
import sqlite3
import sys

_DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),  # 项目根 = frontend/ 上级
    "data", "db", "haf.db",
)
DB = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_DB

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

tables = [r[0] for r in cur.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
views = [r[0] for r in cur.execute(
    "SELECT name FROM sqlite_master WHERE type='view' AND name NOT LIKE 'sqlite_%' ORDER BY name")]

print(f"DB: {DB}")
print(f"TABLES ({len(tables)}): {tables}")
print(f"VIEWS ({len(views)}): {views}")
print()

for t in tables:
    row = cur.execute("SELECT sql FROM sqlite_master WHERE name=?", (t,)).fetchone()
    ddl = row[0] if row else "(no ddl)"
    cnt = cur.execute('SELECT COUNT(*) FROM "{}"'.format(t)).fetchone()[0]
    info = list(cur.execute('PRAGMA table_info("{}")'.format(t)))
    cols = [(r[1], r[2], "PK" if r[5] else "", "NN" if r[3] else "") for r in info]
    print("=" * 70)
    print(f"TABLE {t}  rows={cnt}")
    for c in cols:
        print(f"  - {c[0]:<30} {c[1]:<12} {c[2]} {c[3]}")
    print(f"DDL: {ddl}")
    try:
        for r in cur.execute('SELECT * FROM "{}" LIMIT 2'.format(t)):
            d = dict(r)
            for k, v in d.items():
                if isinstance(v, str) and len(v) > 120:
                    d[k] = v[:120] + "..."
                if isinstance(v, bytes):
                    d[k] = f"<blob {len(v)} bytes>"
            print("  sample:", d)
    except Exception as e:
        print("  sample error:", e)
    print()

# indexes
print("=" * 70)
print("INDEXES:")
for r in cur.execute("SELECT name, tbl_name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"):
    print(f"  {r[0]} on {r[1]}: {r[2]}")

con.close()
