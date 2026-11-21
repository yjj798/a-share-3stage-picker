把 A股三阶段选股系统（精简版）的源码做代码质量优化，然后推送到 GitHub。项目已独立到 E:\stockpool_oss\stockpool 并上了 GitHub（yjj798/a-share-3stage-picker，私有）。

已就绪：源码14个py文件、README、LICENSE、.gitignore、requirements.txt、OPTIMIZE_TASK.md（优化任务书）

约束：
- 零第三方依赖，只用 Python 标准库
- Python 3.9+
- 不改评分算法（调好的），只优化代码质量
- 不改数据库 schema
- 保持现有功能行为一致

验收：
1. cd src && python refresh_all.py --force 能生成 out/股票池.html
2. 无硬编码 Windows 路径
3. 核心逻辑有单元测试