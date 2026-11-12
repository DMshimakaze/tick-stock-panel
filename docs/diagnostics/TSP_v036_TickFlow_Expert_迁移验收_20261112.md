# TSP v0.3.36 / TickFlow Expert 迁移验收（2026-11-12）

宿主已从官方 v0.3.32 升级到 v0.3.36，四个个人扩展已用新版本通过官方管理器安装并启动。TSP 七类行情路由均为 TickFlow Expert，盘中全量 1m 增量已实际恢复。MA55 的生产输入仍来自原 QuantDash 公共滚动库；TickFlow 5m 影子等价尚未通过，当前不能取消 QuantDash。

## 版本与边界

- 个人基线：`b60b9246a297649754ec013a462183a98a4ef3d1`，分支 `feature/tsp-quantdash-capacity-sim-20261109`，执行前与个人远端一致。
- 官方锁定 tag：`v0.3.36` → `a7868b783fc6057913bbc0a8d2b69817ad9b7d9b`，fetch 和远端解引用结果一致；[正式 Release](https://github.com/shy3130/tick-stock-panel/releases/tag/v0.3.36)。
- 交付分支：`feature/tsp-v036-tickflow-expert-20261112`，仅推送 `DMshimakaze/tick-stock-panel`；官方升级采用保留个人历史的 merge。
- `git diff --quiet v0.3.36 -- backend/app frontend/src dev.ps1` 返回 0。个人改动仅为 addons 的兼容版本、构建及说明；冻结 `lan_ma55/backend/engine.py` 和 `pipeline.py` 与个人基线无差异。
- 全部 Python 使用 `D:\Python_Envs\envs\nga\python.exe`。TickFlow SDK 0.1.25 已满足官方要求，未升级依赖。

| 扩展 | 原版本 | 已安装/运行版本 | 管理器结果 |
| --- | --- | --- | --- |
| lan.marketdata | 1.0.2 | 1.0.3 | enabled，runtime_version 一致，无 pending/failed |
| lan.ma55 | 0.3.2 | 0.3.3 | 同上 |
| lan.kline | 2.0.1 | 2.0.2 | 同上 |
| lan.theme | 1.0.2 | 1.0.3 | 同上 |

四包均保持 extension API / Page Pack v1，兼容范围 `>=0.3.32,<0.3.37`。先核对公开契约、构建及定向测试，再逐包 inspect → 校验前端候选 → install → 正常停机重启。四包旧版本仍在官方管理器 previous_version 中；没有删除 ZIP 或已安装版本。

## 数据保全及运行恢复

PASS：维护停机后完整复制 data，2677 个文件、3,544,314,532 字节，副本相对路径和大小清单一致；SQLite quick_check=ok。旧源码保存 Git bundle，旧包、配置和凭据仅保存在受限本机目录 `.private-backups/v0332-before-v036-20261112`，不进入 Git。保留未跟踪 `REVIEW_SUMMARY.md`。

旧 `signal_event` 共 **44,422** 条。按 event_id 排序、全部列 JSON UTF-8 序列化，备份与升级后旧事件子集逐行相等，无缺失或更改；两者 SHA256 均为 `98ede962501f99c056735c56b6422cae568896dec151a1621bd346433c7ced5c`。升级后正常新增事件独立计数，不混入旧子集比较。

升级前 lan.marketdata 已因 DuckDB 初始化/元数据 OOM 停止，MA55 已报 HTTP 503。使用已有私有设置将内存上限从 8GB 调为 16GB（实际连接 14.9 GiB，2 线程），未修改存储源码。启动后 writer_alive=true，完成当日主数据验证并进入 LIVE；MA55 恢复 WAITING/ANALYZING，source_asof 推进到 2026-11-12 11:15，历史发布恢复。QuantDash 部分批次仍返回 INVALID_5M_OHLCV，接口和图表保留真实已保存 K 并显示错误；该供应商输入问题未记为“全市场最新行情通过”。

## TSP 原生 TickFlow 路由

执行开始时用户已绑定 Expert，七类路由已全部选择 TickFlow，全量 1m 已开启。本轮保留设置并复核实际生效，未重新发起全市场历史同步。

| 能力 | current / effective | usable | 实证 |
| --- | --- | --- | --- |
| daily | tickflow / tickflow | true | 股票、ETF、指数各 3 根 raw 日 K |
| adj_factor | tickflow / tickflow | true | 股票 30 条、ETF 14 条真实事件；指数无返回键，不判定“确认无事件” |
| realtime | tickflow / tickflow | true | 三类标的快照及看板盘中更新 |
| minute | tickflow / tickflow | true | 三类标的 11-11 09:30—15:00 各 241 条（包含 09:30 标签） |
| full_minute | tickflow / tickflow | true | 原生增量请求和当日真实分区推进 |
| depth5 | tickflow / tickflow | true | 股票买卖各 5 档 |
| financial | tickflow / tickflow | true | 股票最新 metrics 1 条 |

真实有界 SDK 验证合计 **6 个 HTTP 请求**（klines/batch 两次，ex-factors、quotes、depth、financials/metrics 各一次），全部 HTTP 200，max_retries=0；证券为 000001.SZ、510300.SH、000001.SH。股票/ETF 有成交分钟按 amount/(volume×100) 均落在 OHLC 范围内，验证原始量为手；指数点位不能套用个股 VWAP 检查。事件因子均正数，时间按北京时间转换。

11:21—11:28 的原生全量 1m：healthy/running=true，increment，每轮 1 请求，last_symbols=5578；观测轮数 8→134。直接读当日 Parquet，实际 5573 个有行标的，632,516→638,089 行，最大时间推进到 **11:27**。API 标的计数与实际有行标的分别报告。未开启额外全市场冷启动。

NOT_VERIFIED：最新“成功”任务 239c52316b 是 11-11 的完整性修复，日志只有 integrity_check/sync_daily/compute_enriched/compute_regime，没有 sync_adj，不能当作完整盘后管道成功。11-09 的原盘后任务仍为 sync_adj 失败；升级后下一次 15:35 完整管道尚未发生，本轮未主动全市场重跑。

Fuyao 概念/行业成员为独立旧快照，保留来源；KPL 正式授权快照仍缺失，lan.theme 为 AUTHORIZED_SNAPSHOT_REQUIRED / ONLINE_KPL_NOT_VERIFIED，未伪造题材。

## 直接验证

| 检查 | 实际结果 |
| --- | --- |
| 官方前端及三包前端构建 | 完成；使用现有依赖，未改官方前端 |
| 官方分钟流、分钟复权/刷新、扩展管理、配置备份/数据目录测试（6 个文件） | **114 passed / 4 failed / 1 skipped** |
| lan.marketdata Python | 27 passed |
| lan.ma55 Python | 37 passed / 26 skipped |
| lan.theme Python | 23 passed |
| lan.kline Python unittest | 4 passed |
| MA55 / K线 / 题材 Node | 41 passed + 2 skipped / 23 passed / 15 passed |
| 官方管理器及 HTTP | 四包新版本实际加载，公共 15/30/60m 对同一证券均返回 256 根，MA55 恢复发布 |
| UI | v0.3.36 导航、看板及 K线工作台 15/30/60m 真实蜡烛、成交量、MA 已观察；BUY 深链携带 event/setup/defense/factor |
| 工作区检查 | git diff --check 通过；官方核心和冻结算法差异为零 |

官方测试四个失败原样保留：Windows ZIP 将反斜杠路径规范化导致一个拒绝测试未抛错；symlink 用例受 WinError 1314 权限限制；两个配置恢复用例导出策略文本空行格式与固定期望不同。没有为通过测试修改官方代码。本次包安装与完整停机文件备份已分别实测，配置归档往返的四个失败未称为通过。MA55 跳过项需要未提供的冻结包/研究 oracle，不构成真实 TickFlow BUY 等价验证。首次将多个同名 tests_backend 根目录合跑导致导入冲突，之后按各目录独立运行得出上表结果。

## 迁移闸门和回退

PASS：官方宿主及四包兼容运行、旧 BUY 完整、七类路由、盘中原生 1m。NOT_VERIFIED：完整新盘后管道、TickFlow 5m/历史 BUY 真实影子等价、生产停用 QuantDash。TickFlow 原生 minute 只支持 1m；5m 候选必须单独验证，不能把原生 15m 标成 source_count=3。

回退先正常停止本工作树前后端。仅需退包时使用官方管理器 previous_version，并使用兼容的旧宿主；完整回退用保留的旧分支/源码 bundle 和私有一致性副本，在保留升级后数据副本后恢复原路径。不要直接覆盖正在写入的数据库。API Key、旧 QuantDash、旧包和原数据库均保留；本轮未执行完整生产回退。
