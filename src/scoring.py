# -*- coding: utf-8 -*-
"""三阶段形态识别 + 三维评分（纯函数，无IO依赖）

对外接口:
    find_launch(rows)                    ①建仓信号识别
    tech_score_calc(rows, launch_idx)    ②整理不破位 + ③上车信号 → 技术分
    heat_score_calc(heat_ranks, prices)  热度趋势分（可负）
    concept_bonus_calc(concepts, stat)   概念热度加成
    is_generic(concept)                  泛概念判定
"""
import re
import collections


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
