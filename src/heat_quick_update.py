# -*- coding: utf-8 -*-
"""
热度快速更新（每小时一次，安全限速）
只拉当天同花顺热度排名写 heat_rank_daily。跨平台。
"""
import sys, io, os, json, time, sqlite3, datetime
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


def get_pool_codes(con):
    """基础池：股价2-8/主板创业板/非ST非北交（不要求热度，让评分层再去筛人气）
    这样池子定义稳定，不会因为热度数据变化而抖动"""
    try:
        rows = con.execute("""
            SELECT DISTINCT q.code FROM quote_clean q
            WHERE q.board IN ('主板','创业板') AND q.close>=2.0 AND q.close<8.0
              AND q.is_st=0 AND q.is_bj=0
              AND q.trade_date=(SELECT MAX(trade_date) FROM quote_clean)
        """).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


def fetch_today(opener, codes):
    now = datetime.datetime.now()
    start = int((now - datetime.timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    end = int((now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    out = {}
    from concurrent.futures import ThreadPoolExecutor
    def one(code):
        body = {
            "indexes": [{"codes": ["%d:%s" % (market_code(code), code)],
                         "index_info": [{"index_id": "ths-hot-data-day-rank"}]}],
            "time_range": {"time_type": "NATURAL_DAILY", "start": start, "end": end},
        }
        time.sleep(0.15)   # 错峰
        for attempt in range(2):
            try:
                req = urllib.request.Request(BASE, data=json.dumps(body).encode('utf-8'),
                                             headers=HEADERS, method='POST')
                resp = opener.open(req, timeout=10)
                d = json.loads(resp.read().decode('utf-8', 'ignore'))
                rows = (d.get('data') or {}).get('data') or []
                tr = (d.get('data') or {}).get('time_range') or []
                if not rows or not tr:
                    return code, None
                vals = rows[0]['values'][0]['values']
                if not vals:
                    return code, None
                last = vals[-1]
                if isinstance(last, int) and last > 0:
                    return code, (datetime.datetime.fromtimestamp(int(tr[-1])).strftime('%Y%m%d'), last)
            except Exception:
                if attempt == 0:
                    time.sleep(1.0)
        return code, None
    with ThreadPoolExecutor(max_workers=3) as ex:
        for i, (code, res) in enumerate(ex.map(one, codes)):
            if res:
                out[code] = res
            if (i + 1) % 30 == 0:
                time.sleep(1.5)
    return out


def fetch_hotlist_fallback(code_set):
    """降级源：公开热榜(无需cookie)，只能拿top100的排名。
    同花顺cookie失效时用这个保住基本功能。"""
    url = ('https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock'
           '?list_type=normal&stock_type=a&type=hour')
    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', UA)
        d = json.loads(urllib.request.urlopen(req, timeout=10).read().decode('utf-8'))
        lst = (d.get('data') or {}).get('stock_list') or []
        today = datetime.datetime.now().strftime('%Y%m%d')
        out = {}
        for idx, s in enumerate(lst, 1):
            c = str(s.get('code') or '')
            if c in code_set:
                out[c] = (today, idx)
        return out
    except Exception as e:
        print(f'[热度] 降级源也失败: {e}')
        return {}


def main():
    con = sqlite3.connect(paths.DB)
    codes = get_pool_codes(con)
    con.close()
    if not codes:
        print('[热度] 池内无代码，跳过')
        return
    t0 = time.time()
    res = fetch_today(make_opener(), codes)
    src_tag = 'ths_hist_api90'
    # 拿到的太少（cookie失效/限流）→ 走公开热榜降级
    if len(res) < len(codes) * 0.3:
        print(f'[热度] 主源只拿到{len(res)}/{len(codes)}，启用热榜降级源')
        fb = fetch_hotlist_fallback(set(codes))
        if len(fb) > len(res):
            res = fb
            src_tag = 'ths_hotlist'
    print(f'[热度] {len(res)}/{len(codes)}只, {time.time()-t0:.1f}s, 源={src_tag}')
    if not res:
        return
    con = sqlite3.connect(paths.DB)
    cur = con.cursor()
    for code, (dt, rank) in res.items():
        cur.execute("""INSERT OR REPLACE INTO heat_rank_daily
            (trade_date,code,name,rank,src,src_date,aligned_date,align_rule,synced_at)
            VALUES (?,?,?,?,?,?,?,?,datetime('now'))""",
            (dt, code, '', rank, src_tag, dt, dt, 'identity'))
    con.commit()
    con.close()
    print(f'[完成] {len(res)}条, {time.time()-t0:.1f}s')


if __name__ == '__main__':
    main()
