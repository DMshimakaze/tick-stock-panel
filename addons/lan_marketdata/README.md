# 公共滚动行情｜lan.marketdata 2.0.0

官方 TSP v0.3.36 的独立后端扩展（extension API v1）。唯一行情生产者使用 TickFlow SDK 0.1.25 的 raw 5m，真实三根组成完整 15m。MA55 和 K线工作台通过本包 HTTP v2 消费；官方全市场 1m 继续独立运行。本包不导入宿主私有模块、不读取官方 Parquet、不使用 QuantDash SDK。

## 运行与数据

- 在线文件：`data/user_data/lan_marketdata/rolling15.sqlite3`，SQLite WAL、一个 writer/OS lease、短事务、独立只读连接和 busy_timeout。K、因子状态及源水位同事务提交。
- 每股最多 250 根 raw 15m；尚未完成的 5m 短尾最多 2 根。只持久化 bar15 / tail5 / instrument_state / factor_event / meta，不维护全市场多周期表或独立指标历史。
- 15m 读取时按真实事件链投影 QFQ，成交量由手转股，成交额为元。明确空事件列表有效；缺证券键/无效因子保持 FACTOR_PENDING，不能补 1。因子和历史修订改变 basis_revision，原始价格不混入复权列。
- SMA 在有效、连续、同源的窗口上计算。250 根只有末尾 18 个 MA233 点。30m/60m 只对正在查看的证券从 2/4 根完整 15m 聚合，最多 125/62 根，MA233 无值；缺口、午休、跨日不会拼造 K。
- 北京完成安全余量 75 秒，16 个真实 15m 完成点驱动更新。交易日来自明确的指数日 K 日期，不把工作日当作已确认交易日。节假日、午休和收盘沿用最后真实完成点。
- 初始冷请求至多 8 股、18 个已有交易日、1200 根 5m/股；已预热后的增量只请求缺少的完成 5m，补缺从对应桶起点开始。每次独立 SDK HTTP 请求计数，minute 默认 8 RPM（配置上限 12）、冷请求默认 2 RPM；已预热增量批量至多 100 股。新安装后全市场历史在正常有界队列中逐步恢复，验收只预热有限样本。
- 请求前查询官方 `/api/pipeline/jobs`，有活跃任务时让出供应商额度。单 producer/单进程 ownership；429 后按 Retry-After 冷却至少 60 秒，SDK max_retries=0。它不是跨进程统一账号配额预约，不保证其他客户端并发时零 429。
- 单条不合法 OHLC 被隔离为缺口，不合成其 15m；同一响应的合法股票与历史仍可提交。最新桶异常暂停该股分析，较早缺口由冻结算法按连续 56 根预热。结构错位/冲突重复拒收相应证券，网络错误保留已有缓存。

## 配置

凭证只取启动环境 `TICKFLOW_API_KEY` 或私有 `data/user_data/lan_marketdata/settings.json` 的 `tickflow_api_key`。没有凭证显示 CREDENTIAL_REQUIRED；不读取宿主 secrets_store。配置和凭证不进 Git。可选 `api_base/api_session` 用于本机官方任务状态读取，`minute_requests_per_minute`、`cold_requests_per_minute` 控制本包预算；`collection_symbols` 可限定授权的现场验证范围。

## HTTP v2

前缀 `/api/extensions/lan.marketdata/v2`，仅 GET，沿用官方会话认证。

| 路径 | 返回 |
| --- | --- |
| `/status` | writer/SQLite、实际 HTTP/429、数据源、状态计数、最老/最新完成点、冷队列与配额说明 |
| `/universe?offset=0&limit=100` | 证券元数据、revision/source/basis/validity、会话日历、data_revision |
| `/bars?symbols=000001.SZ&timeframes=15m,30m,60m&limit=250&asof=...` | 最多32证券、8000根；raw来源/QFQ基准、缺口、MA、source_count、当前服务错误 |
| `/inventory?symbol=000001.SZ` | 实际15m和5m短尾行数、首尾与容量 |

v1 已随三个消费包一起切换，不维护第二套行情接口或采集端。writer 退出或源不可用时，只读历史仍可返回并显示错误；SQLite 文件本身不可读才返回明确 503。

## 旧库迁移及恢复

先正常停止相关服务并保存一致性备份。使用既有 nga Python 执行：

```text
python addons/lan_marketdata/import_legacy.py --source <旧rolling.duckdb> --destination <新rolling15.sqlite3>
```

程序用 DuckDB 官方 read_only 连接读取原包的真实 raw 15m/证券/因子，仅复制每股最后250根。重复执行跳过已导入证券，不覆盖新 TickFlow 数据；不写原文件。导入来源固定 QUANTDASH_LEGACY，只可展示；旧数据不会与未核实的 TickFlow 新 K 拼成有效 BUY 窗口。完整新源窗口会原子替换该股在线窗口；旧原文件一直保留。至少56根重叠及因子完全核实才能接续旧因果状态，否则以新源真实窗口重建并保护原锁/确认。

回退应先停机、另存新产生的数据，再使用官方管理器 previous_version 及本轮一致性事件库备份。旧 QuantDash 运行包、原 rolling.duckdb、全部旧 BUY 和设置都保留；不在线覆盖数据库。

## 构建与验证

nga Python 运行 `addons/lan_marketdata/build_backend.py` 生成2.0.0包；通过官方管理器安装，与 MA55 0.4.0、K线3.0.0同步重启。定向 pytest 在 `tests_backend`；`tools/benchmark_sqlite.py --database <新的私有测试文件>` 可重现 5600×250 合成 WAL 压测。商业响应、DB、日志、ZIP/dist 不提交。
