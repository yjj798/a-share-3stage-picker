# A股三阶段选股系统（精简版）

基于「**建仓信号 → 整理不破位 → 上车信号**」三阶段形态的 A 股量化选股工具。
纯 Python 标准库实现，零第三方依赖，单文件 HTML 输出。

## 核心思路

不做花哨的多因子打分，只回答一个问题：**这只票是不是主力刚建完仓、正在整理、随时可以再启动？**

```
① 建仓信号  涨停 / 炸板 / 放量上涨（且收盘必须是阳线）
        ↓
② 整理不破位  收盘价不跌破建仓日最低价的 0.97
        ↓
③ 上车信号  缩量 + 均线多头 + 贴近支撑 + 筹码获利 + 距前高不远
```

排除连板票（建仓前 3 天无涨停、次日不涨停、后 3 天涨停 ≤1 次）——我们要的是「一波之后的整理」，不是连板中途。

## 评分构成

| 维度 | 分值 | 依据 |
|---|---|---|
| 技术面 | -20 ~ 100 | 不破位20 + 均线多头15 + 均线粘合10 + MA60方向10 + 缩量15 + 距支撑10 + 筹码获利10 + 距前高10 |
| 热度趋势 | -35 ~ 50 | 当前排名20 + 持续关注15 + 暴增10 + 热度股价共振±10 + 冷门/衰减扣分 |
| 概念热度 | 0 ~ 20 | 同花顺官方概念榜排名 + 涨停家数 + 持续天数 |
| 破位惩罚 | -20 | 有建仓信号但跌破建仓低位 = 主力拉高出货，比没信号更差 |

**总分无下限**——烂票可以到负分，和没数据的 0 分明确区分。

## 数据源

| 数据 | 来源 | 说明 |
|---|---|---|
| 行情 | 腾讯/新浪日线 | 无需鉴权 |
| 个股热度 | 同花顺 `interval_data` | 需 cookie；失效自动降级到公开热榜 |
| 概念（走势最相关） | 同花顺 F10 `stock_concept_list` | 无鉴权，带官方"走势最相关"标记 |
| 概念热度榜 | `data.10jqka.com.cn/dataapi/limit_up/block_top` | 无鉴权 |
| 涨停原因 | 同花顺涨停池 | 作为概念补充 |

## 目录结构

```
stockpool/
├── src/
│   ├── paths.py               路径配置（跨平台）
│   ├── simple_pool.py         ★ 核心：三阶段识别 + 评分 + 生成页面
│   ├── heat_quick_update.py   热度当日更新（安全限速）
│   ├── heat_backfill.py       热度90天历史补全
│   ├── concept_backfill.py    F10概念补全（走势最相关）
│   ├── ths_concept_snap.py    概念热度榜快照
│   ├── refresh_all.py         一键刷新（串起上面全部）
│   ├── export_cloud_db.py     导出精简数据库
│   └── serve_cloud.py         静态页面服务（带自动刷新）
├── engine/ingest/             行情抓取链
│   ├── quote_chain.py         编排：抓取→清洗→换手→涨停表
│   ├── fetch_quote.py         腾讯/新浪日线
│   ├── clean_quote.py         复权/涨停价/清洗
│   ├── turnover_fix.py        换手率反推
│   └── limit_up_day_sync.py   涨停表重算
├── data/                      数据库（不进版本库）
├── cache/                     概念缓存
└── out/                       生成的页面
```

## 快速开始

### 1. 环境

```bash
python >= 3.9      # 只用标准库，无需 pip install
```

### 2. 首次建库

需要一个已有的 `quant_v2.db`（含 `quote_clean` 表），或用行情链从头抓：

```bash
cd engine
python ingest/quote_chain.py     # 抓全市场日线 → 建库（首次约30-60分钟）
```

### 3. 刷新数据并生成页面

```bash
cd src
python refresh_all.py --force
```

输出：`out/股票池.html`

### 4. 本地预览

```bash
cd src
python serve_cloud.py --host 0.0.0.0 --port 8890
# 打开 http://127.0.0.1:8890
```

## 测试

```bash
python tests/test_scoring.py    # 16 个用例，覆盖三阶段识别/评分/泛概念过滤
```

## 定时任务

```cron
# 每2小时：热度 + 概念 + 评分 + 页面
0 */2 * * * cd /path/to/stockpool/src && python3 refresh_all.py >> ../logs/refresh.log 2>&1

# 交易日16:40：行情更新 → 全量重算
40 16 * * 1-5 cd /path/to/stockpool/engine && python3 ingest/quote_chain.py >> ../logs/quote.log 2>&1
```

## 接口限速说明

同花顺热度接口需逐个股票请求，默认 **3 并发 + 每只 0.12~0.15s 错峰 + 每30只歇1.5s + 失败退避**。
1586 只全池约 2 分钟，2 小时一轮，不会触发风控。

热度接口需要 cookie（有效期约 3 个月）。cookie 失效时自动降级到公开热榜接口（覆盖 top100），
不会中断服务。

## 配置

| 环境变量 | 作用 | 默认 |
|---|---|---|
| `STOCKPOOL_DB` | 数据库路径 | `engine/data/quant_v2.db` |
| `THS_COOKIE_JAR` | 同花顺 cookie 文件 | `~/ths/ths-cookie.jar` |
| `SP_REFRESH_SEC` | 自动刷新间隔（秒） | `7200` |
| `STOCKPOOL_TOKEN` | 页面访问口令（可选） | 空=不鉴权 |

## 免责声明

本项目仅用于量化研究与技术学习，不构成任何投资建议。股市有风险，入市需谨慎。
数据来自第三方公开接口，其准确性、完整性与可用性不做保证。

## License

MIT
