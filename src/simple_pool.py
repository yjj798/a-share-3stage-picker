# -*- coding: utf-8 -*-
"""
simple_pool.py — 三阶段选股系统主入口（只做编排）

流程: 入池 → 逐只评分 → 排序 → 渲染页面

数据层:  datasource.py   （概念/实时价）
计算层:  scoring.py      （三阶段识别 + 三维评分）
展示层:  template.py     （HTML模板）
"""
import os
import sys
import json
import argparse
import sqlite3
import datetime

import paths
import scoring
from scoring import (find_launch, tech_score_calc, heat_score_calc,
                     concept_bonus_calc, calc_ma, is_generic)
from datasource import load_concept_heat, load_concepts, fetch_realtime, latest_date
from template import HTML_TPL

DB = paths.DB
OUT = os.path.join(paths.OUT, '简版股票池.html')


def score_one(con, code, date, d0, concept_heat=None, concepts=None, heat_end=None):
    """返回一只票的完整数据。heat_end: 热度取到哪天(用热表最新日，不截到行情日)"""
    # 价格/名称
    q = con.execute("""
        SELECT name, close, pct_recalc, board, is_st, is_bj
        FROM quote_clean WHERE code=? AND trade_date=?
    """, (code, date)).fetchone()
    if not q:
        return None
    name, close, pct, board, is_st, is_bj = q
    # 日K
    rows = con.execute("""
        SELECT trade_date, open, high, low, close, volume, pct_recalc
        FROM quote_clean WHERE code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 60
    """, (code, date)).fetchall()[::-1]
    if len(rows) < 30:
        return None
    # 热度（取到热表最新日 heat_end，不截到行情日——显示"当前热度"）
    he = heat_end or date
    hrows = con.execute("""
        SELECT trade_date, rank FROM heat_rank_daily
        WHERE code=? AND trade_date<=? AND trade_date>=?
        ORDER BY trade_date
    """, (code, he, d0)).fetchall()
    heat_ranks = [(r[0], r[1]) for r in hrows if r[1]]
    # 共振算"热度同比"：用同一天口径。热度序列里≤行情日的部分 vs 股价近端
    aligned_heat = [(d, rk) for d, rk in heat_ranks if d <= date]
    price_tail = [r[4] or 0 for r in rows[-20:]]
    # 分数/共振用对齐热度算，显示排名用最新热度
    hs, _, hlow, hdet = heat_score_calc(aligned_heat if len(aligned_heat) >= 3 else heat_ranks, price_tail)
    hrank = heat_ranks[-1][1] if heat_ranks else None  # 显示最新排名
    # 技术
    li = find_launch(rows)
    if li is None:
        ts = int(hs * 0.3)  # 无技术信号，热度打折
        tdet = []
        launch_date = None
    else:
        ts, _, _, tdet = tech_score_calc(rows, li)
        launch_date = str(rows[li][0])
    # 概念热度加成（小众概念爆发 0~20）
    cbonus, ctag = concept_bonus_calc(concepts, concept_heat or {})
    # 总分 = 技术 + 热榜 + 概念，三维；其余维度一律不要
    total = ts + hs + cbonus

    # 日K技术数据（最近20天：收盘/MA5/MA10/MA20/MA60/量/建仓标志）
    # 20天足够看清均线形态与建仓点，页面体积只有30天时的 2/3
    closes_all = [r[4] or 0 for r in rows]
    kline = []
    for j in range(max(0, len(rows) - 20), len(rows)):
        r = rows[j]
        ma5 = calc_ma(closes_all[:j + 1], 5)
        ma10 = calc_ma(closes_all[:j + 1], 10)
        ma20 = calc_ma(closes_all[:j + 1], 20)
        ma60 = calc_ma(closes_all[:j + 1], 60)
        is_launch = (launch_date is not None and str(r[0]) == launch_date)
        chg = r[6] or 0
        kline.append([
            str(r[0]), round(r[4] or 0, 2), round(chg, 1),
            round(ma5, 2) if ma5 else None,
            round(ma10, 2) if ma10 else None,
            round(ma20, 2) if ma20 else None,
            round(ma60, 2) if ma60 else None,
            round((r[5] or 0) / 1e4, 0),
            1 if is_launch else 0,
        ])

    # 建仓距今天数（交易日数）
    launch_days = None
    if launch_date:
        # 用完整 rows 计算（kline只含最近30天，建仓日可能更早）
        for rd_idx, rr in enumerate(rows):
            if str(rr[0]) == launch_date:
                launch_days = len(rows) - 1 - rd_idx
                break

    mc = 17 if code.startswith(('6', '9')) else 33
    return {
        'code': code, 'name': name, 'mc': mc, 'close': close, 'pct': pct or 0,
        'total': total, 'tech': ts, 'heat': hs, 'cbonus': cbonus, 'ctag': ctag,
        'hrank': hrank, 'hlow': hlow,
        'launch': launch_date, 'launch_days': launch_days,
        'tdet': ' '.join(tdet), 'hdet': ' '.join(hdet),
        'heat_series': [[r[0], r[1]] for r in heat_ranks[-20:]],   # [日期, 排名] 只要20日，够看够打分
        'kline': kline,
    }





def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None, help='基准日，默认取quote_clean最新交易日')
    a = ap.parse_args()

    con = sqlite3.connect(DB)

    # ── 数据对齐：以行情日为准，各源取≤基准日的最新日 ──
    quote_d = latest_date(con, 'quote_clean')
    date = a.date or quote_d
    # 技术/行情用基准日；热度用热表最新日（显示"当前热度"，跟同花顺一致）
    heat_d = latest_date(con, 'heat_rank_daily')   # 不截断，取绝对最新
    stat_d = latest_date(con, 'tag_stat_daily', "WHERE trade_date<='%s' AND tag_kind='concept'" % date)
    reason_d = latest_date(con, 'limit_up_day_v2', "WHERE trade_date<='%s' AND reason_raw IS NOT NULL" % date)

    print("[数据对齐] 行情%s | 热度%s | 概念热度%s | 涨停原因%s" % (date, heat_d, stat_d, reason_d))
    # 新鲜度警告：超过2个交易日未更新就提示
    for nm, d in [("热度", heat_d), ("概念热度", stat_d), ("涨停原因", reason_d)]:
        if d and d < date:
            lag = (datetime.datetime.strptime(date, '%Y%m%d') - datetime.datetime.strptime(d, '%Y%m%d')).days
            if lag > 4:
                print("  ⚠ %s落后%d天，可能评分偏差" % (nm, lag))

    d0 = (datetime.datetime.strptime(date, '%Y%m%d') - datetime.timedelta(days=45)).strftime('%Y%m%d')
    d20 = (datetime.datetime.strptime(date, '%Y%m%d') - datetime.timedelta(days=20)).strftime('%Y%m%d')

    # 入池：股价2-8 主板/创业板 非ST 非北交 + 近20天进过热度前3000（真有人气）
    # 诊断：逐条件打印命中数，便于定位云端0只卡在哪一步
    base_n = con.execute("SELECT COUNT(*) FROM quote_clean WHERE trade_date=?", (date,)).fetchone()[0]
    board_n = con.execute("SELECT COUNT(*) FROM quote_clean WHERE trade_date=? AND board IN ('主板','创业板')", (date,)).fetchone()[0]
    price_n = con.execute("SELECT COUNT(*) FROM quote_clean WHERE trade_date=? AND close>=2.0 AND close<8.0", (date,)).fetchone()[0]
    flag_n = con.execute("SELECT COUNT(*) FROM quote_clean WHERE trade_date=? AND is_st=0 AND is_bj=0", (date,)).fetchone()[0]
    heat_n = con.execute("SELECT COUNT(DISTINCT code) FROM heat_rank_daily WHERE trade_date>=? AND trade_date<=? AND rank<=3000", (d20, heat_d or date)).fetchone()[0]
    print(f"[入池诊断] 基准日{date}: 行情{base_n} 板块{board_n} 价格2-8:{price_n} 非ST非BJ:{flag_n} 热度前3000:{heat_n} (d20={d20},heat={heat_d})")
    pool = con.execute("""
        SELECT q.code FROM quote_clean q
        WHERE q.trade_date=? AND q.board IN ('主板','创业板')
          AND q.close>=2.0 AND q.close<8.0 AND q.is_st=0 AND q.is_bj=0
          AND EXISTS(SELECT 1 FROM heat_rank_daily h WHERE h.code=q.code
                     AND h.trade_date>=? AND h.trade_date<=? AND h.rank<=3000)
    """, (date, d20, heat_d or date)).fetchall()
    codes = [r[0] for r in pool]
    print(f"[池] {len(codes)}只")

    concepts = load_concepts(con, date)
    # 概念共振用涨停原因日（与概念标签同源）
    concept_heat = load_concept_heat(con)
    print(f"[概念热度] 官方概念榜{len(concept_heat)}个(快照存于ths_concept_rank_daily)")
    results = []
    for i, code in enumerate(codes):
        cs = concepts.get(code, [])
        r = score_one(con, code, date, d0, concept_heat, cs, heat_d or date)
        if r:
            r['concepts'] = cs
            results.append(r)
        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{len(codes)}")
    con.close()

    # 用腾讯实时价替换显示的股价/涨跌幅（评分仍用收盘价，不受影响）
    rt = fetch_realtime([r['code'] for r in results])
    rt_time = ''
    for r in results:
        q = rt.get(r['code'])
        if q:
            r['close'] = q['price']
            r['pct'] = q['pct']
            r['rt_time'] = q['time']
            if not rt_time:
                rt_time = q['time']
    print(f"[实时价] 更新{len(rt)}只, 快照时间{rt_time[:12] if rt_time else '无'}")

    results.sort(key=lambda x: -x['total'])
    print(f"[完成] {len(results)}只, ≥90: {sum(1 for x in results if x['total']>=90)}")

    # 生成HTML
    rows_json = json.dumps(results, ensure_ascii=False)
    fresh = "行情%s" % date
    fresh += " · 热度%s" % (heat_d or "无")
    fresh += " · 概念%s" % (stat_d or "无")
    if rt_time:
        fresh += " · 实时价%s" % rt_time[8:14]
    html = (HTML_TPL.replace('__DATA__', rows_json)
                    .replace('__DATE__', date)
                    .replace('id="fresh"></span>', 'id="fresh">%s</span>' % fresh))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"[输出] {OUT}")
    # 旧入口 股票池.html：改用硬链接（两个文件名指向同一份数据，不多占 2MB 磁盘）
    legacy = os.path.join(os.path.dirname(OUT), '股票池.html')
    try:
        if os.path.exists(legacy):
            os.remove(legacy)
        os.link(OUT, legacy)
        print(f"[输出] {legacy}（硬链接到简版，不占额外空间）")
    except Exception:
        import shutil
        try:
            shutil.copyfile(OUT, legacy)
            print(f"[输出] {legacy}（硬链接不可用，已复制）")
        except Exception as e2:
            print(f"[警告] 写股票池.html失败: {e2}")


if __name__ == '__main__':
    main()
