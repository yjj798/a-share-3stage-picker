# -*- coding: utf-8 -*-
"""三阶段形态识别 + 评分逻辑的单元测试

用合成K线验证核心判定，不依赖数据库和外网。
运行: python -m pytest tests/ -v   或   python tests/test_scoring.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

import simple_pool as sp


def bar(date, o, h, l, c, vol, pct):
    """构造一行K线: (trade_date, open, high, low, close, volume, pct_recalc)"""
    return (date, o, h, l, c, vol, pct)


def make_flat(n, price=5.0, vol=10000, start=1):
    """构造n根横盘K线"""
    return [bar(f"202601{start+i:02d}", price, price * 1.01, price * 0.99, price, vol, 0.0)
            for i in range(n)]


class TestFindLaunch(unittest.TestCase):
    """①建仓信号识别"""

    def test_limit_up_signal(self):
        """涨停应被识别为建仓信号"""
        rows = make_flat(10, 5.0)
        # 第11根：涨停 5.0 → 5.5
        rows.append(bar("20260111", 5.0, 5.5, 5.0, 5.5, 30000, 10.0))
        rows.extend(make_flat(3, 5.5, start=12))
        idx = sp.find_launch(rows)
        self.assertIsNotNone(idx)
        self.assertEqual(idx, 10)

    def test_volume_up_yang(self):
        """放量上涨+阳线 应被识别"""
        rows = make_flat(10, 5.0, vol=10000)
        # 涨6%、量3倍、收盘>开盘
        rows.append(bar("20260111", 5.0, 5.4, 4.95, 5.3, 35000, 6.0))
        rows.extend(make_flat(3, 5.3, start=12))
        idx = sp.find_launch(rows)
        self.assertIsNotNone(idx)
        self.assertEqual(idx, 10)

    def test_volume_up_yin_rejected(self):
        """放量但收阴线 —— 不是建仓信号"""
        rows = make_flat(10, 5.0, vol=10000)
        # 涨6%但收盘5.0 < 开盘5.2 → 阴线
        rows.append(bar("20260111", 5.2, 5.35, 4.95, 5.0, 35000, 6.0))
        rows.extend(make_flat(3, 5.0, start=12))
        idx = sp.find_launch(rows)
        # 这个位置不应该被选中（可能返回None或其他位置）
        self.assertNotEqual(idx, 10)

    def test_consecutive_limit_up_excluded(self):
        """连板票应被排除（前面3天有涨停）"""
        rows = make_flat(10, 5.0)
        # 连板：连续涨停
        rows.append(bar("20260111", 5.0, 5.5, 5.0, 5.5, 30000, 10.0))
        rows.append(bar("20260112", 5.5, 6.05, 5.5, 6.05, 30000, 10.0))
        rows.append(bar("20260113", 6.05, 6.66, 6.05, 6.66, 30000, 10.0))
        idx = sp.find_launch(rows)
        # 连板的后续位置不该被当成建仓
        if idx is not None:
            self.assertNotEqual(idx, 12)
            self.assertNotEqual(idx, 11)

    def test_no_signal_returns_none(self):
        """完全横盘无信号"""
        rows = make_flat(30, 5.0)
        self.assertIsNone(sp.find_launch(rows))


class TestTechScore(unittest.TestCase):
    """②整理不破位 + ③上车信号 → 技术分"""

    def _base(self):
        """构造：横盘 → 涨停建仓 → 缩量横盘整理"""
        rows = make_flat(20, 5.0, vol=10000)
        rows.append(bar("20260121", 5.0, 5.5, 5.0, 5.5, 40000, 10.0))  # 建仓
        # 整理：不破建仓日最低5.0，缓缓抬升
        for i in range(10):
            p = 5.4 + i * 0.02
            rows.append(bar(f"202602{i+1:02d}", p, p * 1.01, 5.25, p, 5000, 0.5))
        return rows, 20

    def test_normal_setup_positive(self):
        """正常蓄势应得正分"""
        rows, li = self._base()
        score, _, _, det = sp.tech_score_calc(rows, li)
        self.assertGreater(score, 0, f"正常蓄势不该<=0, det={det}")

    def test_broken_position_negative(self):
        """跌破建仓低位应得负分（-20）"""
        rows, li = self._base()
        # 追加一根跌破建仓日最低价 5.0*0.97=4.85
        rows.append(bar("20260301", 5.0, 5.0, 4.5, 4.6, 8000, -15.0))
        score, _, _, det = sp.tech_score_calc(rows, li)
        self.assertEqual(score, -20, f"破位应-20, 实际{score}, det={det}")
        self.assertTrue(any('破位' in d for d in det))


class TestHeatScore(unittest.TestCase):
    """热度趋势评分（可负）"""

    def test_hot_stock_high_score(self):
        """当前在前200名的票应有高分"""
        ranks = [("20260101", 1500), ("20260102", 800), ("20260103", 300),
                 ("20260104", 150), ("20260105", 90), ("20260106", 45)]
        score, latest, low, det = sp.heat_score_calc(ranks)
        self.assertGreaterEqual(score, 30, f"前50名应高分, 实际{score}, det={det}")
        self.assertEqual(latest, 45)

    def test_cold_stock_negative(self):
        """长期无人问津应得负分（0不再是下限）"""
        ranks = [("20260101", 5000), ("20260102", 5200), ("20260103", 4800),
                 ("20260104", 5100), ("20260105", 5300), ("20260106", 5500)]
        score, _, _, det = sp.heat_score_calc(ranks)
        self.assertLess(score, 0, f"冷门票应负分, 实际{score}, det={det}")

    def test_insufficient_data_returns_zero(self):
        """数据不足3天返回0"""
        score, _, _, _ = sp.heat_score_calc([("20260101", 100)])
        self.assertEqual(score, 0)

    def test_decay_penalty(self):
        """曾进前100现在掉出500，应有坍塌扣分"""
        ranks = [("20260101", 500), ("20260102", 200), ("20260103", 80),
                 ("20260104", 60), ("20260105", 50), ("20260106", 900)]
        score, _, _, det = sp.heat_score_calc(ranks)
        joined = " ".join(det)
        self.assertTrue('坍塌' in joined or '退潮' in joined or '降温' in joined,
                        f"应有衰减扣分, det={det}")


class TestConceptBonus(unittest.TestCase):
    """概念热度加成（用官方概念榜）"""

    def test_hot_concept_bonus(self):
        stat = {"算力租赁": {"rank": 3, "change": 5.0, "lu": 8, "days": 4, "prev_rank": 5}}
        bonus, tag = sp.concept_bonus_calc(["算力租赁"], stat)
        self.assertGreater(bonus, 0)
        self.assertIn("算力租赁", tag or "")

    def test_generic_concept_ignored(self):
        """泛概念不计分"""
        stat = {"央国企改革": {"rank": 1, "change": 3.0, "lu": 10, "days": 5, "prev_rank": 2}}
        bonus, tag = sp.concept_bonus_calc(["央国企改革"], stat)
        self.assertEqual(bonus, 0, "泛概念不该加分")

    def test_no_match_zero(self):
        bonus, tag = sp.concept_bonus_calc(["不存在的概念"], {})
        self.assertEqual(bonus, 0)
        self.assertIsNone(tag)


class TestIsGeneric(unittest.TestCase):
    """泛概念过滤"""

    def test_generic_true(self):
        for c in ["央国企改革", "国企改革", "机器人概念", "一带一路", "专精特新", "人工智能"]:
            self.assertTrue(sp.is_generic(c), f"{c} 应判为泛概念")

    def test_specific_false(self):
        for c in ["光刻胶", "算力租赁", "商业航天", "固态电池", "人形机器人"]:
            self.assertFalse(sp.is_generic(c), f"{c} 不该判为泛概念")


if __name__ == '__main__':
    unittest.main(verbosity=2)
