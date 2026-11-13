# MA55 盘中刷新与原生状态面板验收（2026-11-13）

盘中目标推进、真实状态投影和页面验收 **PASS**。后台在北京时间11:31启动后，自动从11:15推进到11:30，真实5m采集、完整15m保存、冻结MA55计算、Ext Data发布和浏览器展示均已观察。全池行情/因子齐套仍为 **PENDING**，页面保留真实覆盖数，没有标记 ALL_CURRENT。

开发基线为个人分支 `feature/tsp-ma55-etf-compact-complete-ma-20261112` / `311e47e19f46b5e46e56fe5c86ba109366afa64e`，开始时 fetch personal 后本地与远端一致。本轮分支为 `feature/tsp-ma55-live-status-native-ui-20261113`。origin 指向官方仓库，实际交付使用指向 `DMshimakaze/tick-stock-panel` 的 personal remote。

实际安装为 **lan.ma55 0.7.3**；TSP保持 **v0.3.36**，`requires_app >=0.3.36,<0.3.37` 不变，lan.kline **3.2.1**、lan.theme **1.0.7**不变。使用既有 nga Python、TickFlow SDK 0.1.25，未升级依赖。只修改MA55插件、定向测试和本说明。宿主核心对v0.3.36无差异，`engine.py/pipeline.py` 对开发基线无差异，研究工程只读。

根因证据链如下：

1. **触发**：旧worker在11月13日00:00请求指数日K；当时交易日集合截至11月12日，却把 `metadata_day` 写成11月13日。
2. **路径**：旧 `runtime.py` 将日历刷新放在 `last_master_day != now[:10]` 内。当天后续循环跳过日历，`latest_point(sessions, now)` 始终选中昨日15:00，采集器继续低优先级历史任务。
3. **观测**：11:08 `/status` 为0.6.3、worker存活、目标/数据/分析均11月12日15:00，日历最后日期11月12日；官方投影的发布时间已到11月13日11:08，`pending_publish=null`、`publisher_error=null`。宿主 `/api/pipeline/jobs` HTTP200、`active_id=null`，插件429次数为0。因此页面缓存、发布失败和宿主忙碌都不是本次根因。
4. **源端反例**：正常停机、备份后，以原账号、单个临时owner执行两次有界HTTP：11:15的指数日K已含11月13日；指数和一只股票均有当日至11:10的有效5m。指数两个无效OHLC行被拒收，未填补。源端当前有今日数据，旧插件因未重查而没有使用。
5. **修复反例**：注入真实SDK transport验证“日K仍截止昨天，但有效完成5m确认今天”，10:52可进入已核实的10:45目标；生产运行又实际观察到11:15→11:30自动推进。

11月13日为星期五，公开休市安排未列该日；运行中的当日完成行情提供了直接交易证据。参考[上交所2026年休市安排](https://www.sse.com.cn/disclosure/dealinstruc/closed/list/)与[深交所年度通知](https://investor.szse.cn/disclosure/notice/general/t20251222_618087.html)。插件没有内置工作日推算表，也未引入新的日历账号；当前宿主/SDK未提供可直接复用的公开交易日历契约，因此使用原授权源的已完成5m确认。盘前、周末或未得到有效行情时保留最近有效交易日。

主要实现入口：

- `collector.py`、`runtime.py`：独立日历检查、五分钟有界重查、宿主任务失败分类；新目标先核实日历，重启补算只消费真实已到达的目标K线。新完成桶仍优先于冷队列和深历史。
- `market_store.py`、`unified_store.py`：在同一只读事务中分别统计目标K线到达与实际算法输出，支持有效 `collection_symbols` 范围；扫描证券维表并按 bar15 主键查找，不扫描百万行原始K。
- `runtime_status.py`：目标、保存、分析、发布各自水位；采5m/聚合/计算/发布/等待/受阻阶段；历史任务与盘中状态分开，历史额度等待不遮蔽盘中源缺口。新库尚无元数据时数量保持未知。
- `shared_store.py`：generation变化后按当前cycle重新确认处理资格，原确认不删除。`day/history/prices_v1` schema不变，新增状态仍放在原 `lan_ma55_summary.payload_json`。
- `src/status-view.mjs`、`styles.mjs`、`results.mjs`：原生令牌、六项KPI、双进度、独立历史队列、260px搜索、发布失败保留旧结果、过期状态提示；输入焦点、三列表、日期、分页、ETF三位价格与原深链保留。

11:59:28的实际运行快照如下，后续后台继续更新：

| 事实 | 实测 |
| --- | --- |
| 有效监控股票 / ETF | 5,892 / 1,769，共7,661，来自当时有效范围 |
| 已核实目标 / 最新保存 / 最新分析 | 均为2026-11-13 11:30 |
| 目标K线完整到达 | 3,760 / 7,661 |
| 目标MA55实际完成 | 1,858 / 7,661 |
| 最新交易日已保存BUY | 431，非历史累计数 |
| 最近成功状态发布 | 11:59:23，API/官方投影均为0.7.3 |
| 当时任务 | COLD，冷预算等待；盘中数据仍部分待补 |

真实链路收据：11:31:01核实日历，11:31:12发出完成5m批量请求；首轮cycle保存11:15目标的100个处理结果。11:31:19开始新11:30目标的请求，样本11:30原价15m均以 `TICKFLOW_RAW_5M / source_count=3` 保存，处理收据已分析至11:30；随后官方投影和页面均显示11月13日。此段自动推进发生在浏览器“刷新展示”之外。

独立核对使用一只真实股票：已捕获raw5独立聚合的6根15m、36个OHLCV/amount值与保存结果一致。按实际11:15、11:30两个250根输入窗口顺序重放，冻结核心的结构、决策、序号、55根特征历史与生产状态一致，确认ID一致；跨来源风险依原适配规则保留，风险输出一致。仅重放最终裁剪窗口会漏掉首轮已经处理的一个格点，因此验证保留了两轮输入顺序。15/30/60m分别有250/125/62根可见K，所有六MA有值，未重跑全市场均线压力测试。

一致性备份为停机后的SQLite backup API快照，升级没有新增表、迁移BUY或重建行情库。11:53只读一致性快照的比较结果：

| 表 | 升级前 | 快照时 | 删除旧行 | 改变旧行 | 新增 |
| --- | ---: | ---: | ---: | ---: | ---: |
| signal_event | 48,441 | 48,872 | 0 | 0 | 431 |
| event_risk | 48,441 | 48,872 | 0 | 1,658 | 431 |

原确认全字段保持一致。风险变化为真实新K线推进产生的 `age_bars/data_asof/invalidated_at/extended` 等跟踪字段；1,652条来自既有TickFlow事件，另外6条旧来源风险对应两只已 `OVERLAP_VERIFIED` 的证券，更新日期均为11月13日。原风险事实没有删除；没有把风险随行情正常变化误报为“全表字节未变”。升级前后 `PRAGMA quick_check=ok`。

实际验证结果：

| 验证 | 结果 |
| --- | --- |
| nga Python MA55 tests_backend + tests_market，挂载既有只读冻结参考和输入包 | **116 passed / 1 skipped**；跳过私人拓维个案未挂载，另有既有Starlette弃用警告 |
| MA55 Node测试 | **13 passed** |
| Vite build、nga build_backend.py、相关Ruff、git diff --check | **PASS** |
| 日历缺当日日K、10:52目标、午休/收盘/周末/休市空响应、五分钟重查 | **PASS**，注入transport与纯函数验证 |
| 宿主busy、401/403/404/429/500、超时/连接/JSON错误、发布失败与恢复 | **PASS**，未把模拟响应作为实盘证据 |
| 有效范围、股票/ETF独立计数、到达与计算分离、generation变化、未知元信息 | **PASS** |
| 真实盘中目标自动推进、真实保存/分析/发布/页面 | **PASS**，上述北京时间收据 |
| 1366 / 900 / 390px，各明暗主题 | **PASS**；六/三/两列响应式，桌面搜索260px，页面无横向溢出 |
| 日期、回车搜索、清除、三个Tab、第二页、整行键盘深链、ETF精度 | **PASS** |
| Kline三周期、六MA与原事件参数，Theme读取新状态 | **PASS**；跨交易日旧BUY保留原有基准核实提示；Theme仍明确需要授权快照 |
| 官方管理器安装、正常重启、实际安装文件与构建包/源码一致 | **PASS**；0.7.0～0.7.2为本轮中间版，最终0.7.3 |

日历核查仅两次供应商HTTP，0次429、0自动重试。其后为原owner正常的已授权盘中增量及有界冷队列，未发起全市场历史重拉。分钟预算8 RPM、冷预算2 RPM保持不变，插件无1m请求；浏览器轮询只调用官方Ext Data。账户跨宿主端点总配额仍为 **NOT_VERIFIED**。生产状态接口连续十次读取平均170.4ms、最大176.8ms，查询计划为证券维表扫描加 `(symbol,bar_end)` 索引查找。

本机证据位于 `data/private/live_status_20261113/`：`before-readonly.json`、`source-evidence.json`、`real-next-bar-chain.json`、`real-frozen-and-ma-verification.json`、`final-integrity.json`、`final-runtime-evidence.json`、`pytest-final.txt`、`layouts.json`、`results-ui-proof.json`、六档主题截图及最终面板图。原始行情、凭据、数据库、日志、ZIP、dist和截图全部留在本机，不进入Git。

剩余事项为源端缺完整5m/因子、尚未完成的冷/深历史队列及整池持续覆盖；页面显示对应进度和阻塞，不承诺全池已追平。没有合法题材快照的状态保持原样。

回退时正常停机，先用SQLite backup API保存当前新增状态，再通过官方管理器rollback并重启。previous是本轮中间版0.7.2；恢复任务前0.6.3组合可在停机后恢复私人目录保存的 `ma55-state.json` 到 `data/extensions/lan.ma55/state.json`，其0.6.3版本目录仍保留。数据库schema兼容；若另需恢复任务前数据，可使用 `before.sqlite3`，先保存新增事实。回退未执行。
