# TSP / MA55 TickFlow Expert 一轮重构验收

2026-11-12，北京时间。结论：**DONE：实现、正常停机备份、旧库只读迁移、官方管理器安装、重启及有界现场验收完成。** 全市场新源预热由正常限额队列继续；供应商异常和未实测项目见下文。

交付分支：`feature/tsp-ma55-expert-onepass-20261112`，个人 Fork `DMshimakaze/tick-stock-panel`。本报告所在提交为本轮交付；完整 SHA 和远端读回结果见交付回复。基线 `df48361c97e7438475cec234ee28747c9365c00d`；官方宿主 `v0.3.36` / `a7868b783fc6057913bbc0a8d2b69817ad9b7d9b`。

## 已安装的实现

| 包 | runtime_version | previous_version | enabled / pending / failed |
| --- | --- | --- | --- |
| lan.marketdata | 2.0.0 | 1.0.3 | true / null / null |
| lan.ma55 | 0.4.0 | 0.3.3 | true / null / null |
| lan.kline | 3.0.0 | 2.0.2 | true / null / null |
| lan.theme | 1.0.4 | 1.0.3 | true / null / null |

唯一在线 producer：TickFlow SDK 0.1.25 `klines.batch(period='5m', adjust='none')` → 真实三根完整 5m → raw 15m → `data/user_data/lan_marketdata/rolling15.sqlite3`。SQLite 使用 WAL、OS 单 writer lease、短事务及独立只读连接；每股 ≤250 根 15m、≤2 根未完成 5m。因子、基准修订、缺口及水位随行情事务提交。QFQ 和 SMA 在读取时计算，30/60m 仅对查看证券聚合 2/4 根完整 15m。

MA55 与 Kline 同步消费 `/api/extensions/lan.marketdata/v2`。MA55 的独立 QuantDash 采集器、预算及 LiveStore 生产入口已移除。冻结 `engine.py`、`pipeline.py` 字节差异为零；规则仍为 `MA55_15M_MINIMAL_V5_R1`、state revision 4。消费收据带源及因子修订；正常增量沿用持久结构、volume slots 和推动记忆，跨源未核实时按新源窗口重建，原确认始终保留。

`backend/app/`、`frontend/src/`、`dev.ps1` 相对官方 tag 差异为零；研究仓库只读。运行环境为原 nga，未升级依赖。凭证来自用户本地 `TICKFLOW_API_KEY`，未读取宿主私有凭证存储；商业响应、备份、ZIP、数据库和配置均留在本机。

## 迁移与事件保全

原生 15:35 日常任务成功完成后正常停止两个服务。完整数据和安装状态已复制到本机受限备份目录；三个 SQLite 通过 backup API 另存，`quick_check=ok`。通过官方导入预检、三个前端候选内容核对、四包暂存，再停机执行离线迁移并正常重启。

旧 DuckDB 以官方 `read_only=True` 读取，共迁入 8,282 条证券状态、1,395,123 根完整 15m，拒收 0 根。旧源统一标为 `QUANTDASH_LEGACY` / `REBUILD_PENDING`，仅供缓存展示；新 TickFlow 窗口按证券原子替换，来源不混装。生产迁移后 SQLite `quick_check=ok`、15m 最大250、5m短尾最大2。幂等重复迁移由定向测试验证。原 `rolling.duckdb` 和 `workbench.sqlite3` 原文件保留；新进程未打开旧 DuckDB。

| 事件保全检查 | 结果 |
| --- | --- |
| 原 44,422 条 signal_event，全字段及原 payload | 缺失0、改变0 |
| 原历史子集 JSON 数组 SHA256 | `98ede962501f99c056735c56b6422cae568896dec151a1621bd346433c7ced5c`，与旧基线一致 |
| 本次停机前 44,423 条（旧版本今天新增1条） | 缺失0、改变0 |
| 本次基线子集逐行全字段 SHA256 | `5a7ea2146342dab29414e6dba705700fb4dc14e456a820f437cd9c07a96911fe`，切换后相同 |
| 检查时仍处旧源缓存的已锁结构 | 1,119 / 1,119 原结构保持一致 |

15:51 截面：`signal_event/event_risk=44,431`、`symbol_state=5,892`、`scan_cycle=342`、`day_state=76,567`。新版新增8条均标为 **REPLAY**，来源为 TickFlow，单独计数；后台预热继续后计数随真实提交增加。原确认的 event_id、first_seen_at、防守和 payload 保持原样。

## 真实数据和页面

四只样本请求18个真实交易日的 raw 5m，三只正常历史各864根，新股96根。完整新源的三个250根窗口分别用冻结 pipeline 只读重放，输出与相同窗口的旧源重放一致。`001317.SZ` 从250根真实预热后选取11月9日至11日验证段，原库和新源重放均有3条 BUY，event_id、确认/支撑时刻、支撑低点、确认价、防守价、形态和 raw_tick 全部一致。更早的持续波段记忆不能由空状态250根重放完全复现，已由原 SQLite 状态和不可变确认保留。

| 样本 | 实机 HTTP v2 结果（15:51截面） |
| --- | --- |
| 000001.SZ | TickFlow READY；15/30/60m=250/125/62；最新15:00；MA233点数18/0/0；MA55点数196/71/8 |
| 001317.SZ | TickFlow缓存250根至当日10:00；后续存在非法 OHLC，SOURCE_UNAVAILABLE，暂停新BUY，保留历史图和原确认 |
| 002152.SZ | 缓存250根至14:30，存在真实缺口及非法 OHLC，SOURCE_UNAVAILABLE；除权因子投影与 SDK forward OHLC 最大误差约1.78e-15 |
| 688827.SH | 新股历史32根、缺因子键，FACTOR_PENDING / RAW，MA233无值、不出BUY |

000001、001317 的历史 QFQ 与 SDK `adjust='forward'` 比较最大 OHLC 误差为0。普通样本另以真实完成历史请求依次推进14:30、14:45、15:00，始终≤250根。现场返回的非法 OHLC（high/low 与 open/close 不一致）逐条隔离，不修造价格；合法历史和其他证券继续提交。

实机浏览器检查 `/lan-kline`、`/lan-ma55`、`/lan-theme`：15m真实蜡烛、成交量和MA233可见；30/60m禁用MA233并说明样本不足；源出错时显示已有行情及具体错误；历史股票名可跳到原15m确认、分别标记支撑和BUY。深链保留 event_id/source/因子/风险字段。跨源基准未经证实时，原防守文字保留、价格线隐藏。题材页原发布契约继续读取历史BUY。

## 限额及压力

实际探测：冷参数8证券×末尾3根、增量100证券×3根均各发出一次真实HTTP，零429；有效返回分别为6/84证券、11/150根合法5m，其余为缺响应或非法OHLC，均按证券隔离。另有4证券×864根的深窗口实测。手工有界 SDK 验证共13次实际HTTP；15:51运行截面 producer 21次HTTP，其中9次5m、零429。MA55/Kline自有商业分钟请求均为0。

默认插件分钟8 RPM（可配置上限12）、冷请求2 RPM；一个SDK owner、每次HTTP前查官方公开任务状态，原生任务活跃时让出额度，429按Retry-After且至少60秒退避，SDK不自动重试。5892证券的暖增量最多59个100证券批次，8 RPM名义约7.4分钟；冷请求和其他客户端共享账号会增加等待。官方60 RPM/200证券是参考配置，账户真实RPM上限和跨进程同时抢额的最坏429数量 **NOT_VERIFIED**；没有公开的跨进程预约契约，未调用私有限速器。

| 合成压力：5,600×250（1,400,000根） | 测量 |
| --- | --- |
| 写入时间 / 吞吐 | 30.41秒 / 46,034根每秒 |
| SQLite文件 | 159,424,512字节，约152.0 MiB |
| 独立基准进程RSS峰值 | 25,518,080字节，约24.3 MiB |
| 并发读取 | 1,768次，p95=8.89ms，读取错误0 |
| WAL写锁获取补充测量（100次BEGIN IMMEDIATE，并发持有读事务） | p95=0.0202ms，最大0.0567ms，错误0 |
| 重启和裁剪 | quick_check=ok，最大250，重复writer被lease拒绝 |

旧现场 DuckDB 曾在8GB配置下OOM，旧任务将其提高至16GB；本次用SQLite保存单套15m。以上24.3MiB为独立合成基准进程，和整宿主RSS分开；生产15:51宿主RSS约2.82GiB、SQLite主文件约199.1MiB。不同工作负载不计算加速或内存改善倍数。

## 定向验证

| 验证 | 结果 |
| --- | --- |
| 公共行情 pytest（协议/真实SDK MockTransport/认证/迁移/250/MA233/时段/缺口/纠错/因子/重启/WAL/429/任务避让） | 21 passed；上游Starlette弃用警告1条 |
| MA55 tests_backend | 19 passed / 26 skipped |
| Kline 后端 unittest | 4 passed |
| Theme 后端 pytest | 23 passed |
| MA55 Node | 41 passed / 2 skipped |
| Kline Node（最终源） | 24 passed |
| Theme Node | 15 passed |
| Ruff、git diff --check、核心/冻结文件差异检查 | PASS |
| 三个前端构建、四个ZIP、官方预检/候选内容/安装、重启版本读回 | PASS；ZIP Python/manifest 与当前源码逐文件一致 |

缺私有冻结包和指定研究参考对象的26项 Python / 2项 Node oracle 测试标记skip；冻结内核未改，仓内固定场景和上述真实250根BUY重放已验证。真实停牌状态无法从本次报价确认；停牌/缺K处理用固定输入覆盖，真实停牌现场 **NOT_VERIFIED**。现场切换在收盘后完成，下一交易日盘中持续刷新 **NOT_VERIFIED**。全市场新源覆盖仍在后台队列中恢复；15:51已有55只新源、5837只旧源缓存，47只缺tick暂停BUY。题材页沿用既有 `BLOCKED_AUTHORIZED_KPL_DATA` 状态，等待合法快照。

## 使用与回退

用户只需刷新已打开的网页。当前主页面 `/lan-kline`、`/lan-ma55`、`/lan-theme` 已可用。正常启动须让后端继承用户本地 `TICKFLOW_API_KEY`；本次重启已显式继承，无需把Key填入聊天或网页。

需要回退时，先正常停机，四包协调恢复上述 previous_version 后重启；旧版行情仍使用保留的 `rolling.duckdb`。MA55数据库schema保持兼容，新增确认也保留。只有明确需要恢复某个时间点时才使用受限目录中的一致性备份，先另存回退前新事件；不要直接覆盖当前事件库。原数据、设置和旧分支均保留。
