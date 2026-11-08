# 兰 K 线工作台 1.1.2

TSP v0.3.32 官方前后端扩展包 `lan.kline`，独立路由 `/lan-kline`。原 Page Pack 原地升级；宿主核心零修改。

## 数据路径

页面搜索 / MA55 深链 → `ctx.api.extDataIngest(lan_kline_demand)` → 宿主内后台 worker → QuantDash Pro 原始 K（`adjust=none`）→ `context.data_dir/user_data/lan_kline/workbench.sqlite3` → 官方 `lan_kline_view` → `ctx.api.extDataRows` → 当前周期聚合 / MA → Lightweight Charts 5.2.1。

- `bar_5m`：75 个**实际交易日**；15/30/60m 仅由完整真实5m聚合。
- `bar_1m`：用户首次选择1m后才采集，20交易日；09:30集合竞价不计入连续竞价分钟。
- `bar_daily`：300交易日原始日K，支持独立日K和长周期MA；不保存派生周期和MA副本。
- `symbol_cursor`：真实最早/最新、连续水位、缺口、请求及重试时间。先增量，再预热，再旧缺口；缺口查询止于下一根已存K之前。
- 最多50只跟踪标的；普通订阅7日到期，满额按未固定标的LRU淘汰；固定关注持续维护。单次最多3只同范围批量，页面关闭后继续维护已订阅标的。
- 日历来自真实指数日K，周末/节假日沿用最近已确认交易日。完成点留60秒余量。
- SQLite只有一个后台写线程和进程租约，WAL；收到批次后原始K、水位、请求记录和配额检查点同事务落盘。重启不重抓已完整覆盖的历史。

当前官方 Page SDK 未开放内部因子读取或任意扩展 GET。此包仅对跟踪标的读取真实 QuantDash 事件链，`qfq = raw / product(bar_day < event_day <= asof_day 的 ex_factor)`，OHLC只投影一次，量额不变。源遗漏因子覆盖时明确显示 **RAW / 因子待补**，防守价不叠加；覆盖空事件链与缺覆盖分开处理。没有复制宿主复权流水线或读取宿主 Parquet。

## 官方数据桥

两个市场级 snapshot 表以第一列 `scope` 为固定键，16个槽位，避免官方 snapshot 合并行为导致无限累积股票/客户端。90秒租约，30秒心跳，仅选股、级别需求、主动刷新及必要心跳写需求。标签页使用独立UUID和请求序号；读回槽位回执，按 scope/client/symbol/request/revision 核对后渲染。最多16个活动图表；源订阅上限与活动视图区分。

页面每3秒只读状态列，revision变化才取历史；过期槽位由官方ingest清空大载荷。SQLite保留已订阅行情，官方表只保存当前少量图表视图。页面无自建fetch、无后端直连、无SQLite读取，localStorage仅保存MA/根数/最后级别。

## 构建与安装

使用现有 nga，依赖为 `quantdash==0.1.0`、`httpx>=0.28,<0.29`；不安装或分发商业SDK。

```powershell
pnpm --dir addons/kline_workbench install --frozen-lockfile
pnpm --dir addons/kline_workbench build
& 'D:\Python_Envs\envs\nga\python.exe' -B -X utf8 addons/kline_workbench/build_backend.py
```

新安装须通过官方API创建本插件的两个表（本轮已完成）：在项目根目录以 nga 执行 `from addons.kline_workbench.backend.publisher import Publisher; p=Publisher('http://127.0.0.1:3018'); p.prepare(); p.close()`。`prepare()`核对同名表结构，不覆盖其他表。若宿主启用认证，传入已授权的官方token；后台可用 `KLINE_TSP_EXT_TOKEN` 或私有 `settings.json` 的 `api_token`，需要官方扩展数据读写及扩展状态读取权限。页面继续使用宿主SDK现有认证。不要把token写入源码或ZIP。

1. 备份旧 `lan_kline` JS和管理信息，在官方页面管理中禁用旧独立页面，保留回退副本。
2. 设置 → 导入与备份 → 导入 `dist/lan-kline-1.1.2.zip`，确认来源后安装并重启。统一包和旧包不同时启用同一路由。
3. 宿主进程沿用 `QUANTDASH_TOKEN`。可选私有配置位置 `data/user_data/lan_kline/settings.json`，字段 `api_base`、`api_token`、`quantdash_token`；所有凭据留在本机。
4. 只读运维：`GET /api/extensions/lan.kline/status`，查看源请求、覆盖、缺口与后台状态。

分钟额度：工作台2次/滚动60秒；配套 MA55 0.2.10 支持私有配置 `minute_requests_per_minute=22`（原默认24仍保留）；TSP现有Provider最多6次非实时请求，合计不超过30。实际HTTP hooks计数，429按真实Retry-After冷却并落盘；工作台首次启动留60秒交接。若活跃MA55仍配置24，工作台暂停分钟请求并显示BUDGET_WAIT。

MA55改动只涉及额度配置、公开状态字段及工作台深链（确认/支撑/防守），策略与确认持久库不变。回退MA55至24前先禁用工作台；工作台可由官方管理器回退1.1.0，或禁用统一包后恢复备份的1.0.1独立页面。都不删除行情或MA55确认数据。

## 页面

日K / 1m / 5m / 15m / 30m / 60m；默认15m、MA55/233。支持最多12条自定义MA、可见根数、日期范围、刷新保留缩放、准确的MA55确认时点定位。交易日缺K会中断对应均线，不跨午休合成，不把范围外信号挪到最后一根。

主题读取TSP公开CSS变量和html主题状态；图表同步更新。A股涨跌 `#C74040/#2D9B65`，JetBrains Mono轴/读数、专业十字光标、独立成交量pane。原生个股详情仍由 `ctx.ui.openStockPreview` 打开。

定向验证见 [REVIEW_HANDOFF.md](REVIEW_HANDOFF.md)。真实行情、SQLite、构建ZIP、日志、SDK、截图均不入Git。周日验证历史恢复与实际采集；跨日淘汰和未来新完成K用临时历史样例验证，没有声称已实测未来盘中时效。
