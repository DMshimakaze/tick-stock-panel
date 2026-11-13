# lan.marketdata 2.1.0｜退役包

行情所有权和数据库已迁入 lan.ma55 0.5.0 的原 state.sqlite3。本包只有 `/api/extensions/lan.marketdata/status` 返回 RETIRED，不包含 SDK、采集器、数据库 writer 或转发服务；在官方管理器中保持停用。

迁移和回退见 addons/lan_ma55/README.md。旧 SQLite、DuckDB、workbench 和配置保存在受限本地归档。原运行版本由官方管理器 previous_version 保留。

import_legacy.py 是仅在源码 checkout 使用的离线 DuckDB 导入工具，复用统一 MA55 滚动存储；不进入生产 ZIP。旧 collector/store/runtime 和重复测试已迁入 MA55 或移除。nga Python 运行 build_backend.py 生成退役包，构建产物及用户数据不提交。
