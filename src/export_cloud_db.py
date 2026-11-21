# -*- coding: utf-8 -*-
"""
export_cloud_db.py — 导出云端用的精简数据库
只保留新系统需要的表 + 最近N个交易日行情，体积从993MB压到约100-200MB。
用法: python export_cloud_db.py [--days 250] [--out cloud.db]
"""
import os, sys, sqlite3, argparse, shutil

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
import paths

# 云端需要的表（完整保留）
KEEP_FULL = [
    'heat_rank_daily', 'ths_concept_rank_daily', 'limit_up_day_v2',
    'tag_concept', 'tag_stat_daily', 'heat_hist_daily',
    'limit_up_pool_daily', 'preselect_pool_daily', 'score_v2_daily',
]
# 云端需要的表（按天数裁剪）
KEEP_DAYS = {'quote_clean': 'trade_date', 'daily_quote_src': 'trade_date'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=250, help='行情保留天数（默认250交易日≈1年）')
    ap.add_argument('--out', default=os.path.join(paths.OUT, 'cloud_quant.db'))
    a = ap.parse_args()

    src = sqlite3.connect(paths.DB)
    if os.path.exists(a.out):
        os.remove(a.out)
    dst = sqlite3.connect(a.out)

    # 取行情截止日
    max_d = src.execute("SELECT MAX(trade_date) FROM quote_clean").fetchone()[0]
    dates = [r[0] for r in src.execute(
        "SELECT DISTINCT trade_date FROM quote_clean WHERE trade_date<=? ORDER BY trade_date DESC LIMIT ?",
        (max_d, a.days))]
    min_d = min(dates) if dates else max_d
    print(f'行情范围: {min_d} ~ {max_d} ({len(dates)}个交易日)')

    # 全量保留的表
    for t in KEEP_FULL:
        try:
            ddl = src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
            if not ddl:
                print(f'  跳过 {t}（不存在）')
                continue
            dst.execute(ddl[0])
            rows = src.execute(f'SELECT * FROM "{t}"').fetchall()
            if rows:
                ph = ','.join('?' * len(rows[0]))
                dst.executemany(f'INSERT INTO "{t}" VALUES ({ph})', rows)
            print(f'  {t}: {len(rows)}行')
        except Exception as e:
            print(f'  {t} 失败: {e}')

    # 裁剪的表
    for t, datecol in KEEP_DAYS.items():
        try:
            ddl = src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
            if not ddl:
                continue
            dst.execute(ddl[0])
            cur = src.execute(f'SELECT * FROM "{t}" WHERE {datecol}>=?', (min_d,))
            rows = cur.fetchall()
            if rows:
                ph = ','.join('?' * len(rows[0]))
                dst.executemany(f'INSERT INTO "{t}" VALUES ({ph})', rows)
            print(f'  {t}: {len(rows)}行（{min_d}起）')
        except Exception as e:
            print(f'  {t} 失败: {e}')

    # 复制索引
    for (idx_sql,) in src.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL").fetchall():
        try:
            dst.execute(idx_sql)
        except Exception:
            pass

    dst.commit()
    dst.execute('VACUUM')
    dst.close()
    src.close()

    sz = os.path.getsize(a.out) / 1024 / 1024
    print(f'\n导出完成: {a.out}')
    print(f'大小: {sz:.0f} MB')


if __name__ == '__main__':
    main()
