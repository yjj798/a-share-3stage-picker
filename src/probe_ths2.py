# -*- coding: utf-8 -*-
"""probe_ths2.py — 深挖同花顺 K线 last.js 的数据结构 + 全市场候选接口。"""
import json
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (compatible; stockpool-ci/1.0)"}


def get(url, ref="https://stockpage.10jqka.com.cn/"):
    try:
        req = urllib.request.Request(url, headers={**UA, "Referer": ref})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read()
    except Exception as e:
        print(f"[P2] 失败 {url[:70]} -> {type(e).__name__}: {str(e)[:60]}", flush=True)
        return None


def main():
    # 1) last.js 完整结构
    b = get("https://d.10jqka.com.cn/v6/line/hs_000001/01/last.js")
    if b:
        txt = b.decode("utf-8", "ignore")
        print(f"[P2] last.js 长度 {len(txt)} | 尾部200: ...{txt[-200:]}", flush=True)
        # JSONP: xxx({....})
        i, j = txt.find("("), txt.rfind(")")
        if i >= 0 and j > i:
            try:
                d = json.loads(txt[i + 1:j])
                print(f"[P2] last.js keys: {list(d.keys())}", flush=True)
                data = d.get("data")
                if isinstance(data, str):
                    print(f"[P2] data(str) 长度 {len(data)} | 前200: {data[:200]}", flush=True)
                    print(f"[P2] data 尾部100: ...{data[-100:]}", flush=True)
                else:
                    print(f"[P2] data type={type(data).__name__} 前300: {str(data)[:300]}", flush=True)
                for k in ("price", "rt", "num", "total"):
                    print(f"[P2] {k} = {str(d.get(k))[:150]}", flush=True)
            except Exception as e:
                print(f"[P2] last.js JSON解析失败: {e}", flush=True)

    # 2) 更多日线接口候选
    cands = [
        ("hs_000001/01/last.js?_=1", "https://d.10jqka.com.cn/v6/line/hs_000001/01/last.js?_=1"),
        ("hs_600000/01/last.js", "https://d.10jqka.com.cn/v6/line/hs_600000/01/last.js"),
        ("hs_300750/01/last.js", "https://d.10jqka.com.cn/v6/line/hs_300750/01/last.js"),
        ("问财GET", "https://www.iwencai.com/unifiedwap/unified-wap/v2/result/get-robot-data?question=沪深A股"),
        ("涨停池全字段", "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool?page=1&limit=500&field=199112,10,9001,330323,330324,330325&order_field=330324&order_type=0"),
    ]
    for name, url in cands:
        b = get(url)
        if b:
            print(f"[P2] {name}: HTTP200 {len(b)}B | {b.decode('utf-8','ignore')[:160]}", flush=True)


if __name__ == "__main__":
    main()
