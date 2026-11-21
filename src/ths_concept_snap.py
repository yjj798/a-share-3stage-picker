# -*- coding: utf-8 -*-
"""同花顺概念榜快照抓取（跨平台，直接用官方数据不自己算）"""
import sys, io, os, json, sqlite3, datetime, urllib.request

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
import paths

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
API = ('https://data.10jqka.com.cn/dataapi/limit_up/block_top'
       '?filter=HS,GEM2STAR&page=1&field=199112&order_field=199112'
       '&order_type=0&offset=0&limit=100')


def ensure_table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS ths_concept_rank_daily (
        snap_time TEXT NOT NULL, trade_date TEXT NOT NULL, rank INTEGER NOT NULL,
        concept_code TEXT NOT NULL, concept_name TEXT NOT NULL,
        change_pct REAL, limit_up_num INTEGER, continuous_plate INTEGER,
        high_desc TEXT, days INTEGER, stock_list TEXT,
        PRIMARY KEY (snap_time, concept_code))""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ts_crd_date ON ths_concept_rank_daily(trade_date)")
    con.commit()


def main():
    req = urllib.request.Request(API)
    req.add_header('User-Agent', UA)
    req.add_header('Referer', 'https://data.10jqka.com.cn/')
    rows = json.loads(urllib.request.urlopen(req, timeout=10).read().decode('utf-8')).get('data') or []
    if not rows:
        print('概念榜无数据')
        return
    now = datetime.datetime.now()
    snap = now.strftime('%Y-%m-%d %H:%M:%S')
    td = now.strftime('%Y%m%d')
    con = sqlite3.connect(paths.DB)
    ensure_table(con)
    for idx, r in enumerate(rows, 1):
        stocks = r.get('stock_list') or []
        con.execute("""INSERT OR REPLACE INTO ths_concept_rank_daily
            (snap_time,trade_date,rank,concept_code,concept_name,change_pct,
             limit_up_num,continuous_plate,high_desc,days,stock_list)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (snap, td, idx, str(r.get('code') or ''), str(r.get('name') or ''),
             float(r.get('change') or 0), int(r.get('limit_up_num') or 0),
             int(r.get('continuous_plate_num') or 0), str(r.get('high') or ''),
             int(r.get('days') or 0),
             json.dumps([{'code': s.get('code'), 'name': s.get('name')} for s in stocks[:10]], ensure_ascii=False)))
    con.commit()
    print(f'[{snap}] 存{len(rows)}个概念')
    con.close()


if __name__ == '__main__':
    main()
