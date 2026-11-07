# MA55 V0.2 全市场后台与状态库：实际交付状态

2026-11-07，北京时间。**当前能否宣称全市场监控完成：NO。** 状态为 `FULL_MARKET_BLOCKED_BY_HOST_CONTRACT` / **尚未完成全市场监控**。

本轮完成插件自己的Python冻结内核、独立SQLite、离线单写者服务、只读结果接口、官方后端包及定向验证。真实TSP的v0.3.32、QuantDash、已安装浏览器观察版和用户数据保留原状；没有后台市场扫描，没有新增供应商请求或Key。

## 基线与边界

- Fork：`DMshimakaze/tick-stock-panel`；开发分支 `feature/tsp-ma55-full-market-state-20261107`，从已推送 `2b7596e7ac4cf0a8108f54c12aa908a09e8667b7` 建立。原MA55与QuantDash分支保留。
- 官方基线：v0.3.32，`ca3a44252edb414e3f4c1df1b7c381bad9f55c8b`。没有升级v0.3.33。
- 冻结规则：`MA55_15M_MINIMAL_V5`，权威生产提交 `23477622536d1206b2af3c164b4c905aa2e15e8d`；规则文件blob `0e777b61d9c33c9fdae4892508491e24824b0a5e`，特征文件blob `e682a304c493b1ff8c78a67980f51096056451ab`。原研究仓库仅按Git对象只读引用，未改其工作树或库。
- 所有业务改动在 `addons/lan_ma55/`。初始未跟踪 `REVIEW_SUMMARY.md` 原样保留。宿主 `backend/app`、`frontend/src`、`dev.ps1` 相对官方tag差异为零。

## 现场公开合同与供给状态

已核对实际 `ExtensionContext.repository`、Page Pack ctx.api、Token网关、minute-range/minute-batch及全量分钟服务调用链：

| 项目 | 现场结论 |
| --- | --- |
| 股票全集 | `GET /api/data/status` 的官方维表统计为 **5,890**，named同为5,890，维表日期2026-11-06；这是统计快照，不是可供扩展枚举的完整标的列表合同 |
| 数据统计时效 | `checked_at=2026-11-07T11:04:03Z`，`stats_pending=true`；minute统计中的rows/symbols_covered零值不能解读为真实零覆盖 |
| 全量分钟开关/线程 | `enabled=true`、`running=true`，配置间隔120秒 |
| 源及能力 | `provider=provider_effective=quantdash_user`、`repair_only=true`、`capability_ok=true` |
| 现场健康/门控 | `healthy=false`，休市日 `outside_trading_hours` |
| 已完成轮次 | `rounds=0`、`last_symbols=last_rows=last_requests=0`；`last_round_at/last_round_ms/last_mode/last_error/minute_basis_raw` 均为null |
| 真实覆盖与时延 | 主进程没有已完成轮次、没有正式批量只读消费接口，因此 **不可测/未知**；不报告0%或100%完成率 |
| 稳态廉价增量 | 当前QuantDash源码未实现 `get_intraday_latest`，修复读取回退至100股/批 |
| 默认请求预算 | 源码为12次/滚动分钟，其中实时保留6次；本轮不改环境或预算设置 |

按统计快照5,890股估算需要59个100股批次，默认后台6次/分钟约为9.8分钟的预算量级，另有网络与落盘耗时。**这是源码预算估计，不是真实全市场一轮测量，也不保证15分钟低延迟。** 本轮没有触发手动全量轮、没有增加补拉或扫描。

合同阻碍仍然存在：只读扩展repository只有名称查询；无股票维表枚举及已复权分钟批量仅读接口；minute-batch可能补源且未对Token开放；页面无自身扩展API桥。最小需求见 [UPSTREAM_CONTRACT_REQUEST.md](UPSTREAM_CONTRACT_REQUEST.md)。未绕过这些边界。

## 已完成的独立实现

- `backend/engine.py`：原Python Rules/advance/minimal_features纯规则，无行情SDK、Pandas或外部MA55运行依赖。
- `backend/pipeline.py`：完成15m/会话/连续性/价格口径验证，56根预热、同波段锁、55根有界特征及每槽5份量；首次确认与后续风险分开，历史模式为REPLAY。
- `backend/store.py`：`symbol_state / signal_event / event_risk / scan_cycle` 四表；每次最多128股原子提交，首次事件不可覆盖，结果查询使用SQLite只读连接。规则不兼容重建派生态，旧确认按版本保留；无TSP数据迁移。
- `backend/service.py`：显式离线全集和分批输入、唯一写线程及有界队列；按完成点持久化分母、已处理、有效/不足/缺口/失败数量、源时点、处理耗时、完成时间和健康状态；中断后只跳过真正已提交的标的，重复轮次不生成新事件。
- `backend/extension.py`：官方setup/startup入口，仅注册 `/api/extensions/lan.ma55.state/` 下的只读status/events/symbols/cycles。当前status固定报告真实阻碍；没有定时器、后台市场数据接入或策略/告警中心接线。
- `extension.yaml / build_backend.py`：可重复构建官方v1后端包。只含6个已验证Python模块与清单，无前端或用户数据。
- 原Page Pack只增加显著阻断提示，保留原观察逻辑；当前页和IndexedDB没有改名冒充全市场。新构建可用，**本轮未覆盖真实TSP当前安装版本**。

安装产物均在忽略目录：`dist/lan-ma55-state-0.2.1.zip` 与 `dist/extension.js`。后端包在隔离临时宿主中完成原生安装、启动、启停和卸载验证；真实宿主未装此包或重启。安装后的状态库固定在 `context.data_dir/user_data/lan_ma55/state.sqlite3`，不在安装版本目录；卸载代码保留状态库。

保留窗口 `retention_sessions` 默认60个交易日，可配置20–252，由明确输入的交易日日历裁剪旧事件/轮次。SQLite只存派生态、55根必要15m样本、同槽量、确认和风险，不存1m/5m/日K行情库。

## 实际测试与测量

Python解释器使用既有nga，已验证sys.executable；quantdash0.1.0、Polars1.44.1、FastAPI0.142.2。没有新环境或依赖升级。

| 命令/范围 | 实际结果 |
| --- | --- |
| `nga python -B -X utf8 -m pytest addons/lan_ma55/tests_backend -q -s -p no:cacheprovider`，挂载已有只读参考及冻结包 | 首轮 **38 passed，0 skipped**，47.81秒 |
| 增加完成时刻/缺口风险后，定向重跑 `test_store_service.py test_extension.py` | **12 passed，0 skipped**，12.85秒；后端共39个不同测试已覆盖通过 |
| `pnpm build` + `node --test tests/page.test.mjs` | 构建通过；页面 **9 passed，0 skipped**，含显著阻断文案及原观察能力回归 |
| Ruff：新增backend、tests_backend、build_backend.py | 通过 |
| 现有告警 | 测试依赖发出1条Starlette/httpx弃用提示，未因此更换依赖 |

真实规则回放：复用已有 `tests/oracle.py`，从指定Git提交导出临时原Python源码，实际调用权威 `analyze`，对照新Python后端决定、关键结构和所有事件身份/时刻/支撑起点/单多K分类。**15正向、7抑制、2否定窗口全通过，不是skip或JS镜像替代。** 6类合成规则链也逐根对照原Python。没有重新抓取私有行情或运行研究GUI/生产数据库。

SQLite/服务专项覆盖：5,000合成标的，单轮正确分母，稳态增量，同波段去重，唯一写者和两个客户端重复调用，跨连接只读分页，部分提交中断与重启恢复，缺数据/错误分计，旧确认冻结与后续风险，版本隔离与保留期，未完成点/周末/源过期阻断。午休连续性、因子缺失/变化及JSON滚动重启亦已验证。

**合成性能（不是实际市场数据吞吐）：**

- 5,000股，每股56根完成15m预热，计算并落库：**7,098.58 ms**。
- 随后一个完成点的5,000股增量与风险更新：**3,484.07 ms**。
- 临时SQLite文件：**54,013,952 bytes，约51.51 MiB**；每股55根必要输入，不是原始分钟库。该数据库只在测试临时目录，不提交，也未复制到TSP data。

**真实宿主有限仅读烟测：**

| 官方调用 | 返回 | 该次耗时 |
| --- | --- | --- |
| `minute-range(600519.SH,1)` | local，2026-11-06，1会话/241条 | 47.02 ms |
| `minute-range(588170.SH,1)` | none，0会话 | 5.97 ms |

这两次GET走已核实的本地仅读端点，不调用源补拉；前后minute-refresh均为rounds0、last_requests0、休市门控。**MA55本轮新增供应商请求为0**；该结论来自本轮调用路径，不把last_requests当供应商累计账单。没有全Universe读速、真实一个全市场完成点耗时或盘中源延迟实测，因为所需公开消费合同不存在。

## 剩余工作与交付判断

真实全市场数据接线、后台持续扫描、浏览器关闭后持续监测、多个浏览器共同查询正式后台结果、正式通知去重均 **受现有官方扩展合同限制，未实现**。本轮离线队列、状态库和恢复测试不冒充这些能力。

已有浏览器版仍可按其原范围观察；新UI产物显著显示“尚未完成全市场监控”。SQLite后端无行情接线，未与浏览器同时生产真实后台BUY。正式替换为纯结果页面必须等公开桥与数据接口具备后另行接线。

提交仅包含插件源码、定向测试、配置和脱敏说明。任务附件、凭据、数据库、真实行情、缓存、日志、ZIP/JS构建产物不提交。提交前 `git diff --check` 与相对官方核心零差异检查通过；最终分支和SHA以交付回执及该文件所属提交为准。
