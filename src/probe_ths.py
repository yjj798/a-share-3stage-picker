# -*- coding: utf-8 -*-
"""probe_ths.py — 云端探测同花顺各接口在本环境的可用性（纯诊断，不改库）。"""
import json
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (compatible; stockpool-ci/1.0)"}

URLS = [
    # 热榜（已知可用）—— 打印原始字段，看有无价格/名称
    ("热榜", "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock?stock_type=a&type=hour&list_type=normal",
     {**UA, "Referer": "https://data.10jqka.com.cn/"}),
    # K线候选
    ("K线-01.js", "https://d.10jqka.com.cn/v6/line/hs_000001/01.js",
     {**UA, "Referer": "https://stockpage.10jqka.com.cn/"}),
    ("K线-last.js", "https://d.10jqka.com.cn/v6/line/hs_000001/01/last.js",
     {**UA, "Referer": "https://stockpage.10jqka.com.cn/"}),
    ("K线-HTTP", "http://d.10jqka.com.cn/v6/line/hs_000001/01.js",
     {**UA, "Referer": "https://stockpage.10jqka.com.cn/"}),
    ("K线-stockpage", "https://stockpage.10jqka.com.cn/ajax/realstock/company/sh600000/hisdata/klc_kl.js",
     {**UA, "Referer": "https://stockpage.10jqka.com.cn/"}),
    ("实时-stockinfo", "https://d.10jqka.com.cn/v2/stockinfo/hs_000001.js",
     {**UA, "Referer": "https://stockpage.10jqka.com.cn/"}),
    # 全市场/列表候选（data.10jqka 域名，概念榜已验证可用）
    ("涨停池", "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool?page=1&limit=200&field=199112,10,9001,330323,330324&order_field=330324&order_type=0",
     {**UA, "Referer": "https://data.10jqka.com.cn/"}),
    ("连板池", "https://data.10jqka.com.cn/dataapi/limit_up/continuous_limit_up?page=1&limit=200",
     {**UA, "Referer": "https://data.10jqka.com.cn/"}),
    ("行情中心", "https://q.10jqka.com.cn/index/index/board/all/field/code/order/asc/page/1/ajax/1/",
     {**UA, "Referer": "https://q.10jqka.com.cn/"}),
]


def main():
    for name, url, headers in URLS:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                b = r.read()
            txt = b.decode("utf-8", "ignore")
            head = txt[:280].replace("\n", " ").replace("\r", " ")
            print(f"[PROBE] {name}: HTTP 200, {len(b)} 字节 | {head}", flush=True)
            if name == "热榜":
                try:
                    rows = (json.loads(txt).get("data") or {}).get("stock_list") or []
                    if rows:
                        print(f"[PROBE] 热榜首条字段: {rows[0]}", flush=True)
                except Exception as e:
                    print(f"[PROBE] 热榜解析失败: {e}", flush=True)
        except Exception as e:
            print(f"[PROBE] {name}: 失败 {type(e).__name__}: {str(e)[:80]}", flush=True)


if __name__ == "__main__":
    main()
