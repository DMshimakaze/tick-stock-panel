# 兰 MA55 R1｜lan.ma55 0.6.3

生产链：一个 TickFlow raw 5m 适配器 → 三根完整 5m 聚合 15m → 原 SQLite 内的冻结 R1 → 官方 Ext Data 发布。唯一在线个人行情/策略库为 `data/user_data/lan_ma55/state.sqlite3`。K 线工作台通过本机 HTTP v1 只读，题材页继续消费 `lan_ma55_summary/day/history`。宿主固定 v0.3.36。

`engine.py`、`pipeline.py` 保持原样：MA55_15M_MINIMAL_V5_R1、state revision 4、56 根连续预热、source_count=3、有效因子和结构锁。图表计算历史淘汰仅作用于行情，持久结构记忆、历史确认及风险继续保留。相同风险 payload 不重写原 updated_at。

## 所有权、容量与额度

`runtime.py` 负责单 worker，`collector.py` 唯一拥有个人行情 SDK，`market_grid.py` 聚合/投影，`market_store.py` 管理 raw 窗口，`unified_store.py` 用同一连接提交行情、策略、风险和收据。HTTP 使用独立只读连接，关闭页面后继续维护。

SQLite WAL、短事务、writer lease、每股默认保留 1,280 根完成 raw 15m 和 2 根未完成 5m；容量由 `max(1280, (visible+232)×周期比例+聚合边界)` 计算。30/60m 从完整同源历史按需聚合，在 QFQ 投影和六 SMA 完成后才裁剪到 250/125/62 根。冻结策略单独读取最近 250 根，指标准备状态不参与 BUY。

同股同目标桶最多尝试一次；优先最新桶，再低速冷预热。minute 总预算默认 8 RPM、配置上限 12，冷预算默认 2 RPM，冷调用包含在总预算内。任务集合受证券元数据及最多 64 个短期查看需求约束。429 按响应冷却。请求收据记录 endpoint、能力、状态、延迟、标的数和重试数，滚动保留约一万行。

官方活跃 pipeline job 会让插件让出请求；它只覆盖 pipeline，账号跨端点总配额仍需实测。官方全量 1m 保持官方所有权。v0.3.36 的 minute-range 是单股最多 20 个已保存交易日的展示投影，缺完整原价及逐股来源契约；minute-batch 可触发商业补拉。因此保留一个 5m 适配器，并记录无法稳定复用的原因。

## 本机 HTTP v1

前缀 `/api/extensions/lan.ma55/v1`，仅 GET。沿用宿主认证：无密码本机访问；有密码时用已有面板 tf_session。开放 API Token 不能访问自定义路由；Ext Data 发布继续使用已有 read:ext/write:ext Token。凭证留在本机配置。

| 接口 | 限额与内容 |
| --- | --- |
| /status | owner、来源、水位、覆盖及实际请求计数 |
| /universe | 分页最多100行、日历及 generation |
| /bars | 15/30/60m，最多32证券、8000根；来源、基准、因子及有效性 |
| /events | symbol/start/end/rule_version 筛选，分页最多200行，原确认及风险；symbol_total 显示窗口外数量 |
| /inventory | 15m、5m短尾数量及首尾 |

原 status/events/symbols/cycles 路由保留。GAP、FACTOR_PENDING、SOURCE_DISCREPANCY、SOURCE_UNAVAILABLE 暂停新 BUY，历史仍可查看。

## 迁移、构建和回退

停机后备份 SQLite/WAL、旧缓存及配置。`python -m addons.lan_ma55.migrate_unified --state <状态快照> --market <行情快照> --output <新候选>` 以原 MA55 库为底，幂等迁入行情，逐条核验 signal_event/event_risk 全字段哈希和 quick_check。原 collector 的三张旧缓存表保存在快照归档，候选移除未被当前代码读取的旧表。

验过后停机替换原状态路径，原 SQLite、DuckDB、workbench 移入受限归档。官方管理器停用 lan.marketdata；退役包不启动 writer 或 SDK。回退先停机另存新状态，再按 previous_version 和一致性快照恢复。

`pnpm --dir addons/lan_ma55 build` 后由现有 nga Python 执行 build_backend.py，与 K线3.2.1、题材1.0.7、旧源退役包一起安装、重启。定向测试在 tests_backend、tests_market 和 Node tests。冻结 oracle 使用指定提交的只读副本和私人行情包。`tools/run_stress.py --output <新私人目录> --symbols 5600` 运行统一库规模、1/4/8/16读者、64 SSE、16槽及官方真实请求路径的注入 transport 联合重放，零商业流量。DB、商业返回、日志、截图、ZIP/dist 和私人参考包均不提交。

## 0.6.3 历史与展示

同一 SDK owner 优先新完成 15m，历史队列按最多18个真实交易日分窗，共享2 RPM冷预算和8 RPM总预算。`ma_history_progress` 只保存库存数量、最早时间和回补游标；图表每次按 source/basis/factor revision 和真实可见首末重新判定 READY，三秒读取不发商业请求。回补只 UPSERT raw 历史，保留最新K、原事件与风险；同源 rebuild 不清库。真实缺格点和未确认因子保留空 MA。

股票及沪深 ETF 根据 TickFlow 元信息入同一 Universe，tick_size、上市日和事件链均取原响应。默认页面只有最新完成K和同 generation/目标15m的真实数据到达数/证券分母；三列结果为名称、原价完成15m收盘、confirmed_at。价格以 `lan_ma55_prices_v1` 小体量只读投影发布，所选日无价格显示—，不改旧 day 表 schema。详情与原始诊断可展开。
