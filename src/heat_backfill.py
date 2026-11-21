# -*- coding: utf-8 -*-
"""
heat_backfill.py — 补全池内股票的90天热度历史
问题背景(2026-11-21)：上次批量拉取只覆盖当时池子的612只，后续新入池的票
（如四川长虹600839）没有历史，热榜页显示大量空缺。
本脚本动态取当前池，只补记录数不足的票，安全限速。
"""
import sys, os, json, time, sqlite3, datetime
import urllib.request, http.cookiejar

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
import paths

BASE = 'https://dataq.10jqka.com.cn/fetch-data-server/fetch/v1/interval_data'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36'
HEADERS = {
    'Content-Type': 'application/json', 'Platform': 'mobileweb',
    'Source-Id': 'ths-hot-detail', 'Origin': 'https://ms.10jqka.com.cn',
    'Referer': 'https://ms.10jqka.com.cn/thsHotDetail/', 'User-Agent': UA,
}


def make_opener():
    cj = http.cookiejar.MozillaCookieJar()
    jar = paths.COOKIE_JAR
    if os.path.isfile(jar):
        for enc in ('utf-8-sig', 'latin-1'):
            try:
                with open(jar, encoding=enc) as f:
                    cj._really_load(f, jar, ignore_discard=True, ignore_expires=True)
                break
            except Exception:
                continue
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def market_code(code):
    return 17 if code.startswith(('6', '9')) else 33


def fetch_heat(opener, code, days=90):
    now = datetime.datetime.now()
    start = int((now - datetime.timedelta(days=days)).replace(hour=0, minute=0, second=0).timestamp())
    end = int((now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0).timestamp())
    body = {
        "indexes": [{"codes": ["%d:%s" % (market_code(code), code)],
                     "index_info": [{"index_id": "ths-hot-data-day-rank"}]}],
        "time_range": {"time_type": "NATURAL_DAILY", "start": start, "end": end},
    }
    time.sleep(0.12)   # 错峰
    for attempt in range(2):
        try:
            req = urllib.request.Request(BASE, data=json.dumps(body).encode('utf-8'),
                                         headers=HEADERS, method='POST')
            d = json.loads(opener.open(req, timeout=15).read().decode('utf-8', 'ignore'))
            data = d.get('data') or {}
            tr = data.get('time_range') or []
            rows = data.get('data') or []
            if not tr or not rows:
                return []
            vals = rows[0]['values'][0]['values']
            out = []
            for t, v in zip(tr, vals):
                if isinstance(v, int) and v > 0:
                    out.append((datetime.datetime.fromtimestamp(int(t)).strftime('%Y%m%d'), v))
            return out[-days:]
        except Exception:
            if attempt == 0:
                time.sleep(1.0)
    return []


def main():
    need_all = '--all' in sys.argv
    min_days = 15
    con = sqlite3.connect(paths.DB)
    d90 = (datetime.datetime.now() - datetime.timedelta(days=90)).strftime('%Y%m%d')

    # 当前池 + 各自近期记录数
    pool = [r[0] for r in con.execute("""
        SELECT DISTINCT q.code FROM quote_clean q
        WHERE q.board IN ('主板','创业板') AND q.close>=2.0 AND q.close<8.0
          AND q.is_st=0 AND q.is_bj=0
          AND q.trade_date=(SELECT MAX(trade_date) FROM quote_clean)
    """)]
    have = dict(con.execute("""
        SELECT code, COUNT(*) FROM heat_rank_daily
        WHERE trade_date>=? GROUP BY code
    """, (d90,)).fetchall())

    todo = pool if need_all else [c for c in pool if have.get(c, 0) < min_days]
    print(f'池内 {len(pool)} 只，历史不足{min_days}天的 {len(todo)} 只')
    if not todo:
        print('无需补全')
        con.close()
        return

    opener = make_opener()
    t0 = time.time()
    cur = con.cursor()
    ok = nodata = 0
    for i, code in enumerate(todo):
        rows = fetch_heat(opener, code, 90)
        if rows:
            for dt, rk in rows:
                cur.execute("""INSERT OR REPLACE INTO heat_rank_daily
                    (trade_date,code,name,rank,src,src_date,aligned_date,align_rule,synced_at)
                    VALUES (?,?,?,?,?,?,?,?,datetime('now'))""",
                    (dt, code, '', rk, 'ths_hist_api90', dt, dt, 'identity'))
            ok += 1
        else:
            nodata += 1
        if (i + 1) % 30 == 0:
            con.commit()
            print(f'  {i+1}/{len(todo)} | 补到{ok}只 无数据{nodata}只 | {time.time()-t0:.0f}s', flush=True)
    con.commit()
    con.close()
    print(f'完成: 补到{ok}只 无数据{nodata}只 | {time.time()-t0:.0f}s')


if __name__ == '__main__':
    main()
