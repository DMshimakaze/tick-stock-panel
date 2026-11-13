# MA55 统一库、BUY 图表与 Expert 刷新验收（2026-11-12）

统一库、历史保全和图表修复已安装运行。5600 股离线联合负载的两个新增 15m 桶均在 900 秒目标内完成。盘中真实联合负载、供应商共享账户总限额及长期 RSS 平台仍为 NOT_VERIFIED；当前生产覆盖尚未全部预热。

交付分支：`feature/tsp-ma55-unified-db-stress-20261112`，个人仓库 `DMshimakaze/tick-stock-panel`；起点 `58b4187ae3181adf73849ccab9c1c072d5e48194`。宿主保持 `v0.3.36` / `a7868b783fc6057913bbc0a8d2b69817ad9b7d9b`。执行环境为现有 `D:\Python_Envs\envs\nga\python.exe`、TickFlow SDK 0.1.25。

## 实现与数据保全

唯一在线个人库：`E:\tick stock panel\data\user_data\lan_ma55\state.sqlite3`。链路为 lan.ma55 单 worker / 单 TickFlow raw 5m 适配器 → 完整三根聚合 15m → 同一 SQLite 连接提交行情、冻结策略、风险及收据 → 官方 Ext Data 发布。lan.kline 经本机 HTTP v1 只读；lan.theme 继续读取发布结果。

`market_store.py` 管理原始窗口，`market_grid.py` 负责聚合/前复权投影，`collector.py` 管理请求，`unified_store.py` 负责原子提交，`runtime.py` 调度。每股完成 15m ≤250、未完成 5m ≤2；持久结构锁独立于行情淘汰。HTTP 为只读连接；WAL、唯一 writer lease、检查点和有界请求收据均保留。最新桶优先，冷预热默认 2 RPM，包含在总分钟 8 RPM 内，上限 12；同股同目标合并，不无限重试。

v0.3.36 的公开 minute-range 是单股最多 20 个保存交易日的展示投影，无法提供此处所需的完整原价、逐股来源和全市场批量读取契约；分钟批量路径还能触发商业补拉。因此本轮保留一个真实 5m 适配器，没有增加个人 1m 采集。运行时代码不导入宿主私有行情模块，不读取官方内部 Parquet 格式。官方 Parquet/DuckDB 保留。

正常停止服务后，以原 MA55 SQLite backup 为底幂等迁入 `bar15/tail5/factor_event/instrument_state/meta`；原业务表保留。迁移候选重复执行通过，迁入 1,395,137 根 15m，容量最大 250，quick_check=ok。原 collector 的三张无读取者旧缓存表从候选退出，原内容仍在快照中。

18:23:32 的最终生产回读：原 **44,536 signal_event + 44,536 event_risk** 全字段及 JSON 逐条哈希相等，改变 0 行；新增各 129 行，全部 REPLAY，合计各 44,665 行。quick_check=ok，最大 bar15=250、tail5=0。只发现后端 PID 8840 打开个人生产库及其 WAL/SHM；旧 rolling15.sqlite3、rolling.duckdb、workbench.sqlite3 已移入离线归档，原在线路径不存在。

受限本机资料根目录为 `data/private/unified-db-20261112/`：一致性快照、原文件/WAL、配置、每行比对证据、测试日志、合成压力库及截图均只存本机。旧 MA55、行情和工作台原文件在 `inactive-originals/`。首次停机在 Uvicorn 完成 shutdown 后出现 Python 原生终结错误；备份与其后重启的 quick_check、完整旧行比对均通过。

`engine.py`、`pipeline.py` 对起点差异为零；`backend/app/`、`frontend/src/`、`dev.ps1` 对官方 tag 差异为零。研究仓库只读，固定 oracle 来源为 `DM-Ma55-Method@5d1d0f6e8913f528110c93e054429f050b9851f6`，参考 Git 对象副本保存在本项目私有目录。

## 图表与公开接口

- 主图只有蜡烛、成交量、事件和经基准核实的防守价；移除 MA 曲线及旧偏好回灌路径，MA55/233 仅有图外读数。
- 所有持久 BUY 经事件分页读取，用 Lightweight Charts 5.2.1 公开图元在价格/量柱两 pane 绘制 `#ff7383` / alpha .08 背景，保留 BUY 箭头及次要支撑标记；页面不计算 BUY。
- 本机实图：000338.SZ 保存窗口有 6 条 BUY、另 1 条窗口外确认。验证滚轮、平移、明暗主题、430×900 窄屏、900×430 横屏、日期范围跨周末、两窗口开关及重载。股票名称深链定位 2026-11-05 的原确认，仍显示其他 BUY；2026-10-21 的窗口外深链明确提示，不补造 K 线。截图仅在私有 `screenshots/`。
- 旧 QuantDash 事件与 TickFlow 基准不一致时保留原文字并提示防守线未显示。15m/30m/60m 实图分别 250/125/62 根；30/60m 不画 BUY，MA233 无值。
- 新 `/api/extensions/lan.ma55/v1/{status,universe,bars,events,inventory}` 仅 GET，bars 限 32 股/8000 根、events 限 200 行/页，支持 symbol/start/end/rule_version，保留旧事件 ID/确认/首次时间/风险。沿用宿主本机访问或面板 tf_session；API Token 不能替代面板会话访问自定义路由。工作台使用正式 HTTP，题材页使用官方 Ext Data。
- 清除已无引用的工作台/旧源 collector、重复 store 及旧 MA55 浏览器 BUY 引擎。行情测试迁入 MA55；冻结 Python oracle 保留，旧未使用的浏览器算法测试不计入当前通过数。MA55 页面改为显示真实 5m/HTTP 请求数和待预热数。

## 离线联合负载（零商业 HTTP）

正式产物 `stress-acceptance/`。`tools/run_stress.py` 用统一库、SDK 注入 transport、官方实际 quote/minute/depth 路径及 fake clock 连贯执行 1800 秒计划，再走真实 15:02 定版路径。没有把 UI 刷新机械展开为供应商请求。官方分钟合并/文件写入发生在隔离目录；quote 最终 enriched sink 只计数，因此完整 enriched CPU 与浏览器查询重绘不在本次测量内。

5600×250=1,400,000 根行情；5600 symbol_state、44,800 事件及风险、78,400 day_state、1,915,200 cycle_member、28,000 rule_state_archive。两个新桶与官方请求重放、并发 HTTP 读者及常驻 SSE 同时运行。

| 新 15m 桶 | 实际提交墙钟 / 吞吐 | 配额时序到齐 | 保守相加 / 900s目标 | 事务 p50/p95/p99 ms | 锁等待 p99 ms | checkpoint ms |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 225.165s / 24.87股/s | 478s | 703.2s / PASS | 6.36 / 39.65 / 353.60 | .0523 | 235.28 |
| 2 | 278.996s / 20.07股/s | 440s | 719.0s / PASS | 6.98 / 39.70 / 727.06 | .1386 | 407.64 |

配额调度最迟提交等待为 7.97/7.33 个交易分钟；保守把 CPU 墙钟再相加为 11.72/11.98 分钟。两轮目标桶都到齐，结束时相对各自目标桶滞后 0 根。随后注入故障后的 5596 READY 股水位不早于第二目标桶 14:00，其中 1 股另完成 14:15 短尾测试；GAP、FACTOR_PENDING、INVALID_5M_OHLCV、SOURCE_UNAVAILABLE 各 1 股，均停止新增 BUY。

| 并发客户端 | bars+events 读取数 | p50 / p95 / p99 ms | 常驻官方 SSE 连接 | 读取错误 |
| --- | --- | --- | --- | --- |
| 1 | 24 | 62.49 / 73.84 / 73.94 | 4 | 0 |
| 4 | 96 | 331.30 / 383.08 / 620.22 | 16 | 0 |
| 8 | 192 | 719.25 / 766.87 / 782.75 | 32 | 0 |
| 16 | 384 | 1629.31 / 1802.75 / 2076.15 | 64 | 0 |

工作台最多 16 槽，个人 producer/writer 各 1；队列峰值 5500，边界 5600。无锁错误、死锁、重复事件或损坏；44,800 原合成事件不变。短尾峰值 2，结束 0；行情最大 250；纠错、因子变更、缺 K、无响应、午休/非交易日/收盘后、更旧行情淘汰均通过；重启收据相等且 quick_check=ok。

DB 667,840,512 bytes，采样 WAL 17,357,592 bytes；进程峰值 RSS 1,281,601,536 bytes（1.19 GiB），结束 1,212,985,344 bytes；CPU 1052.23s，总墙钟 897.82s（含 seed 180.77s）。额外固定 184,800 行、200 轮官方分钟合并无商业流量，行数不变，RSS 峰值 789.69 MiB；第 50–100 轮均值 695.03 MiB，末 50 轮 748.88 MiB，末段范围 145.42 MiB。未出现 OOM；有限重放尚未建立长期 RSS 平台，记 NOT_VERIFIED。

## 刷新与额度模型

生产配置前后逐项相等：实时行情开/1s、全量分钟开/3s、分时图 3s、连板自动修复开/10s、15:02 定版。看板/自选/连板/概念行业四组 SSE 均有效开启（未显式保存的项沿用前端默认开启）。离线采用相同计划：quote 1800 轮、minute 600 轮、分时视图 600 次、depth 180 轮、定版 1 次。

| 能力 / 实际请求路径 | 请求数 | 滑动60s峰值 | 平均/最大标的批次 | 运行时缓存参考 RPM | 独立池模型 |
| --- | --- | --- | --- | --- | --- |
| quote.pool | 1800 | 60 | 5600 / 5600 | 120 | PASS |
| quote.batch（指数） | 1800 | 60 | 9 / 9 | 300 | PASS |
| intraday.universe（增量） | 598 | 20 | 5600 / 5600 | 20 | PASS（恰到上限） |
| intraday.batch（两次修复脉冲） | 56 | 28 | 200 / 200 | 60 | PASS |
| depth5.batch | 362 | 12 | 90 / 100 | 60 | PASS |
| MA55 kline.minute.batch / 5m | 114 | 8 | 98.25 / 100 | 60；插件8 | PASS |
| MA55 adj_factor | 2 | 2 | 8 / 8 | 120 | PASS |
| 所有 HTTP 共享池保守合计 | 4732 | **188** | — | 账号共享限额未知 | NOT_VERIFIED；若共用120则FAIL |

全量分钟 600 轮中 598 次增量和 2 次 repair；repair 每次 28×200 股。quote 每轮两个实际 HTTP 合并输出 5609 标的；depth 的实际 SDK 把 180 股分成 100+80，两次 HTTP。分时视图与 SSE 自身不增加 SDK producer。模拟响应均 200，429=0，重试=0；这不能确认真实账号跨端点限额。

每分钟柱状数据（区间行给出区间内最小–最大值；完整滑窗/逐请求记录留在私有 JSON）：

| 计划分钟 | quote池+指数 | 全量分钟增量 | repair | depth | MA55 5m | 因子 | 总 HTTP |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 120 | 19 | 28 | 12 | 8 | 0 | 187 |
| 1–6 | 120 | 20 | 0 | 12 | 7–8 | 0 | 159–160 |
| 7 | 120 | 19 | 28 | 12 | 5 | 2 | 186 |
| 8–14 | 120 | 20 | 0 | 12 | 0 | 0 | 152 |
| 15–21 | 120 | 20 | 0 | 12 | 7–8 | 0 | 159–160 |
| 22 | 120 | 20 | 0 | 12 | 3 | 0 | 155 |
| 23–29 | 120 | 20 | 0 | 12 | 0 | 0 | 152 |
| 75（单独跳至15:02定版） | 0 | 0 | 0 | 2 | 0 | 0 | 2 |

官方真实调用路径的模拟整轮 p50/p95/p99：quote 23.32/46.72/59.10ms，minute 636.39/717.86/773.62ms。模拟 transport 的 HTTP 处理分位数分别为 quote.pool 6.85/27.00/33.29ms、quote.batch .088/.114/.161ms、intraday.universe 40.82/54.81/62.79ms、intraday.batch .882/1.443/1.671ms、depth .206/.269/.365ms；这些是本地响应处理时间。个人压力 trace 未单独计 transport 延迟，其端到端提交分位数见上表。

## 有界真实验证、安装与当前覆盖

17:55 真实 HTTP 冒烟只用已授权本机凭证，硬限 12、max_retries=0，实际 **3 请求**：instruments、ex-factors、5m batch 各一次、每批 4 股，全部 200；延迟 422/297/281ms，429=0、重试=0。服务未返回额度响应头。使用私有隔离库，已有 14 条样本事件全列不变，新增 6 条 REPLAY，quick_check=ok。

| 样本角色 | 标的 | 完成15m | 因子事件数 | 结果 |
| --- | --- | --- | --- | --- |
| 普通股票 | 000001.SZ | 250 | 30 | READY |
| 真实历史BUY | 000338.SZ | 250 | 37 | READY |
| 真实除权 | 002152.SZ | 250 | 21 | READY |
| 新股/因子缺失 | 688827.SH | 48 | 未响应 | FACTOR_PENDING，analysis_asof=null，无新BUY |

经官方扩展管理器 inspect/安装、构建前端导出契约核验并正常重启；HTTP 与页面回读：

| 扩展 | 安装/运行版本 | 状态 / 回退资料 |
| --- | --- | --- |
| lan.ma55 | 0.5.2 / 0.5.2 | RUNNING；previous 0.5.1，原0.4.0包及原安装记录仍留本机 |
| lan.kline | 3.1.0 / 3.1.0 | READY，个人SDK请求0；previous 3.0.0 |
| lan.theme | 1.0.5 / 1.0.5 | 已启用；previous 1.0.4 |
| lan.marketdata | 2.0.0 / 无运行版本 | 官方管理器已停用、无 pending；仓库另有2.1.0无collector退役包 |

18:23:32 生产状态：5892 股中 READY 529、REBUILD_PENDING 5294、FACTOR_PENDING 7、GAP 17、SOURCE_UNAVAILABLE 45；TickFlow 553 股、QuantDash 迁移历史 5339 股；待预热 5363，队列 5021/8282。source_asof 与 analysis_asof 最新值均为 `2026-11-12 15:00:00`，仅说明已到达的最新水位。47 股缺报价 tick、上市状态范围仍未完全确认。

本轮安装后累计保存 36 次因子 +36 次5m生产请求收据，均200、重试0；这些是正常低速预热，不与上述3次专门冒烟混算。最终一次启动的计数为14次5m/28次HTTP、429=0。官方实时行情仍 enabled/1s；官方分钟 enabled/3s，`outside_trading_hours`、rounds=0。没有真实交易时段联合负载证据。

## 验收结论与复现

| 闸门 | 结论 | 直接证据 |
| --- | --- | --- |
| 原历史完整、单库、唯一个人owner | PASS | 全44536旧事件及风险逐行一致、最终文件句柄、旧路径归档、writer lease测试 |
| 冻结规则与真实250窗口 | PASS | 后端87通过/1跳过，24个私人冻结窗口对照指定Python oracle；真实滚动250与结构锁跨淘汰重启通过 |
| 图表和公开只读契约 | PASS | Node MA55 5、K线27、题材15通过；本机实图/深链/窄屏/缩放/主题/窗口外提示 |
| 5600股离线轮次与有界队列 | PASS | 两轮保守703.2/719.0s，读并发/64SSE、容量/恢复/quick_check通过 |
| 账号独立能力池参考模型 | PASS | 各池峰值未超运行时缓存参考值，未代表服务端授权确认 |
| 共享账户池、盘中实际1s/3s、长期RSS平台 | NOT_VERIFIED | 缺共享额度证据；执行时盘后；有限重放内存仍波动 |
| 全市场可分析覆盖 | FAIL（尚未预热完） | READY 529/5892，保留迁移历史并按原冷预算继续预热，缺因子/缺口暂停BUY |

唯一跳过为未挂载的私人 Tuowei 输入；已使用其他可读真实冻结案例，未将合成数据当 oracle。Python 测试有一条现有 Starlette/httpx 弃用警告；依赖未升级。最后界面状态计数修正后 MA55 Node 5 项再次通过。相关 Python ruff 与 git diff --check 通过；三个前端包已构建，未执行全量宿主测试或改动宿主。

运行后端定向测试需设置 `MA55_REFERENCE_REPO` 为私有指定提交副本、`MA55_FROZEN_PACK` 为只读真实数据包、`MA55_PYTHON` 为 nga 解释器，然后执行：

```powershell
& 'D:\Python_Envs\envs\nga\python.exe' -m pytest addons/lan_ma55/tests_backend addons/lan_ma55/tests_market addons/kline_workbench/tests/test_backend.py addons/lan_theme/tests_backend/test_identity_package.py -q
pnpm --dir addons/lan_ma55 test
pnpm --dir addons/kline_workbench test
pnpm --dir addons/lan_theme test
& 'D:\Python_Envs\envs\nga\python.exe' addons/lan_ma55/tools/run_stress.py --output '<新的私人目录>' --symbols 5600
```

快速包回退使用官方管理器 previous_version 并正常重启；若需回到原双服务拓扑，先停机另存本轮新库和配置，再使用私有一致性快照及 `configs/<扩展>/state.json`、仍保存的原版本包恢复成套状态。不可只换旧二进制并丢弃本轮新增记录。未执行回退，当前新版服务保留运行。提交仅含扩展源码、定向测试及本报告；密钥、数据库、商业返回、日志、截图、ZIP/dist 与闭源依赖留本机。
