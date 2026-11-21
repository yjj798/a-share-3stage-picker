# -*- coding: utf-8 -*-
"""数据源读取层：概念 / 实时价 / 日期探测"""
import os
import json
import re
import urllib.request
import collections

import paths
from scoring import is_generic

CONCEPT_CACHE = paths.CONCEPT_CACHE


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


