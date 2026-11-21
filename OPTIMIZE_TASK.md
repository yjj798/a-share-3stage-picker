# 代码优化任务书（给豆包 AI）

## 项目背景

这是一个 **A股三阶段选股系统（精简版）**，要开源到 GitHub。
纯 Python 标准库，零第三方依赖，核心功能：

1. 行情抓取（同花顺日线）
2. 同花顺热度抓取（个股热度排名 + 概念热度榜）
3. F10 概念抓取（含"走势最相关"标记）
4. 三阶段形态识别（建仓信号→整理不破位→上车信号）
5. 三维评分（技术面 + 热度趋势 + 概念热度）
6. 生成单文件 HTML 页面
7. 静态服务（带自动刷新）

## 目录结构

```
stockpool/
├── src/
│   ├── paths.py               路径配置
│   ├── simple_pool.py   (901行) ★核心：形态识别+评分+页面生成
│   ├── heat_quick_update.py   热度当日更新
│   ├── heat_backfill.py       热度90天历史补全
│   ├── concept_backfill.py    F10概念补全
│   ├── ths_concept_snap.py    概念热度榜快照
│   ├── refresh_all.py         一键刷新编排
│   ├── export_cloud_db.py     导出精简数据库
│   └── serve_cloud.py         静态服务+自动刷新
├── engine/ingest/             行情抓取链(5个文件)
├── data/  cache/  out/        数据/缓存/输出
├── README.md  LICENSE  .gitignore  requirements.txt
```

## 需要优化的方向

### 优先级 P0（必须改）

1. **simple_pool.py 拆分（901行太长）**
   建议拆成：
   - `scoring.py`   形态识别 + 三维评分（纯函数，无IO）
   - `template.py`  HTML模板（目前内嵌在PY里，很长）
   - `simple_pool.py` 只做编排（读库→调评分→渲染→写文件）
   目标：每个文件 <300 行

2. **错误处理补齐**
   - 网络请求全部要有 timeout + 重试 + 降级
   - 数据库操作要有异常保护，不能让单只股票失败导致整轮崩
   - 现在有些 `except: pass` 吞异常，要改成记录日志

3. **日志规范化**
   - 现在全是 `print`，改用 `logging` 模块
   - 支持 `--verbose / --quiet`
   - 关键节点打时间戳和耗时

### 优先级 P1（重要）

4. **配置外置**
   - 现在阈值硬编码在代码里（如破位判定 0.97、放量 2 倍、涨停 9.8%）
   - 抽到 `config.json` 或命令行参数，方便不同市场环境调整

5. **单元测试**
   - 给评分核心写测试（构造合成K线验证三阶段识别）
   - 覆盖边界：无建仓信号、破位、连板排除、数据不足

6. **CLI 友好化**
   - `python -m stockpool.refresh` 这样能跑
   - `--date` 参数支持历史回看

### 优先级 P2（加分）

7. **类型注解**（关键函数）
8. **docstring 补全**（Google 风格）
9. **数据校验层**（抓取后校验字段完整性，异常数据不入库）

## 硬性约束

- ✅ 保持零第三方依赖（只用标准库）
- ✅ Python 3.9+
- ✅ 保持现有功能不变（行为一致性）
- ✅ 不引入框架（不用 Flask/FastAPI/pandas）
- ❌ 不要改数据库 schema
- ❌ 不要改评分逻辑（算法是我们调好的，只优化代码质量）

## 验收标准

```bash
# 1. 能跑
cd src && python refresh_all.py --force
# 期望：生成 out/股票池.html

# 2. 能测
cd src && python -m pytest tests/ -v
# 期望：全绿

# 3. 能起服务
cd src && python serve_cloud.py --port 8890
# 期望：http://127.0.0.1:8890 可访问

# 4. 无硬编码
grep -rn "E:\\|C:\\Users" src/ engine/  # 应为空
```

## 交付物

1. 重构后的代码（保持目录结构，可新增 tests/）
2. 更新 README（补充开发/测试章节）
3. 一份 CHANGELOG 说明改了什么
4. `config.example.json` 示例配置
