# 兰 MA55 原生15m候选｜lan.ma55 0.9.8

生产链路为 TickFlow `klines.batch(period="15m", adjust="none")` → 同一 SQLite 的原生来源分区 → `NATIVE15_REVIEW_V1` 人工复核候选 → 官方 Ext Data → MA55 页面 / K线工作台。宿主保持官方 v0.3.36，兼容范围 `>=0.3.36,<0.3.37`。

原生柱保存真实 `TICKFLOW_NATIVE_15M / source_count=1 / NATIVE_15M_COMPLETED`。旧 `bar15` 的三根5m约束、`signal_event`、`event_risk` 和旧结构状态保留。生产 worker 不调用5m；`fetch_five()` 仅供明确执行的离线归档对照工具使用。

## 候选与冻结历史

`native15_signal_adapter.py` 直接调用原样保留的 `engine.minimal_features / engine.advance`，引用研究提交 `5d1d0f6e8913f528110c93e054429f050b9851f6`。状态保留55根特征历史、每槽5份量和完整结构锁，按证券增量持久化。冻结 `pipeline.py` 的 `source_count=3` 合同保持原样。

新候选单独存入 `native15_candidate` 与 `native15_candidate_risk`；来源、输入合同、规则引用、因子/基准修订、支撑/确认/首次看到时间均可追溯。首次预热及历史回补为 REPLAY；之后的新完成桶按真实首次看到时间区分 LIVE / DELAYED。候选供人工看图，不宣称与旧冻结 BUY 等价。

真实缺口、无效OHLC、未知因子或tick会阻止对应证券的候选。输入修订后已有结构锁须从真实原生历史复现；证据不足时保留锁，显示 `INPUT_REVISION_LOCK_HISTORY_REQUIRED`。现有确认不改写。

首页默认原生候选，旧冻结 BUY 使用独立投影入口。Theme 的旧 BUY 数据集保持原含义。

## 所有权、调度与缓存

`runtime.py` 维护唯一后台 SDK owner 和 writer lease，`native15_owner.py` 使用原数据库同一连接原子提交行情、候选与收据。关闭页面后继续运行。新表位于既有 `state.sqlite3` 内；原生行情保留，计算和图表读取有界。

- A：已有输入的最新完成目标追赶，最高优先级。
- B：全池取得约64根原生15m，满足至少56根连续合法策略输入；零成交量、真实缺口及复权基准重置仍按冻结规则重新预热。
- 因子独立队列（内部阶段F）：与B按最早持久尝试时间公平调度，高于C；行情游标与因子冷却分开。只用已保存真实15m补算，补因子产生的候选标为REPLAY。
- C：低优先级补足约1,280根图表历史，并修复真实历史缺口；相同窗口最多100股一批。

15m与因子共享原有8 RPM采集池；C还受2 RPM冷池约束。未提高现有配置或账号配额。总池和C池均在真实HTTP入口扣取；重启从最近收据恢复60秒窗口，并恢复持久429冷却。行情同目标失败300秒后可重试，连续失败延长至1800秒；新15m目标独立推进。因子失败按300/900/1800/3600/7200/21600秒渐进退避，状态跨重启保留。429遵守Retry-After，401/403停止供应商调用，等待配置/重启。

`factors_day`仅表示明确响应的成功核实日期；真实`[]`代表已核实无事件，缺键/None保留未知。跨交易日的新候选等待当日因子核实，已有链不冒充新响应。升级清除0.9.3错误写入的成功日和因子导致的24小时行情冷却，包括目标落后的SOURCE_LAG分支；不删除行情、候选或旧BUY。账户跨端点共享池仍待核实。

交易日从已核实历史日历及完成原生15m核对，首根完成前显示等待；不请求5m探测交易日。午休、完成延迟余量、交易日、OHLCV、去重冲突、手→股/份和金额元均在输入边界验证。

`market_store.py` 与 `indicator_cache.py` 复用原读写/投影逻辑，按来源使用独立表及缓存命名空间。30/60m只聚合连续的2/4根原生15m；六SMA按来源、基准、因子、日历与元数据修订隔离。可见上限250/125/62根，策略普通增量只读取最近250根。MA233不足时显示空值及原因，最新图表/候选先发布。

## 公开只读接口

前缀 `/api/extensions/lan.ma55/v1`，沿用宿主面板会话认证。

| 接口 | 内容 |
| --- | --- |
| `/status` | 原生目标、保存/实际分析覆盖、A/B/C、重试、因子、历史和请求收据 |
| `/universe` | 真实股票/ETF范围，最多100行/页 |
| `/bars` | 默认原生15/30/60m，最多32证券/8000根；`source=archive` 明确读取旧行情 |
| `/candidates` | 独立原生候选，symbol/start/end筛选，最多200行/页 |
| `/events` | 原冻结 BUY 与风险，原有分页/筛选合同 |
| `/inventory` | 所选原生证券实际库存 |

原有 symbols/cycles/events/status 路由保留。原生投影为 `lan_ma55_native15_{summary,day,history,prices}_v1`；旧 `lan_ma55_summary/day/history/prices_v1` 不混入候选。页面标题为“MA55 选股”，五项主指标、互斥阻塞分类及最近A/B成功直接显示；行情/因子/候选/历史诊断默认折叠。1280根库存就绪不等于各周期MA233全部可见。页面每10秒只读发布结果，“刷新展示”不采集行情。

## 构建、验证与回退

在现有 nga 环境执行后端命令；前端使用现有 pnpm 依赖，不升级宿主或SDK。

```text
pnpm --dir addons/lan_ma55 test
pnpm --dir addons/lan_ma55 build
python addons/lan_ma55/build_backend.py
python -B -m pytest -q -p no:cacheprovider --basetemp data/private/codex_runs/<唯一轮次>/scratch/pytest addons/lan_ma55/tests_backend addons/lan_ma55/tests_market
python -m ruff check addons/lan_ma55
python -B addons/lan_ma55/tools/run_stress.py --native --output data/private/codex_runs/<唯一轮次>/scratch/stress --symbols 5600
python -B addons/lan_ma55/tools/native15_production_benchmark.py --output data/private/codex_runs/<唯一轮次>/scratch/benchmark
```

稳态压力夹具明确从当日因子已核实开始；跨日未核实阻断、同日恢复和预算由专项回归验证。每轮按根AGENTS.md的创建清单清理scratch，保留少量最终证据与一致性备份。

已删除无人运行的旧5m调度与历史排队模块；原始归档、迁移、冻结规则、来源规范化及显式离线5m对照仍有实际依赖。原生schema明确声明，不再通过替换旧DDL生成。

通过官方扩展管理器预检、浏览器候选加载、安装，再正常重启。迁移仅新增来源分区；上线前使用 SQLite backup API 一致性备份及旧确认/风险逐行比对。回退前另存新行情和候选，再由官方管理器回退上一版本；旧版忽略原生分区，保留当前数据库。旧版恢复其旧生产口径，不能作为新版本的自动fallback。

实际安装、覆盖、性能、数据保全与剩余范围见[本轮验收](../../docs/diagnostics/TSP_MA55_Native15_Production_20261113.md)。此前shadow和缓存验收见[0.8.2历史报告](../../docs/diagnostics/TSP_MA55_Native15_Incremental_UI_20261113.md)。商业行情、数据库、日志、截图、ZIP/dist、凭据和私人参考包只保存在本机。
