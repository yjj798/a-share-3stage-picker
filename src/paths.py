# -*- coding: utf-8 -*-
"""跨平台路径配置 —— Windows / Linux 通用

目录约定：
    stockpool/
      src/          Python 源码（本文件所在）
      engine/       行情抓取链
        ingest/
        data/       行情数据库 quant_v2.db
      cache/        F10 概念缓存
      out/          生成的静态页面
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))   # src/
ROOT = os.path.dirname(HERE)                        # stockpool/

# 数据库：环境变量优先，其次本地 engine/data
DB = os.environ.get('STOCKPOOL_DB') or os.path.join(ROOT, 'engine', 'data', 'quant_v2.db')

# 概念缓存（同花顺 F10 概念 + 走势最相关标记）
CONCEPT_CACHE = os.path.join(ROOT, 'cache', 'f10_concepts.json')

# 同花顺 cookie jar（热度接口需要；缺失时自动降级到公开热榜）
COOKIE_JAR = os.environ.get('THS_COOKIE_JAR') or os.path.join(
    os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ths', 'ths-cookie.jar')

# 页面输出目录
OUT = os.path.join(ROOT, 'out')
