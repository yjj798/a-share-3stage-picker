# -*- coding: utf-8 -*-
"""
精简版股票池生成器 v1
=====================
只做：股池 + 技术分 + 热度分 + 概念
前端：名称 代码 总评分 技术分 热度分 概念 热度柱状图

用法: python simple_pool.py --date 20261120
"""
import argparse
import json
import os
import sqlite3
import datetime
import re
import collections
import urllib.request
import html as htmlmod

HERE = os.path.dirname(os.path.abspath(__file__))
import paths                                    # 跨平台路径配置
DB = paths.DB
CONCEPT_CACHE = paths.CONCEPT_CACHE
OUT = os.path.join(paths.OUT, '简版股票池.html')


def calc_ma(closes, period):
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def heat_score_calc(heat_ranks, price_closes=None):
    """热度分（可负）：当前位置 + 持续关注 + 暴增 + 热度/股价共振
    共振：热度升+股价升=+10；热度降+股价降=-10；背离=0
    """
    if not heat_ranks or len(heat_ranks) < 3:
        return 0, None, None, []
    ranks = [r[1] for r in heat_ranks]
    latest = heat_ranks[-1][1]  # 当前=最新
    low = min(ranks)
    peak = max(ranks)
    s = 0
    det = []
    # 当前位置（0~20）—— 0不是下限，冷门/衰减要扣
    if latest <= 200: s += 20; det.append("到位20")
    elif latest <= 500: s += 12; det.append("接近12")
    elif latest <= 1000: s += 5; det.append("边缘5")
    elif latest <= 2000: s -= 5; det.append("冷门-5")
    elif latest <= 4000: s -= 10; det.append("沉寂-10")
    else: s -= 15; det.append("无人问津-15")
    # 持续关注（0~15 / 负）
    if low < 1200: s += 15; det.append("持续15")
    elif low < 2000: s += 8; det.append("偶关8")
    elif low < 4000: s -= 5; det.append("少人-5")
    else: s -= 10; det.append("无人-10")
    # 暴增（0~10）
    if peak >= 2000 and low <= 800: s += 10; det.append("暴增10")
    elif peak >= 1000 and low <= 500: s += 5; det.append("跃升5")
    # 热度衰减惩罚：从高位大幅回落的票（曾经热过、现在掉了）
    if len(ranks) >= 5:
        recent_peak = min(ranks[-10:])     # 近10日最好的排名
        # 曾进前100，现在掉出500 → 资金撤离
        if recent_peak <= 100 and latest > 500:
            s -= 10; det.append("热后坍塌-10")
        # 曾进前300，现在掉出1000 → 退潮
        elif recent_peak <= 300 and latest > 1000:
            s -= 8; det.append("退潮-8")
        elif recent_peak <= 600 and latest > 2000:
            s -= 6; det.append("降温-6")
    # 热度/股价共振（-10~+10）——核心新增
    if price_closes and len(price_closes) >= 6 and len(ranks) >= 6:
        # 热度方向：近5天排名变化（数字变小=热度上升）
        h_old = ranks[-6]
        h_new = ranks[-1]
        heat_up = (h_new < h_old)   # 排名变小=更热
        heat_dn = (h_new > h_old)
        # 股价方向：近5天收盘变化
        p_old = price_closes[-6]
        p_new = price_closes[-1]
        price_up = (p_new > p_old)
        price_dn = (p_new < p_old)
        # 共振判定（看幅度，避免弱市普跌一刀切）
        h_change = h_old - h_new   # 正数=热度上升名次
        p_change = (p_new - p_old) / p_old * 100 if p_old > 0 else 0  # 股价变化%
        if heat_up and price_up and h_change >= 30 and p_change >= 2:
            s += 10; det.append("热度股价同升+10")
        elif heat_dn and price_dn and h_change <= -100 and p_change <= -8:
            s -= 10; det.append("热度股价同降-10")
        elif heat_up and price_up:
            det.append("同升弱0")
        elif heat_dn and price_dn:
            det.append("同降弱0")
        elif heat_up and price_dn:
            det.append("热度升价降背离0")
        elif heat_dn and price_up:
            det.append("热度降价升背离0")
        else:
            det.append("共振平0")
    return s, latest, low, det


def tech_score_calc(rows, launch_idx):
    """技术分 0~100"""
    launch = rows[launch_idx]
    launch_low = launch[3]
    launch_high = launch[2]
    launch_vwap = (launch[1] + launch[2] + launch[3] + launch[4]) / 4
    after = rows[launch_idx + 1:]
    if not after:
        return 0, None, None, []
    # 破位：主力拉高出货，比没信号还烂 → 负分
    for r in after:
        if r[4] is not None and r[4] < launch_low * 0.97:
            return -20, None, None, ["破位-20"]
    closes = [r[4] or 0 for r in rows]
    ma5 = calc_ma(closes, 5)
    ma10 = calc_ma(closes, 10)
    ma20 = calc_ma(closes, 20)
    ma60 = calc_ma(closes, 60)
    today = rows[-1]
    tc, tv = today[4] or 0, today[5] or 0
    vs_sup = (tc - launch_low) / launch_low * 100 if launch_low > 0 else 0
    after_high = max(r[2] or 0 for r in after)
    target = max(launch_high or 0, after_high)
    gap = (target - tc) / target * 100 if target > 0 else 0
    pvols = [r[5] or 0 for r in after[-6:-1]]
    p5 = sum(pvols) / len(pvols) if pvols else 1
    vr = (tv or 0) / p5 if p5 > 0 else 0
    bull4 = bool(all([ma5, ma10, ma20, ma60]) and ma5 >= ma10 >= ma20 >= ma60)
    ma60_5 = calc_ma(closes[:-5], 60) if len(closes) >= 65 else None
    ma60_up = bool(ma60 and ma60_5 and ma60 >= ma60_5)
    chip_ok = False
    if launch_idx >= 20:
        pre20 = sum(closes[launch_idx - 20:launch_idx]) / 20
        chip_ok = tc > launch_vwap and launch_vwap > pre20
    mas = [v for v in (ma5, ma10, ma20, ma60) if v is not None]
    spread = (max(mas) - min(mas)) / sum(mas) * len(mas) * 100 if len(mas) >= 3 else 999

    s = 0
    det = []
    s += 20; det.append("不破20")
    if bull4: s += 15; det.append("多头15")
    elif ma5 and ma10 and ma5 >= ma10: s += 8; det.append("5>10 8")
    if spread < 3: s += 10; det.append("粘10")
    elif spread < 5: s += 7; det.append("粘7")
    elif spread < 8: s += 5; det.append("粘5")
    elif spread < 15: s += 3; det.append("粘3")
    if ma60_up: s += 10; det.append("MA60↑10")
    elif ma60 and ma60_5 and ma60 >= ma60_5 * 0.99: s += 5; det.append("MA60→5")
    if vr < 0.5: s += 15; det.append("缩15")
    elif vr < 0.8: s += 12; det.append("缩12")
    elif vr < 1.0: s += 8; det.append("缩8")
    elif vr < 1.5: s += 5; det.append("缩5")
    else: s += 2; det.append("缩2")
    if 0 <= vs_sup <= 5: s += 10; det.append("支10")
    elif 0 <= vs_sup <= 10: s += 7; det.append("支7")
    elif 0 <= vs_sup <= 15: s += 5; det.append("支5")
    elif vs_sup > 15: s += 2; det.append("支2")
    if chip_ok: s += 10; det.append("获利10")
    elif tc > launch_vwap: s += 5; det.append("获利5")
    if gap <= 3: s += 10; det.append("高10")
    elif gap <= 5: s += 7; det.append("高7")
    elif gap <= 10: s += 5; det.append("高5")
    else: s += 2; det.append("高2")
    return s, tc, target, det


def find_launch(rows):
    """找最近建仓信号"""
    for i in range(len(rows) - 1, 6, -1):
        d = rows[i]
        prev_c = rows[i - 1][4] if i > 0 else d[3]
        if not prev_c or not d[4] or not d[3]:
            continue
        chg = d[6] if d[6] is not None else ((d[4] / prev_c - 1) * 100 if prev_c > 0 else 0)
        vols = [rows[j][5] or 0 for j in range(max(0, i - 7), i)]
        prev7 = sum(vols) / len(vols) if vols else 0
        vr = (d[5] or 0) / prev7 if prev7 > 0 else 0
        limit = round(prev_c * 1.1, 2)
        yang = d[4] >= d[1]
        is_zt = chg >= 9.8
        is_zha = abs(d[2] - limit) < 0.03 and d[4] < limit * 0.99
        is_vol = chg >= 5 and vr >= 2 and yang
        if (is_zt or is_zha or is_vol) and yang:
            # 排除连板
            if any((rows[j][6] or 0) >= 9.8 for j in range(max(0, i - 3), i)):
                continue
            if i + 1 < len(rows) and (rows[i + 1][6] or 0) >= 9.8:
                continue
            return i
    return None


def load_concept_heat(con, date=None):
    """概念热度：直接用同花顺官方概念榜快照(ths_concept_rank_daily)，不自己算。
    返回 {概念名: {rank, change, limit_up_num, days, prev_rank}}
    prev_rank=上一次快照的排名(追踪热度变化：排名变小=升温)
    """
    board = {}
    try:
        snaps = [r[0] for r in con.execute(
            "SELECT DISTINCT snap_time FROM ths_concept_rank_daily ORDER BY snap_time DESC LIMIT 2")]
        if not snaps:
            return {}
        cur_snap = snaps[0]
        prev_snap = snaps[1] if len(snaps) > 1 else None
        # 上一次排名
        prev = {}
        if prev_snap:
            for r in con.execute(
                "SELECT concept_name,rank FROM ths_concept_rank_daily WHERE snap_time=?", (prev_snap,)):
                prev[r[0]] = r[1]
        for r in con.execute("""
            SELECT rank,concept_name,change_pct,limit_up_num,days
            FROM ths_concept_rank_daily WHERE snap_time=? ORDER BY rank
        """, (cur_snap,)):
            name = r[1]
            board[name] = {
                'rank': r[0], 'change': r[2] or 0,
                'lu': r[3] or 0, 'days': r[4] or 0,
                'prev_rank': prev.get(name),
            }
    except Exception:
        return {}
    return board


def concept_bonus_calc(concepts, stat):
    """返回(加分, 概念明细)。用官方榜：排名越靠前/涨停越多/持续越久=分越高。
    升温(排名比上次前进)额外加分。"""
    best, taginfo = 0, None
    for c in (concepts or []):
        if is_generic(c):
            continue
        info = stat.get(c)
        # 模糊匹配
        if not info:
            for bn, v in stat.items():
                if (c in bn or bn in c) and len(c) >= 2 and len(bn) >= 2:
                    info = v
                    c = bn
                    break
        if not info:
            continue
        # 官方榜加分：涨停家数为主 + 排名靠前 + 持续天数
        score = min(10, info['lu'] * 2)          # 涨停数(0~10)
        score += max(0, 5 - info['rank'] // 4)   # 前20名加分(0~5)
        score += min(5, info['days'])            # 持续天数(0~5)
        # 升温加分：排名前进
        if info.get('prev_rank') and info['prev_rank'] > info['rank']:
            score += 3
            trend = '↑'
        elif info.get('prev_rank') and info['prev_rank'] < info['rank']:
            score -= 2
            trend = '↓'
        else:
            trend = '→'
        score = max(0, min(20, score))
        if score > best:
            best = score
            taginfo = "%s#%d涨停%d%s" % (c, info['rank'], info['lu'], trend)
    return best, taginfo


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
    # 概念热度加成（小众概念爆发 0~15）
    cbonus, ctag = concept_bonus_calc(concepts, concept_heat or {})
    # 热度坍塌惩罚：建仓时热度曾进前100(now冷门) = 拉高出货，额外-10
    collapse_penalty = 0
    if tdet and "破位" in " ".join(tdet) and heat_ranks:
        best_rank = min(r[1] for r in heat_ranks)
        if best_rank <= 100 and (hrank or 9999) > 500:
            collapse_penalty = -10
            tdet = tdet + ["热度坍塌-10"]
    total = ts + hs + cbonus + collapse_penalty

    # 日K技术数据（最近40天：收盘/MA5/MA10/MA20/MA60/量/建仓标志）
    closes_all = [r[4] or 0 for r in rows]
    kline = []
    for j in range(max(0, len(rows) - 30), len(rows)):
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
        'heat_series': [[r[0], r[1]] for r in heat_ranks[-20:]],   # [日期, 排名]
        'kline': kline,
    }


# 泛概念黑名单：属性标签/地域/泛主题/持有者标签，不预示题材驱动
GENERIC_CONCEPTS = {
    # 国企/政策属性
    "央国企改革", "国企改革", "央企改革", "国企改革概念", "中特估", "央企国企改革",
    "专精特新", "新型工业化", "一带一路", "西部大开发", "长三角", "长江三角",
    "京津冀", "粤港澳", "粤港澳大湾区", "珠三角", "成渝特区", "雄安新区",
    "海南自贸", "海南自贸区", "深圳特区", "上海自贸", "滨海新区", "东北振兴",
    "中部崛起", "长江经济带", "黄河流域", "京津冀一体化", "中韩自贸区",
    # 持有者/资金属性
    "创投", "机构重仓", "社保重仓", "基金重仓", "融资融券", "深股通", "沪股通",
    "深成指", "上证180", "上证380", "沪深300", "中证500", "标普概念",
    "回购计划", "拟增持", "减持", "股权转让", "控股股东", "举牌", "QFII重仓",
    # 泛技术/泛主题
    "人工智能", "AI应用", "机器人概念", "数字经济", "元宇宙", "元宇宙概念",
    "华为概念", "小米概念", "华为汽车", "小米汽车", "苹果概念", "特斯拉概念",
    "无人驾驶", "虚拟现实", "东数西算", "燃料电池", " tampon",
    "ST板块", "摘帽", "并购重组概念", "股权转让", "预亏预减", "2026中报预增",
    "昨日涨停", "昨日连板", "昨日跌停", "板块题材", "独角兽", "白马股", "绩优股",
    # 泛属性/泛业绩/泛身份
    "央企", "国企", "地方国企", "国资", "四川国资", "黑龙江国资", "上海国资",
    "三季报增长", "业绩增长", "中报增长", "年报增长", "业绩预增", "业绩扭亏",
    "扭亏", "预增", "增长", "海峡两岸", "租售同权", "控制权变更", "资产注入",
    "羊字辈", "字辈", "生肖", "龙字辈",
}


def is_generic(c):
    """判断是否泛概念"""
    if c in GENERIC_CONCEPTS:
        return True
    # 地域泛概念（xx省/市/自贸/新区/振兴）
    for kw in ("自贸", "新区", "振兴", "一体化", "经济带", "特区", "改革"):
        if kw in c and len(c) <= 8:
            return True
    # 属性标签
    for kw in ("重仓", "概念"):
        if c.endswith(kw) and len(c) <= 5:
            return True
    # 带引号的字辈
    if "字辈" in c or re.search(r'[「」“”"]', c):
        return True
    return False


def load_concepts(con, date=None):
    """概念源优先级：①F10「走势最相关」(flag=1) ②真实涨停原因reason_raw
    只用F10里标记"走势最相关"的概念——精确，避免一堆无关标签
    date: 基准日，涨停原因取≤该日的最新
    """
    related_map = {}  # code -> [走势最相关概念] (F10 flag=1)
    reason_map = {}   # code -> [真实涨停原因词]

    # ①F10缓存里 flag=1 的「走势最相关」概念
    try:
        d = json.load(open(CONCEPT_CACHE, encoding='utf-8'))
        data = d.get('data') or {}
        for code, items in data.items():
            rel = []
            fallback = []
            for it in (items or []):
                if isinstance(it, (list, tuple)) and len(it) >= 2:
                    name, flag = it[0], it[1]
                    if not name:
                        continue
                    # F10标"走势最相关"的概念直接信任官方判断，不做泛概念过滤
                    if flag == 1:
                        rel.append(name)
                    elif not is_generic(name) and len(fallback) < 2:
                        fallback.append(name)   # 无最相关标记时的兜底候选
            if rel:
                related_map[code] = rel[:5]
            elif fallback:
                related_map[code] = fallback    # 至少给2个具体概念
    except Exception:
        pass

    # ②真实涨停原因（补充，放在最相关后面）
    dcut = date or '99999999'
    try:
        for code, reason in con.execute("""
            SELECT code, reason_raw FROM limit_up_day_v2
            WHERE reason_raw IS NOT NULL AND reason_raw != '' AND trade_date<=?
            ORDER BY trade_date DESC
        """, (dcut,)):
            if code in reason_map:
                continue
            words = [w.strip() for w in re.split(r'[+＋、,，/]', reason) if w.strip()]
            reason_map[code] = [w for w in words if not is_generic(w)][:3]
    except Exception:
        pass

    # 合并：F10走势最相关优先，真实原因补充
    out = {}
    allcodes = set(related_map) | set(reason_map)
    for code in allcodes:
        merged = []
        # F10「走势最相关」的概念信任官方判断，不做泛概念过滤；reason补充的才过滤
        rel = related_map.get(code, [])
        for c in rel:
            if c and c not in merged:
                merged.append(c)
            if len(merged) >= 5:
                break
        if len(merged) < 5:
            for c in reason_map.get(code, []):
                if not c or c in merged or is_generic(c):
                    continue
                merged.append(c)
                if len(merged) >= 5:
                    break
        out[code] = merged
    return out


def fetch_realtime(codes):
    """腾讯实时行情：{code: {price, pct, time}}。分批GET，GBK解码，不封IP。"""
    out = {}
    prefixed = []
    for c in codes:
        mk = 'sh' if c.startswith(('6', '9')) else ('bj' if c.startswith('8') else 'sz')
        prefixed.append(mk + c)
    # 每批80个
    for i in range(0, len(prefixed), 80):
        batch = prefixed[i:i + 80]
        url = 'https://qt.gtimg.cn/q=' + ','.join(batch)
        try:
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'Mozilla/5.0')
            data = urllib.request.urlopen(req, timeout=10).read().decode('gbk', 'ignore')
            for line in data.strip().split(';'):
                if '"' not in line or '=' not in line:
                    continue
                vals = line.split('"')[1].split('~')
                if len(vals) < 33:
                    continue
                code = line.split('=')[0].split('_')[-1][2:]  # 去掉sh/sz前缀
                try:
                    price = float(vals[3]) if vals[3] else 0
                    pct = float(vals[32]) if vals[32] else 0
                except ValueError:
                    continue
                if price > 0:
                    out[code] = {'price': price, 'pct': pct, 'time': vals[30] if len(vals) > 30 else ''}
        except Exception:
            continue
    return out


def latest_date(con, table, where=""):
    """取某表最新日期"""
    try:
        r = con.execute("SELECT MAX(trade_date) FROM %s %s" % (table, where)).fetchone()
        return r[0] if r and r[0] else None
    except Exception:
        return None


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
    # 同时覆盖旧文件名 股票池.html —— 云端根路径302指向它，不写就还是旧大页面
    legacy = os.path.join(os.path.dirname(OUT), '股票池.html')
    try:
        with open(legacy, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f"[输出] {legacy}（覆盖旧入口）")
    except Exception as e:
        print(f"[警告] 覆盖股票池.html失败: {e}")


HTML_TPL = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>A股观察 · __DATE__</title>
<style>
:root{--bg:#0f1115;--panel:#161a21;--line:#252b36;--tx:#e8ecf3;--tx2:#9aa4b5;--up:#ff5d5d;--down:#2fbf71;--accent:#5b9dff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font-family:-apple-system,"Microsoft YaHei",sans-serif;font-size:13px}
.wrap{max-width:1560px;margin:0 auto;padding:20px}
header{display:flex;align-items:baseline;gap:14px;margin-bottom:12px}
h1{font-size:19px;margin:0}.sub{color:var(--tx2);font-size:12px}
.tabs{display:flex;gap:4px;margin-bottom:14px;border-bottom:1px solid var(--line)}
.tab{padding:8px 20px;cursor:pointer;color:var(--tx2);font-size:13px;border-bottom:2px solid transparent}
.tab.on{color:var(--tx);border-bottom-color:var(--accent);font-weight:600}
.toolbar{display:flex;gap:10px;margin-bottom:12px;align-items:center}
input{background:var(--panel);border:1px solid var(--line);color:var(--tx);padding:8px 12px;border-radius:8px;font-size:13px;outline:none;width:280px}
input:focus{border-color:var(--accent)}
table{width:100%;border-collapse:collapse;background:var(--panel);border-radius:10px;overflow:hidden}
th{background:#1b2029;color:var(--tx2);font-weight:600;text-align:left;padding:9px 12px;font-size:12px;position:sticky;top:0;cursor:pointer;user-select:none;white-space:nowrap}
th:hover{color:var(--tx);border-right:1px solid #313949}th{border-right:1px solid #313949}
td{padding:8px 12px;border-top:1px solid var(--line);border-right:1px solid #2a3140;font-variant-numeric:tabular-nums;white-space:nowrap}
td:last-child,th:last-child{border-right:none}
tr:hover td{background:rgba(91,157,255,.06)}
.up{color:var(--up)}.down{color:var(--down)}
.namecell{display:flex;flex-direction:column;line-height:1.3}
a.name{text-decoration:none;color:var(--tx)}a.name:hover{text-decoration:underline;color:var(--tx)}
a.code{text-decoration:none}a.code:hover{color:var(--accent);text-decoration:underline}
.launchcell{line-height:1.25;font-size:11px}
.launchcell .mini{font-size:10px}
th{line-height:1.2}
.name{font-weight:600;font-size:14px}.code{color:var(--tx2);font-size:12px}
.score{font-size:16px;font-weight:700;line-height:1.1}
.scorecell{display:flex;flex-direction:column;gap:2px;cursor:help;font-size:12px;font-weight:600;line-height:1.2}
.ts{color:#d8c62c}.hs{color:var(--accent);text-decoration:none}
a.hs:hover{text-decoration:underline}
.nbtn{cursor:pointer}
.nbtn:hover{color:var(--accent)}
a.name.nbtn:hover{color:var(--tx)}
.concept{color:var(--tx2);font-size:11px;white-space:normal;max-width:700px}
.concept-link{color:var(--tx2);text-decoration:none;border-bottom:1px dotted var(--tx2)}
.concept-link:hover{color:var(--accent);border-bottom-color:var(--accent)}
.ranklink{color:var(--accent);text-decoration:none;font-size:11px;font-weight:600}
.ranklink:hover{text-decoration:underline}
.heatvals span{font-variant-numeric:tabular-nums}
.price-link{color:var(--tx);text-decoration:none;font-weight:600}
.price-link:hover{color:var(--accent);text-decoration:underline}
.cbonus{color:#ff6a2b;font-weight:700;background:rgba(255,106,43,.1);padding:1px 5px;border-radius:4px}
/* 热榜页 */
.hothead{margin-bottom:10px}
.hotstat{font-size:13px;color:var(--tx2);margin-bottom:4px}
.hotstat b{color:var(--tx)}
.hotwrap{overflow-x:auto}
.hottable{font-size:11px}
.hottable th{padding:6px 4px;text-align:center;font-size:10px}
.hottable td{padding:5px 4px;text-align:center;border-right:1px solid #2a3140}
.hottable td:nth-child(2){text-align:left;white-space:nowrap}
.dcell{font-variant-numeric:tabular-nums;font-weight:600;position:relative;min-width:26px}
.arrup{font-size:8px;margin-left:1px}
.hbar{display:flex;flex-direction:column;align-items:flex-start;gap:3px}
.hbarchart{display:flex;align-items:flex-end;gap:1px;width:200px;height:26px;border-bottom:1px solid var(--line)}
.hbarchart i{flex:1;min-width:2px;border-radius:1px 1px 0 0;opacity:.85}
.mini{font-size:10px;color:var(--tx2)}
.tip{cursor:help;border-bottom:1px dotted var(--tx2)}
.kbtn{background:rgba(91,157,255,.12);border:1px solid rgba(91,157,255,.35);color:var(--accent);padding:2px 9px;border-radius:5px;font-size:11px;cursor:pointer}
.kbtn:hover{background:rgba(91,157,255,.25)}
.page{display:none}.page.on{display:block}
/* 弹窗 */
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:99;align-items:center;justify-content:center}
.modal.on{display:flex}
.mbox{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px;max-width:1100px;width:95%;max-height:90vh;overflow:auto}
.mhead{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.mhead h2{font-size:16px;margin:0}
.close{background:none;border:0;color:var(--tx2);font-size:22px;cursor:pointer}
.legend{display:flex;gap:14px;font-size:11px;color:var(--tx2);margin:8px 0}
.legend i{display:inline-block;width:12px;height:3px;vertical-align:middle;margin-right:4px}
/* 概念/热榜页 */
.cgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px}
.ccard{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:12px}
.ccard .cn{font-weight:600;font-size:13px}.ccard .cs{color:var(--accent);font-size:12px;margin-top:4px}
.ccard .cl{color:var(--tx2);font-size:11px;margin-top:6px}
</style></head><body><div class="wrap">
<header><h1>A股观察</h1><span class="sub">__DATE__ · 股池 + 技术 + 热度 + 概念 · <span id="fresh"></span></span></header>
<div class="tabs">
<div class="tab on" data-p="pool">股池</div>
<div class="tab" data-p="concept">概念</div>
<div class="tab" data-p="hot">热榜</div>
</div>

<div class="page on" id="p-pool">
<div class="toolbar"><input id="q" placeholder="搜索名称/代码/概念"><span id="cnt" class="mini"></span><span class="mini">评分=技术(0~100)+热度(0~50) · 点表头排序 · 点"日线"看技术图</span></div>
<table><thead><tr>
<th style="width:110px">股票</th><th style="width:58px" data-k="total">总分</th><th style="width:78px">技/热</th><th style="width:62px" data-k="close">股价</th><th style="width:62px" data-k="pct">涨跌</th>
<th style="width:100px" data-k="launch">放量日/距今</th>
<th style="width:300px">热度20日 <span style="font-weight:400;font-size:10px;white-space:nowrap"><span style="color:#ff0033">■</span>10 <span style="color:#ff2b2b">■</span>50 <span style="color:#ff6a2b">■</span>100 <span style="color:#ffb02e">■</span>200 <span style="color:#ffe14d">■</span>500 <span style="color:#5ce1a0">■</span>千 <span style="color:#4a9eff">■</span>2千</span></th><th>概念</th>
</tr></thead><tbody id="tb"></tbody></table>
</div>

<div class="page" id="p-concept"><div id="conceptBox" class="cgrid"></div></div>
<div class="page" id="p-hot"><div id="hotBox"></div></div>

</div>
<div class="modal" id="modal"><div class="mbox">
<div class="mhead"><h2 id="mtitle"></h2><button class="close" onclick="closeModal()">×</button></div>
<div class="legend"><span><i style="background:#ff5d5d"></i>收盘</span><span><i style="background:#d8c62c"></i>MA5</span><span><i style="background:#5b9dff"></i>MA10</span><span><i style="background:#a78bfa"></i>MA20</span><span><i style="background:#6b7280"></i>MA60</span><span style="color:#ff2b2b">★=建仓日(涨停/放量)</span></div>
<div id="mchart"></div>
<div id="minfo" class="mini" style="margin-top:10px"></div>
</div></div>
<script>
const DATA=__DATA__;
let sortK='total',sortD=-1;
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
// 概念点击→同花顺F10概念页（带该股代码）
function conceptLinks(r){
  const cs=(r.concepts||[]).slice(0,4);
  if(!cs.length) return '';
  const mc=r.mc||33;
  const url='https://basic.10jqka.com.cn/astockpc/astockmain/index.html#/concept?code='+r.code+'&marketid='+mc+'&code_name='+encodeURIComponent(r.name);
  return cs.map(c=>'<a class="concept-link" href="'+url+'" target="_blank" rel="noopener" title="查看'+esc(r.name)+'的同花顺概念">'+esc(c)+'</a>').join(' / ');
}
function render(){
  const q=document.getElementById('q').value.trim().toLowerCase();
  let rows=DATA.filter(r=>!q||r.name.toLowerCase().includes(q)||r.code.includes(q)||(r.concepts||[]).some(c=>c.toLowerCase().includes(q)));
  rows.sort((a,b)=>((a[sortK]||0)-(b[sortK]||0))*sortD);
  document.getElementById('cnt').textContent=rows.length+' 只';
  const mx=Math.max(...DATA.map(r=>r.heat||0),1);
  document.getElementById('tb').innerHTML=rows.map(r=>{
    const gc=r.total>=120?'#ff2b2b':r.total>=90?'#ff6a2b':r.total>=70?'#d8c62c':r.total>=50?'#5b9dff':'#8a94a6';
    const pcls=r.pct>0?'up':r.pct<0?'down':'';
    const ps=r.pct>0?'+':'';
    const tip='技术: '+r.tdet+' | 热度: '+r.hdet+(r.launch?' | 建仓:'+r.launch:'')+(r.hlow?' | 最低热度#'+r.hlow:'');
    const series=r.heat_series||[];
    let bars='';
    if(series.length){
      // series 是 [[日期, 排名]] 或 [排名]（兼容旧数据）
      const vals=series.map(x=>Array.isArray(x)?x[1]:x);
      const smax=Math.max(...vals),smin=Math.min(...vals);
      bars=series.map(x=>{
        const v=Array.isArray(x)?x[1]:x;
        const dt=Array.isArray(x)?x[0]:'';
        const h=(smax-v)/(smax-smin||1)*100;
        // 冷→热 七档配色（排名越小越热）
        let col;
        if(v<=10)      col='#ff0033';  // 前10：最红
        else if(v<=50) col='#ff2b2b';  // 前50：红
        else if(v<=100)col='#ff6a2b';  // 前100：橙
        else if(v<=200)col='#ffb02e';  // 前200：橙黄
        else if(v<=500)col='#ffe14d';  // 前500：黄
        else if(v<=1000)col='#5ce1a0'; // 前1000：青绿
        else if(v<=2000)col='#4a9eff'; // 前2000：蓝
        else           col='#5a6478';  // 2000后：冷灰蓝
        const dtxt=dt?dt.slice(4,6)+'-'+dt.slice(6,8):'';
        return '<i style="height:'+Math.max(6,h)+'%;background:'+col+'" title="'+(dtxt?dtxt+' ':'')+'热度#'+v+'"></i>';
      }).join('');
    }
    return '<tr>'
      +'<td><div class="namecell"><a class="name nbtn" href="https://finance.baidu.com/stock/ab-'+r.code+'" target="_blank" rel="noopener" title="百度股市通行情">'+esc(r.name)+'</a><br><a class="code nbtn" href="https://www.xueqiu.com/S/'+((r.mc||33)==17?'SH':'SZ')+r.code+'" target="_blank" rel="noopener" title="雪球行情">'+r.code+'</a></div></td>'
      +'<td title="'+esc(tip)+'"><span class="score" style="color:'+gc+'">'+r.total+'</span></td>'
      +'<td><div class="scorecell">'
      +'<span class="ts nbtn" onclick="showK(\''+r.code+'\')" title="技术:'+esc(r.tdet)+'｜点击查看日线图">技 '+r.tech+'</span>'
      +'<a class="hs nbtn" href="https://ms.10jqka.com.cn/thsHotDetail/?name='+encodeURIComponent(r.name)+'&code='+r.code+'&marketCode='+(r.mc||33)+'" target="_blank" rel="noopener" title="热度:'+esc(r.hdet)+(r.hrank?' 当前#'+r.hrank:'')+'｜点击查看同花顺热度页">热 '+r.heat+'</a></div></td>'
      +'<td><a class="price-link" href="https://stockpage.10jqka.com.cn/'+r.code+'/" target="_blank" rel="noopener" title="实时价'+(r.rt_time?'('+r.rt_time.slice(8,14)+')':'')+'｜点击同花顺行情页">'+Number(r.close).toFixed(2)+'</a></td>'
      +'<td class="'+pcls+'">'+ps+Number(r.pct).toFixed(1)+'%</td>'
      +'<td class="launchcell nbtn" onclick="showK(\''+r.code+'\')" title="点击查看日线技术图">'+(r.launch?'<span>'+r.launch.slice(4,6)+'-'+r.launch.slice(6,8)+'</span><br><span class="mini">'+(r.launch_days!=null?r.launch_days+'天前':'-')+'</span>':'<span class="mini">—</span>')+'</td>'
      +'<td><div class="hbar"><div class="hbarchart" title="20日热度：左旧→右新，悬停看具体排名｜颜色由冷到热">'+bars+'</div></div></td>'
      +'<td class="concept">'+(r.cbonus>0?'<span class="cbonus" title="'+esc(r.ctag||'')+'">🔥+'+r.cbonus+'</span> ':'')+conceptLinks(r)+'</td>'
      +'</tr>';
  }).join('');
}
// 日K SVG图：收盘线 + MA5/10/20/60 + 建仓星标 + 量柱
function showK(code){
  const r=DATA.find(x=>x.code===code); if(!r)return;
  const k=r.kline||[]; if(!k.length){alert('无日线数据');return}
  document.getElementById('mtitle').textContent=r.name+' '+code+' · 近'+k.length+'日';
  const W=1040,H=340,PL=48,PR=20,PT=20,PB=70;
  const iw=W-PL-PR, ih=H-PT-PB;
  // 价格范围
  let hi=-1e9,lo=1e9;
  k.forEach(p=>{ [p[1],p[3],p[4],p[5],p[6]].forEach(v=>{if(v!=null&&v>0){hi=Math.max(hi,v);lo=Math.min(lo,v)}}) });
  const pad=(hi-lo)*0.05; hi+=pad; lo-=pad;
  const X=i=>PL+i*(iw/k.length);
  const Y=v=>PT+ih-(v-lo)/(hi-lo)*ih;
  // 网格
  let g='';
  for(let t=0;t<=4;t++){
    const v=lo+(hi-lo)*t/4, y=Y(v);
    g+='<line x1="'+PL+'" y1="'+y+'" x2="'+(W-PR)+'" y2="'+y+'" stroke="#252b36" stroke-width="1"/>';
    g+='<text x="'+(PL-6)+'" y="'+(y+4)+'" fill="#9aa4b5" font-size="10" text-anchor="end">'+v.toFixed(2)+'</text>';
  }
  // 均线路径
  function path(idx,color){
    let d='';
    k.forEach((p,i)=>{ const v=p[idx]; if(v!=null&&v>0){ d+=(d?'L':'M')+X(i).toFixed(1)+','+Y(v).toFixed(1) } });
    return '<path d="'+d+'" fill="none" stroke="'+color+'" stroke-width="1.4" opacity=".9"/>';
  }
  let ma=path(6,'#6b7280')+path(5,'#a78bfa')+path(4,'#5b9dff')+path(3,'#d8c62c');
  // 收盘折线
  let cl='';
  k.forEach((p,i)=>{ cl+=(i?'L':'M')+X(i).toFixed(1)+','+Y(p[1]).toFixed(1) });
  ma+='<path d="'+cl+'" fill="none" stroke="#ff5d5d" stroke-width="2"/>';
  // 建仓星标 + 涨停点
  k.forEach((p,i)=>{
    if(p[8]===1){
      ma+='<circle cx="'+X(i)+'" cy="'+Y(p[1])+'" r="5" fill="#ff2b2b" stroke="#fff" stroke-width="1"/>';
      ma+='<text x="'+X(i)+'" y="'+(Y(p[1])-10)+'" fill="#ff2b2b" font-size="12" text-anchor="middle">★'+p[2].toFixed(0)+'%</text>';
    }
  });
  // 量柱（底部）
  const vmax=Math.max(...k.map(p=>p[7]||0),1);
  k.forEach((p,i)=>{
    const h=(p[7]||0)/vmax*44;
    const col=p[2]>0?'rgba(255,93,93,.6)':'rgba(47,191,113,.6)';
    ma+='<rect x="'+(X(i)-3)+'" y="'+(H-24-h)+'" width="6" height="'+h+'" fill="'+col+'"/>';
  });
  // 日期轴
  for(let t=0;t<k.length;t+=Math.max(1,Math.floor(k.length/6))){
    ma+='<text x="'+X(t)+'" y="'+(H-6)+'" fill="#9aa4b5" font-size="9" text-anchor="middle">'+k[t][0].slice(4)+'</text>';
  }
  document.getElementById('mchart').innerHTML='<svg width="'+W+'" height="'+H+'" style="max-width:100%">'+g+ma+'</svg>';
  document.getElementById('minfo').textContent='建仓标志:'+(r.launch||'无')+' | 技术明细:'+r.tdet+' | 当前热度#'+(r.hrank||'-')+' 最低#'+(r.hlow||'-');
  document.getElementById('modal').classList.add('on');
}
function closeModal(){document.getElementById('modal').classList.remove('on')}
document.getElementById('modal').onclick=function(e){if(e.target===this)closeModal()};
// tabs
function renderConcept(){
  const counts={};
  DATA.forEach(r=>(r.concepts||[]).forEach(c=>{counts[c]=(counts[c]||0)+1}));
  const arr=Object.entries(counts).sort((a,b)=>b[1]-a[1]);
  document.getElementById('conceptBox').innerHTML=arr.slice(0,120).map(([c,n])=>{
    const codes=DATA.filter(r=>(r.concepts||[]).includes(c)).slice(0,5);
    return '<div class="ccard"><div class="cn">'+esc(c)+'</div><div class="cs">'+n+' 只</div><div class="cl">'+codes.map(r=>esc(r.name)).join(' · ')+'</div></div>';
  }).join('');
}
// 颜色档（冷→热）
function heatColor(v){
  return v<=10?'#ff0033':v<=50?'#ff2b2b':v<=100?'#ff6a2b':v<=200?'#ffb02e'
        :v<=500?'#ffe14d':v<=1000?'#5ce1a0':v<=2000?'#4a9eff':'#5a6478';
}
function renderHot(){
  // 全部有热度的票，按当前排名排序
  const arr=DATA.filter(r=>r.hrank!=null).sort((a,b)=>a.hrank-b.hrank);
  let h='<div class="hothead">'
       +'<div class="hotstat">当前有热度 <b>'+arr.length+'</b> 只 · 前50 <b>'+arr.filter(r=>r.hrank<=50).length+'</b> 只 · 前200 <b>'+arr.filter(r=>r.hrank<=200).length+'</b> 只</div>'
       +'<div class="mini">20日每日排名明细（左旧→右新），颜色由冷到热；↓箭头=比前一日降温</div></div>';
  h+='<div class="hotwrap"><table class="hottable"><thead><tr><th>当前<br>排名</th><th>股票</th><th>热度分</th><th>总评分</th>';
  // 20列表头用日期简写
  const sample=arr[0]&&arr[0].heat_series||[];
  sample.forEach(x=>{const dt=Array.isArray(x)?x[0]:'';h+='<th>'+dt.slice(6,8)+'</th>';});
  h+='<th>趋势</th></tr></thead><tbody>';
  arr.slice(0,300).forEach(r=>{
    const s=r.heat_series||[];
    h+='<tr><td><b style="color:'+heatColor(r.hrank)+'">#'+r.hrank+'</b></td>'
      +'<td><span class="name">'+esc(r.name)+'</span><br><span class="code">'+r.code+'</span></td>'
      +'<td class="hs" style="color:'+(r.heat>=35?'#ff5d5d':r.heat>=20?'#ffb02e':r.heat>0?'#5ce1a0':'#5a6478')+'">'+r.heat+'</td>'
      +'<td>'+r.total+'</td>';
    // 20个日格
    s.forEach((x,i)=>{
      const v=Array.isArray(x)?x[1]:x;
      const prev=i>0?(Array.isArray(s[i-1])?s[i-1][1]:s[i-1]):null;
      const up=prev!=null&&v<prev;   // 排名变小=升温
      h+='<td class="dcell" style="background:'+heatColor(v)+'22;color:'+heatColor(v)+'" title="热度#'+v+(up?' ↑升温':' ↓降温')+'">'+v+(up?'<span class="arrup">↑</span>':'')+'</td>';
    });
    // 趋势：最近5日 vs 前5日
    const vals=s.map(x=>Array.isArray(x)?x[1]:x);
    let trend='—',tc='#9aa4b5';
    if(vals.length>=10){
      const a=vals.slice(-10,-5).reduce((p,c)=>p+c,0)/5;
      const b=vals.slice(-5).reduce((p,c)=>p+c,0)/5;
      if(b<a*0.7){trend='升温↑';tc='#ff5d5d';}
      else if(b>a*1.4){trend='降温↓';tc='#4a9eff';}
      else {trend='平稳→';tc='#9aa4b5';}
    }
    h+='<td style="color:'+tc+';font-weight:600">'+trend+'</td>';
    h+='</tr>';
  });
  document.getElementById('hotBox').innerHTML=h+'</tbody></table></div>';
}
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
  document.querySelectorAll('.page').forEach(x=>x.classList.remove('on'));
  t.classList.add('on');
  document.getElementById('p-'+t.dataset.p).classList.add('on');
  if(t.dataset.p==='concept')renderConcept();
  if(t.dataset.p==='hot')renderHot();
});
document.querySelectorAll('th[data-k]').forEach(th=>th.onclick=()=>{
  const k=th.dataset.k; if(sortK===k)sortD=-sortD;else{sortK=k;sortD=-1} render();
});
document.getElementById('q').oninput=render;
render();
</script></body></html>'''


if __name__ == '__main__':
    main()
