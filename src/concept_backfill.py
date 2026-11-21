# -*- coding: utf-8 -*-
"""
concept_backfill.py — 给池内股票补齐 F10 概念（含「走势最相关」标记）
数据源: basic.10jqka.com.cn/fuyao/f10_stock_index/concept/v1/stock_concept_list
      无需 cookie，返回该股全部概念 + tags(含「走势最相关」) + fit_rank
写回 cache/f10_concepts.json，格式 {code: [[name, flag], ...]} flag=1=走势最相关
"""
import sys, os, json, time, sqlite3, datetime
import urllib.request
from concurrent.futures import ThreadPoolExecutor

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
import paths

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
API = ('https://basic.10jqka.com.cn/fuyao/f10_stock_index/concept/v1/'
       'stock_concept_list?market_id=%d&code=%s')


def market_id(code):
    if code.startswith(('6', '9')):
        return 17
    if code.startswith(('4', '8')):
        return 151
    return 33


def fetch_one(code):
    """返回 [[概念名, flag], ...]，flag=1 表示走势最相关。失败返回 None"""
    url = API % (market_id(code), code)
    time.sleep(0.10)   # 错峰
    for attempt in range(2):
        try:
            req = urllib.request.Request(url)
            req.add_header('User-Agent', UA)
            req.add_header('Accept', 'application/json')
            d = json.loads(urllib.request.urlopen(req, timeout=12).read().decode('utf-8', 'ignore'))
            items = d.get('data') or []
            out = []
            for it in items:
                name = it.get('name')
                if not name:
                    continue
                tags = it.get('tags') or []
                flag = 1 if '走势最相关' in tags else 0
                out.append([name, flag])
            return out if out else None
        except Exception:
            if attempt == 0:
                time.sleep(1.0)
    return None


def get_pool(con):
    maxd = con.execute("SELECT MAX(trade_date) FROM quote_clean").fetchone()[0]
    d20 = (datetime.datetime.now() - datetime.timedelta(days=20)).strftime('%Y%m%d')
    return maxd, [r[0] for r in con.execute("""
        SELECT DISTINCT q.code FROM quote_clean q
        WHERE q.trade_date=? AND q.board IN ('主板','创业板')
          AND q.close>=2.0 AND q.close<8.0 AND q.is_st=0 AND q.is_bj=0
          AND EXISTS(SELECT 1 FROM heat_rank_daily h WHERE h.code=q.code
                     AND h.trade_date>=? AND h.rank<=3000)
    """, (maxd, d20))]


def main():
    force = '--all' in sys.argv
    con = sqlite3.connect(paths.DB)
    maxd, pool = get_pool(con)
    con.close()
    print(f'池内 {len(pool)} 只')

    # 读现有缓存
    cache = {}
    meta = {'date': maxd, 'built': '', 'src': '10jqka f10 stock_concept_list', 'data': {}}
    try:
        old = json.load(open(paths.CONCEPT_CACHE, encoding='utf-8'))
        cache = old.get('data') or {}
        meta.update({k: v for k, v in old.items() if k != 'data'})
    except Exception:
        pass
    print(f'现有缓存 {len(cache)} 只')

    # 只补缓存里没有的（--all 则全刷）
    todo = pool if force else [c for c in pool if c not in cache]
    print(f'需抓取 {len(todo)} 只')
    if not todo:
        print('概念已齐全，无需补')
        return

    t0 = time.time()
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=3) as ex:
        for i, (code, res) in enumerate(zip(todo, ex.map(fetch_one, todo))):
            if res:
                cache[code] = res
                ok += 1
            else:
                fail += 1
            if (i + 1) % 50 == 0:
                print(f'  {i+1}/{len(todo)} | 成功{ok} 失败{fail} | {time.time()-t0:.0f}s', flush=True)
                # 中途落盘防丢
                meta['data'] = cache
                meta['built'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                with open(paths.CONCEPT_CACHE, 'w', encoding='utf-8') as f:
                    json.dump(meta, f, ensure_ascii=False)

    meta['data'] = cache
    meta['date'] = maxd
    meta['built'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    os.makedirs(os.path.dirname(paths.CONCEPT_CACHE), exist_ok=True)
    with open(paths.CONCEPT_CACHE, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False)
    print(f'完成: 成功{ok} 失败{fail} | 缓存共{len(cache)}只 | {time.time()-t0:.0f}s')


if __name__ == '__main__':
    main()
