# MA55 V0.2.2 真实运行验收 · 2026-11-07

**FULL_MARKET_COMPLETE：NO。状态：`BLOCKED_BY_OFFICIAL_EXTENSION_CONTRACT`。** 本轮完成现场诊断、真实供给状态页面及正式管理界面部署；没有真实全市场扫描、后台逐股状态或历史BUY回放验收。

分支 `feature/tsp-ma55-runtime-recovery-20261107`，从 `88e1e56f5f87c6161e6d57a4974b166822dce986` 新建。继承 QuantDash 插件化成果，宿主仍为官方 v0.3.32 / `ca3a44252edb414e3f4c1df1b7c381bad9f55c8b`。规则仍为 `MA55_15M_MINIMAL_V5`，来源提交 `23477622536d1206b2af3c164b4c905aa2e15e8d`。本轮未改规则、Python后端、行情采集或复权。

## 为什么原页面为空

1. 开始时本机3018后端、3011前端均无监听。用已有本地启动器和现有nga启动后，真实 `/health` 返回 `ok / 0.3.32`。没有创建环境、升级依赖或变更源设置。
2. 官方管理UI仅列出 QuantDash 1.2.6 与旧MA55页面；后端 `GET /api/extension-packages` 为 `[]`。旧页面 `lan_ma55` 已启用、无加载错误，实际分发45,208字节，界面标为V0.2，没有上一轮源码中的阻断提示。管理器 `version` 为空，不能断言旧产物的补丁版本。dist中的0.2.1构建并未部署。
3. 真实 `GET /api/watchlist` 的自选数量是0。旧页面依赖当前自选页，初始没有标的；显示“页面观察已开启”不能说明后台有任务。
4. `backend/extension.py::startup` 只初始化派生库；`backend/service.py::OfflineService` 没有宿主行情输入或扫描调度；`/status` 的 `backend_scanning=False` 是实现事实。安装此包也不会获得扫描结果。
5. 下面的24股仅读试验表明，样本中的分钟历史不足56根。此问题与后端未安装、契约缺口、自选为空分别成立。

## 真实安装与SQLite

V0.2.2在真实「设置 → 导入与备份」上传 `addons/lan_ma55/dist/extension.js`，标识 `lan_ma55`，确认替换已有页面，经浏览器加载校验成功后刷新。新页面头部明确显示 **V0.2.2**；其清单 `default.version` 也是0.2.2。官方分发内容、构建文件与实际 `data/ext_pages/lan_ma55/bundle.js` 字节完全一致，均51,821字节；`enabled=true / error=""`。宿主Page Pack元数据的version字段仍为空，以产物清单和实际页面版本识别本次部署。页面更新只需刷新，无需重启后台。

后端 `lan.ma55.state` 0.2.1仍**未安装、未启动**，没有active/runtime_version。没有安装一个只会产生空库的包，也没有新建诊断库。实际宿主数据根由既有启动配置及官方分发产物与磁盘页面一致性确认。

| 实际检查对象 | 结果 |
| --- | --- |
| `context.data_dir/user_data/lan_ma55/state.sqlite3`（本机工程data目录下） | 不存在，未初始化 |
| `symbol_state` / `signal_event` / `event_risk` / `scan_cycle` | 表不存在；行数均N/A |
| 最新 `scan_cycle` / `processed_at` | 不存在 |
| MA55正式股票全集分母 / 后台真实已处理数 | 分母尚不可枚举；未执行，无后台轮次 |

检查使用文件存在性；只有文件存在才以SQLite `mode=ro` 打开，未通过连接动作创建文件。最终部署后该路径仍不存在。不能表述为“四表0行”或“已扫描0股”。

## 行情供给侧现场

北京时间20:27–20:35，经真实官方接口读取；本轮启动后：

| full_minute字段 | 实际值 |
| --- | --- |
| available / enabled / running / capability_ok | 全部true |
| provider / provider_effective | `quantdash_user` |
| repair_only / interval_seconds | true / 120 |
| in_trading_hours / gate_reason / healthy | false / `outside_trading_hours` / false |
| rounds / last_round_at / last_round_ms | 0 / null / null |
| last_symbols / last_rows / last_requests | 0 / 0 / 0 |
| last_error / minute_basis_raw | null / null |

这是周末休市门控和本进程尚无成功轮次，**不代表分钟历史零覆盖**。`minute_basis_raw=null` 也不能解释成已确认原始库基准。

股票维表统计5,890，只能作为官方统计数量，截至11-06；`stats_pending=true`。enriched股票快照截至11-06，有效close且去重后5,573只，差额317。快照不承诺完整上市股票全集，名称map还混有ETF/指数。`dataStatus.minute` 虽暂报rows/symbols为0，同时有15个交易日和16个文件；24股仅读结果直接证明不能把待更新统计的0当真实覆盖。

## 有界真实本地仅读

使用普通loopback GET，无密码宿主，未伪造Cookie、创建Token或常态轮询客户端。先经官方 `instruments/search?asset_types=stock` 确认类型，再请求单股 `minute-range?days=20`。总共24只：20只来自有效股票快照，另4只是官方股票搜索命中但不在最新快照中的候选。没有遍历5,890只、读行情文件或直接导入仓库实现。

| 样本结果 | 数量 | 本地分钟 / 有效完成15m |
| --- | --- | --- |
| 有正常单日分钟，历史不足 | 20 | 每股11-06共241行（含09:30），聚合16根有效15m，均不足56根 |
| 请求成功但无本地会话 | 4 | 每股0会话，`source=none`，历史不足 |
| 满足56根预热 | 0 | 没有进入正式BUY计算或写库 |

24次分钟读取合计977.11ms，中位41.78ms，范围5.63–68.65ms。20只有数据样本均值47.03ms，完成K内没有分钟缺口；4只空历史不能认作停牌。快照自身没有零成交量候选；接口没有给出这些股票的已确认停牌/除权事件标签，**本轮没有完成真实停牌和跨复权事件窗口验收**。不按代码、ST名称、价格差猜测，不为补齐验收去调用可能补拉的日K端点或审计全市场因子。

前20股探测客户端CPU为user141ms/system78ms，RSS由49.64MiB到74.31MiB。追加4股（含搜索/快照读取）期间宿主CPU累计增量156.25ms，RSS由1,606.20MiB到1,610.59MiB；宿主同时运行既有后台，不能据此分离全部成本。上述是本地样本测量，不是全市场吞吐量或资源上限。

**MA55新增供应商请求为0。** 新页面4个元数据调用及探测的股票搜索/分钟范围GET均只读已有本地数据；探测前后full_minute `rounds/last_requests` 都为0，未调用Provider或分钟批量POST。`last_requests`是宿主最近轮计数，并非全进程供应商累计计数；零新增结论来自实际调用路径，不能单靠该字段外推。

每股请求时间、数据质量计数、资源记录和截图仅留本地忽略目录 `dist/ma55-runtime-recovery-20261107/`。没有上传OHLCV、SQLite、日志、构建产物或商业行情。

## 公开契约为什么仍不足

- `backend/app/extensions/contracts.py::RepositoryAccess` 仅公开 `get_name_map`；`repository.py::get_name_map` 明确合并股票/ETF/指数名称，无stock-only完整全集合同。
- `backend/app/api/screener.py::market_snapshot` 取 `ScreenerService` 默认stock的最新enriched快照，不承诺维表全覆盖；本机实际少317只。
- `backend/app/api/kline.py::get_minute_range` 是单股最近1–20个已落库会话的本地读取，复权由官方仓库读路径执行。后端扩展上下文没有等价的批量历史/增量完成点协议。
- 同文件 `get_minute_batch` 的缺失分支会触发供应商补拉，`prefer_local`不是严格仅读保证，未拿它扫描全市场。
- `backend/app/services/api_gateway.py` 放行read:market的minute-range GET，不放行minute-batch POST；每Token默认120次/分钟。本次环境与.env没有该限额覆盖。5,890次单股GET按该预算约49.1分钟，无法承诺15分钟一轮；没有更改限额或创建Token，也未构建这个后台适配器。
- `docs/page-sdk.md` 要求数据经ctx.api；`frontend/src/lib/api.ts` 没有调用自身扩展路由的通用GET方法。`extensionPackages()`只能读安装元数据，不等于读取SQLite结果。独立路由的注册能力不能证明存在合规Page Pack结果桥，未做fetch/iframe绕行。

因此在契约探索后停止新增后台业务代码；现有离线服务、四表和规则均保持原样。需要官方提供的窄合同仍只有：[正式股票全集、严格本地且官方复权的批量历史/增量完成点、页面自身后端只读桥](UPSTREAM_CONTRACT_REQUEST.md)。

## 页面与定向验证

- 源码路径：`ctx.api.minuteRefreshStatus / marketSnapshot / dataStatus / extensionPackages` → `src/supply_status.mjs` → 页面模型 → `src/presentation.mjs`。错误逐项显示，保留上次结果及读取时间；没有读到的值显示未知。供给状态独立于自选/日历加载，页面可见时每60秒只重读元数据，离开后清理计时器。
- 真实页面显示“TSP行情同步进度／非MA55扫描进度”、5,573/5,890、repair-only/120秒/休市门控，以及“MA55全市场后台未接通／后端未安装／真实已处理未执行”。源码与产物版本都升到0.2.2。
- 用官方搜索临时观察600519.SH，数据状态显示11-06 15:00、历史不足56根，没有BUY；原生个股详情成功打开。未写自选列表，自选数量仍0。1200×964与720×960实机浏览器已检查，状态卡可读，表格窄屏横向滚动；已恢复默认视口。
- `pnpm build` 通过；`node --test tests/supply.test.mjs tests/page.test.mjs tests/logic.test.mjs` **37/37通过**，包括原24个规则/聚合/状态用例、9个页面用例、4个新供给状态用例。冻结规则未改；本轮未重复私有Python对照或后端合成性能测试。
- 对官方tag检查 `backend/app frontend/src dev.ps1` 的差异为空；`git diff --check` 通过。原有未跟踪 `REVIEW_SUMMARY.md` 保留且不提交。

达到真实全市场的下一步条件：官方合同可给出一致分母N与严格仅读批量输入；本机每个可计算标的有56根连续有效完成15m及官方复权/交易日证据；有界真实轮次在15分钟内提交逐股processed_at和准确分类计数；页面读取该SQLite结果，关闭页面与重启后后台持续且事件不重复。盘中增量仍须下个交易时段单独验收。当前这些条件未满足，不能宣称历史回放、无人值守或全市场功能已完成。
