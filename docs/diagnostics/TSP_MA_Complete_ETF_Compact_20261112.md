# 完整滚动均线、ETF 与紧凑页面验收（2026-11-12）

实现、安装和定向样本验收通过。15/30/60m 从足额真实历史计算六条均线后才裁剪可见窗口；滚动淘汰与重启持续保持首根 MA233。MA55 页面采用两项状态、三列结果，股票与沪深 ETF 使用同一采集和冻结分析链。整体验收为 **NOT_VERIFIED**，待核实项列于文末。

个人基线为 `feature/tsp-kline-ui-unified-20261112` / `f0b7470fbf5987b3814d9b0e352083dcc14aa75d`，开始时 fetch 后远端一致。开发分支为 `feature/tsp-ma55-etf-compact-complete-ma-20261112`，实现终点为 `0134a14c432ca6f90d4f3d9700f868bf836f4813`；交付还包含本报告所在的后续提交。宿主保持官方 v0.3.36 / `a7868b783fc6057913bbc0a8d2b69817ad9b7d9b`。只读 fetch 的 upstream/main 为 `144f921b8c5f8388ee8d8a367db8c8f099ed1cad`，本轮未合并上游。

正式运行版本为 **lan.ma55 0.6.3、lan.kline 3.2.1、lan.theme 1.0.7**。退役 lan.marketdata 维持禁用；其实际 active 为 2.0.0，2.1.0 仅作为已保存版本存在。版本、Page Pack export、构建入口和安装/runtime 记录已核对。期间修改均使用新版本安装。

改动集中于以下入口：

- `addons/lan_ma55/backend/market_grid.py`、`market_store.py`：容量公式、同库 raw15 保留深度、完整投影后裁剪、独立历史 UPSERT、逐周期准备状态。
- `history.py`、`collector.py`、`runtime.py`、`unified_store.py`、`shared_store.py`：同 owner 的历史队列、真实 ETF master、事务一致的 universe、250 根冻结输入、修订重放时保留旧风险。
- `result_publisher.py` 与 `src/results.mjs`：`lan_ma55_prices_v1`、两个可信状态、名称/价格/confirmed_at 三列、旧 generation 的已保存 BUY 保留。
- `addons/kline_workbench/src/data.mjs`、`page.mjs`、`chart.mjs`、`view.mjs` 和后端 bridge：交易格点连续、六线开关、精度与状态转发、跨周期信号提示。
- `addons/lan_theme/src/page.mjs`、`view.mjs`、版本入口：统一控件密度、折叠来源诊断和可见授权提示。
- 对应定向测试、`tools/complete_ma_smoke.py`、现有 `tools/stress_db.py` 及三个包的清单/README。

官方 `backend/app/`、`frontend/src/`、`dev.ps1` 对目标 tag 差异为零。`engine.py`、`pipeline.py` 对个人基线字节一致；研究工程只读。原有未跟踪 `REVIEW_SUMMARY.md` 保留。

容量合同为 `required_history(tf, visible) = visible + 232`。15/30/60m 默认可见数仍为 250/125/62，对应至少 482/357/294 根同周期完成 K。内部 raw15 容量取当前公式需要与 1,280 的较大值，并包含聚合边界余量。MA55 策略通过 `strategy=True` 单独读取最多 250 根，MA233 准备状态不进入 BUY 判定。

所有新 K、raw 修订及因子版本均从同一库读取。图表读路径重新投影后裁剪，来源、basis/factor revision、asof 和可见首末随响应返回；无需另存指标真源。历史任务按最多 18 个真实交易日分窗，当前每窗口最多 864 根 5m，低于请求 count=1200。已有历史不会被同源 rebuild 清空。回补不运行策略或修改尾部5m，失败的内部缺桶修复按五分钟冷却；上市/日历边界保留明确状态。

原 `signal_event`、`event_risk` 各 **46,321** 行，在停机一致性备份、原地 schema 演进和最终只读快照中逐行全字段哈希相等。23:20:47 的快照两表各 **46,964** 行，新增 **643** 行单独计数。新旧 SQLite `quick_check=ok`，唯一生产路径保持 `data/user_data/lan_ma55/state.sqlite3`；WAL、单 writer lease、同连接事务、失败回滚与独立只读连接均通过定向验证。

真实数学证据按样本角色脱敏。价格、原响应、首末精确数值和截图保存在本机私人目录，公共报告记录覆盖及误差结论：

| 样本 | 输入证据 | 改造前 MA233 有效点（15/30/60m） | 改造后 | 结论 |
| --- | --- | --- | --- | --- |
| A 成熟股票 | 实际 TickFlow raw5 + 日历 + 因子链 | 18/0/0 | 250/125/62 | 六MA全部可见点通过 |
| B 除权股票 | 实际 TickFlow raw5 + 真实事件链 | 18/0/0 | 250/125/62 | 六MA全部可见点通过 |
| C 沪市成熟ETF | 实际 TickFlow raw5 + 明确事件响应 | 无可用库存 | 250/125/62 | 六MA全部可见点通过 |
| D 深市ETF | 运行中同 owner 自动回补的 raw15；独立 SMA | 无可用库存 | 250/125/62 | 六MA全部可见点通过 |
| E 深市ETF受阻样本 | raw5 已足额；因子未提供 | 无可用库存 | FACTOR_PENDING | 保留原价、空 MA 和阻断原因 |

A/B/C 的 oracle 独立执行 raw5 格点聚合、QFQ 和 SMA；D 以保存的 raw15 独立计算 SMA，raw5 原响应未留存。每个合格样本检查 437 根可见 K × 6 个指标，四样本共 **10,488** 值，包含首根、第二根、中部、倒数第二根和末根。误差限为绝对 `1e-9`、相对 `1e-10`；零超限。全部首末值见 `real-smoke/evidence.json` 和 `sz-etf-oracle.json`，位于下述本机证据目录。

自动滚动测试先从旧 250 根库向前补齐，再连续新增 **1,280** 根完成15m；每一步检查三周期所有可见六 MA 有值。在 t=0/1/2/15/16/64/249/250/512/1280 逐点核对独立算术 oracle，第16/250步关闭并重开 SQLite。缺5m、修补、同源重建、因子变化、WAL 读隔离和 writer 冲突分别覆盖。合法午休、隔夜及假日由真实交易格点相邻关系连接，真实缺桶保留 whitespace/透明断点。

ETF master 使用安装中的 SDK 0.1.25 `get_instruments(..., instrument_type="etf")`，限 CN 的 SH/SZ，与原股票候选合并；fund、index、海外市场和未上市品种按元信息排除。tick_size、上市日和分类来自实际元数据，`[]` 与 `None` 因子响应分开处理；compact volume 固定按手转份/股，amount 为元。沪深样本实际最小 tick 均为 0.001。ETF 已发生真实冻结确认，并经过保存、三列列表、键盘整行打开和原 event_id 深链进入15m图表；无题材关系时保留未映射状态。

23:20:47 的单库快照口径如下。universe 为 generation=2，股票 **5,892**、ETF **1,769**，合计 **7,661**。当轮目标与最新完成 K 均为 **2026-11-12 15:00**，实际成功到达 **4,623 / 7,661**，页面显示更新中。

| 类型 | 原始行情状态 READY | 同目标已分析 valid | 因子待补 | 原始库存达到1280 |
| --- | ---: | ---: | ---: | ---: |
| 股票 | 3,445 | 3,423 | 26 | 2 |
| ETF | 190 | 165 | 921 | 3 |

“本轮已刷新”只统计当前目标时间确有同源完整 raw15 的去重证券；与策略处理尝试数分离。状态、分母和 generation 在同一个只读事务内取得，核心 SQL 为：

```sql
SELECT count(*) FROM instrument_state i
WHERE i.eligible=1 AND i.source_id=:source
AND EXISTS (
  SELECT 1 FROM bar15 b
  WHERE b.symbol=i.symbol AND b.bar_end=:target
    AND b.source_id=:source AND b.source_count=3
);
```

分母为同事务 `instrument_state WHERE eligible=1`，最新完成时间取真实已保存水位，另保留目标15m。该 SQL 经实测改为按主键索引查找，避免扫描七百多万行。已有确认按交易日保留，universe 换代不会删除其展示资格。

官方 `market-snapshot` 的默认 ScreenerService 读取股票日线 enriched。三列页面统一使用本库同日最近合法完成15m的原价收盘投影，tooltip 标明 bar_end/来源/口径；所选日无对应有效报价显示 `—`。旧 `lan_ma55_day` schema 保持原样，价格通过新的轻量视图发布，BUY payload 未加入展示字段。

视觉检查覆盖实际 `/lan-ma55`、`/lan-kline`、`/lan-theme`，暗/亮主题及390px窄屏：两个状态、三个表头、无横向溢出；搜索、日期/历史、分页、整行键盘深链和六 MA 勾选可用。K线保留15m淡红 BUY 时间列，30/60m为定位提示，未引入箭头、支撑文字或防守价格线。题材授权缺失保留一行明确提示，来源和统计默认折叠。首次看到的旧版和最终图表截图均只存本机。

5,600证券（含560只合成ETF）×1,280根原始15m的离线负载实际写入 **7,168,000** 行，包含历史事件/风险/状态；原完整集成运行覆盖两轮新完成桶、1/4/8/16读者、16图表槽和SSE，并使用注入 transport，商业请求为零。读者阶段有重叠，实测SSE峰值96。规模结果与前轮250根库比较如下：

| 指标 | 前轮250根 | 本轮1280根完整运行 |
| --- | ---: | ---: |
| DB字节 | 667,840,512 | 1,411,268,608 |
| 观测WAL字节 | 17,357,592 | 228,845,432 |
| 峰值RSS字节 | 1,281,601,536 | 1,298,812,928 |
| CPU秒 | 1,052.2 | 3,193.3 |
| 总墙钟秒 | 897.8 | 1,117.6 |

首次压力显示新增状态统计全表扫描，修复后的同规模读取复测结果如下；前8读者为读取负载，16读者与writer两轮并行：

| 读者数 | bars+events p95 ms | p99 ms | 错误 |
| --- | ---: | ---: | --- |
| 1 | 110.2 | 111.6 | 0 |
| 4 | 594.7 | 616.3 | 0 |
| 8 | 1,648.5 | 1,696.7 | 0 |
| 16 第一轮 | 3,940.4 | 4,050.8 | 0 |
| 16 第二轮 | 3,916.4 | 4,134.6 | 0 |

修复后状态快照约49ms，前值约1,064ms。两轮5,600证券writer耗时212.25/213.49秒，事务p50约10ms、p95约38ms、p99约1.3s，锁等待p99约0.12ms。原8 RPM队列模型两轮调度跨度478/440秒，与计算耗时合计均低于900秒。16读者压力下响应约4秒，三秒轮询会等待上一请求结束。复测中一次驱动跳过合成格点导致断言失败，修正驱动、按已知合成输入补齐后上述两轮零错误；失败记录保留在私人目录。最终 quick_check=ok。

有限商业冒烟为实际 **12次HTTP、5次5m窗口请求、0次429**，无自动重试；四证券各取得4,320根raw5。生产后续图表按需历史由同一owner完成。请求计数和状态收据已留存，首次受控运行未保留配额响应头，因此账户共享配额标为 **NOT_VERIFIED**。预算保持5m请求8 RPM、上限12、冷/深历史共享2 RPM；元数据/因子沿用30 RPM池，Kline SDK请求为0。官方设置实读为行情1s、分钟3s、盘中分钟3s、盘口10s，未修改这些设置。

实际验证命令及结果：

- nga Python `pytest addons/lan_ma55/tests_backend addons/lan_ma55/tests_market addons/kline_workbench/tests/test_backend.py addons/lan_theme/tests_backend -q`：**112 passed / 1 skipped**。冻结研究引用指定提交的只读副本和私人输入包；跳过项为未挂载的私人个案。已有一条 Starlette/httpx 弃用警告，依赖未升级。
- 三个插件 `pnpm test`：MA55 **6**、Kline **39**、Theme **15**，合计 **60 passed**；三个 `pnpm build` 和 nga `build_backend.py` 完成。
- 相关 Ruff、`git diff --check`、官方管理器静态预检/安装、真实正常重启、版本读回、原事件/风险逐行哈希及新旧 quick_check 通过。
- 未运行全仓pytest、宿主前端重构或全市场商业重拉。原始真值、数据和个人截图全部留在本机。

本机证据根目录为 `data/private/complete_ma_20261112/`，重点文件：

- `before.sqlite3`、`before-event-digests.json`、`final-runtime-integrity.json`：一致性备份、旧事实与运行快照。
- `real-smoke/window-*.json`、`real-smoke/evidence.json`、`sz-etf-oracle.json`、`before-ma-coverage.json`：原响应与数学首末值。
- `stress/db-metrics.json`、`stress/request-metrics.json`、`performance-query-fix-final.json`：实际容量、请求模型和最终并发结果。
- `ma55-before.jpg`、`kline-before.jpg`、`kline-all-250-dark.jpg`、`ma55-after-light.jpg`、`ma55-etf-result.jpg`、`ma55-mobile-final.jpg`、`etf-mobile-final.jpg` 等：界面前后和移动端证据。
- `pytest-final-actual.txt`、三个 `*-node-final.txt`、`installation.json`、服务日志及安装目录：验证与部署记录。

快速回退通过官方管理器的 previous，再正常重启；previous 可能是本轮中间版。恢复任务前整套版本时，先正常停机并用SQLite backup API另存当前新状态，再将 `rollback/lan.ma55/state.json`、`rollback/lan.kline/state.json`、`rollback/lan.theme/state.json` 恢复到各自 `data/extensions/<id>/state.json`，对应保留的0.5.2/3.1.1/1.0.5目录。若需连数据回到任务前，使用 `before.sqlite3`，本轮新增事实保存在另存快照。当前已运行新版本，回退未执行。

剩余事项明确为：全市场历史/因子覆盖 PENDING，受阻样本保持 FACTOR_PENDING；盘中下一根真实LIVE自动到达、账户总配额及7,661证券整池持续性能 NOT_VERIFIED；合法题材快照仍待用户已有授权数据。以上限制保留真实状态，未以插值、填值或假BUY替代。
