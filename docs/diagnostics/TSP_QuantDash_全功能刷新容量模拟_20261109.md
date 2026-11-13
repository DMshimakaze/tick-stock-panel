# TSP + QuantDash 全功能刷新容量模拟（2026-11-09）

**结论：全功能实时目标为 NO_GO；保留原 5m MA55、降低全市场 1m 刷新频率的组合可继续验证。** 本轮交付可运行的离线模拟器、定向测试和本报告。生产配置、宿主、已安装插件、行情库及真实 BUY 均没有经本轮工具写入。

四个问题的直接回答：

1. **现有默认预算下，5m MA55 与“全市场 1m 每 120 秒”不能同时按目标节奏维持。** 宿主总预算 12、实时预留 6，后台只有 6 HTTP/滚动分钟；加入关注股和盘口后，模拟 59 批修复轮约 12 分钟，1m 数据的交易时间落后 p95 约 25 分钟。09:31 启动首轮，09:33 就错过第二个目标起点。实际宿主不堆叠无限轮次，而是降低实际轮次频率。
2. **原生 15m 将 MA55 最新扫描从 48 轮/日降为 16 轮/日，扫描请求减少约 2/3。** 每轮仍是 59 批。保持宿主默认预算的 S2 几乎没有改善全市场 1m 时效。SDK 的 15m/100 股/compact 形状已用真实 SDK + MockTransport 验证；真实原生 15m 与 5m 聚合、账户权限及 BUY 等价性均为 **NOT_VERIFIED**。
3. **在“分钟批量独立于其它端点、1m/5m/15m 共用 30/min”假设下，24/min 协调模型的候选为：快照配置 30 秒、5m 全池每完成桶后 60 秒取数、全市场 1m 每 7 分钟一轮、关注 10 股及单股盘口每 120 秒。** 名义处理假设下，MA55 p95 约 4.5 分钟；全市场 1m p95 约 11 分钟。128 组合中，全 SLO 通过为 0。该协调器尚未实施，只是下一轮插件候选；增加间隔到10分钟也不自动改善MA55，模型中的排队相位会使其错过5分钟目标。
4. **只用 QuantDash 仍有数据源和公开契约缺口。** 当日同花顺概念/行业成分、真实 KPL 题材、财务能力、部分证券因子覆盖及官方 ETF/指数主数据自动更新不能保证。官方股票主数据可用公开 Provider 方法补齐；现有 QuantDash Provider 尚未实现该方法。分钟路由失败不跨源，与主数据目前仍调用 TickFlow 是两件不同的事实。

## 1. 本轮取证及工作树

首次运行态取证 **18:30:37 北京时间**；补充版本和数据日期查询截至 **19:05:54**。[前轮17:04–17:12取证](TSP_概念行业板块_20261109_时效性排查.md)是历史证据，未冒充本轮实时采样。

| 项目 | 本轮证据 |
| --- | --- |
| fetch 后开发基线 | `personal/feature/tsp-kpl-theme-filter-v1-20261109` = `a65d60d9ee5eeb60592dc728d6a3236960c192c1`；`git ls-remote` 同值 |
| 本轮分支 | `feature/tsp-quantdash-capacity-sim-20261109`；目标分支原先不存在，从上述已核实基线安全建立 |
| 初始工作树 | 仅 `?? REVIEW_SUMMARY.md`，原样保留；没有 stash/reset/clean/强制切换 |
| 官方宿主 | `/health` = `ok / 0.3.32`，`VERSION=v0.3.32`；tag 对应 commit `ca3a44252edb414e3f4c1df1b7c381bad9f55c8b` |
| 核心差异 | `git diff --name-status v0.3.32 -- backend/app frontend/src dev.ps1` 初查为空；交付前复核见验收段 |
| 运行进程 | 后端 PID 47236、3018，cwd 为工程 backend；启动时间 11:50:43，未重启 |
| 进程启动 HEAD | 服务没有暴露启动 SHA。reflog 显示启动时工作树为 `eca53194772202000fe551108a360ac6d233c602`；不能以本轮 HEAD 冒充加载的扩展源码 |
| 环境 | 唯一既有 nga 解释器；SDK 0.1.0、Polars 1.44.1、DuckDB 1.5.5、FastAPI 0.142.2、pytest 9.0.3、Ruff 0.15.14 |
| 实际插件版本 | Provider 1.2.6，enabled/available；公共行情 1.0.2；MA55 0.3.2；工作台 2.0.1；题材 1.0.2 |
| 当前开关/路由 | realtime=true、30 秒；full_minute=false、间隔 120 秒、repair_only=true、rounds=0；六类行情能力均路由 QuantDash；minute_sync 未配置，getter=false |
| 其它真实设置 | 盘后 A 股 getter=true、指数=true、ETF=false；分钟盘后天数 getter=5。financial 当前路由 Fuyao |
| 当前日期 | 19:05 GET `/api/data/status`：daily/enriched=11-09，minute=11-06；last_pipeline_run 仍为 11-06。接口的 5891 是维表分母，不是本轮核实的当日成交覆盖 |
| 盘后故障 | 18:30 GET jobs：active_id=null；任务 `7a738dcf84` failed / sync_adj，15:35–15:54:07；前报告日志已定位 `missing symbol response / UNVERIFIED` |
| 公共采集 | 18:30 phase=WARM、writer_alive=true、全池；expected=5891、candidates=8275、quote_pool=5579、unclassified=22、unpriced=47、listing_status_unconfirmed=312 |
| 请求证据 | 公共 worker 累计 HTTP=3659、5m=3174、429=0。该计数只证明该 worker 已记录的请求，没有核实宿主全部历史日志均无 429 |
| 账户/协调 | 本地只在内存比较来源 Key，结果相同，未输出值。Provider 和公共 collector 在同一后端进程使用不同客户端/限流对象，没有统一账户仲裁；多进程也无仲裁 |

生产仍在做历史预热，**真实商业请求校验跳过，新增商业 HTTP=0**。同日 5m/原生 15m 的获授权成对样本未取得；没有差异数量为零的实测结论。后台自身继续运行和写入原有缓存、日志，本轮没有停止它。

## 2. 源码证据和责任边界

下表路径均相对于工程，读取版本为开发基线；官方核心与 v0.3.32 相同。没有把官方 main 当部署版本，也没有读取或修改独立 MA55 研究工程。

| 路径 / 函数 | 核实结论 |
| --- | --- |
| `examples/quantdash-data-source/quantdash_tsp_plugin/client.py`：`RollingLimiter.acquire`、`QuantDashClient._before_http_request` | 真实 HTTP request hook；总 12、后台 6；max_retries=0；进程内对象，非账户协调器 |
| 同目录 `provider.py`：`_klines`、`get_minute`、`get_realtime*`、`audit_adj_factors`、`get_depth_batch` | K 线 100 股、count=5000、adjust=none、compact；minute 只接受 1m、12 自然日分窗；股票/ETF 各一快照，指数另一次；因子遗漏有别于空事件；五档逐股、额度不足非阻塞跳过 |
| 同目录 `mapping.py`：`kline_rows`、`quote_rows`、`factor_rows` | TSP 量为手、金额为元，北京墙钟；没有猜单位或把缺因子当 1 |
| `addons/lan_marketdata/backend/collector.py`：`before_request`、`fetch`、`factors` | 固定 raw 5m；分钟/日/因子/元信息/快照各自 RollingBudget，通常各 24；429 记录、Retry-After 与 checkpoint；其分类代码目前只识别 period=5m 为分钟 |
| `addons/lan_marketdata/backend/runtime.py`：`Worker.__init__`、`task`、`run` | LIVE 全池先于 COLD16/WARM64/GAP；minute_limit=min(设置,24,30-host_background)，只是静态数字切分；看过股票仅优先历史阶段；同批按证券逐个事务 |
| `addons/lan_marketdata/backend/grid.py`：`latest_point`、`aggregate`；`store.py`：`accept` | 60 秒完成余量；午休不跨桶；3/6/12 根真实 5m 形成 15/30/60m；单股5m144、六种衍生周期各256，指标状态另存；历史更正可能要求有界重建 |
| `addons/lan_ma55/backend/runtime.py`、`pipeline.py::process_symbol`、`engine.py::advance` | HTTP v1 消费公共行情；冻结 `MA55_15M_MINIMAL_V5_R1`；真实 `source_count==3` 及因子/格点完整性是输入要求，30/60m 不计算 BUY |
| `addons/kline_workbench/README.md`、`addons/lan_theme/README.md` | 工作台消费已保存15/30/60m；题材消费合法分类快照与原 MA55 发布结果；二者不生产行情。题材运行态 AUTHORIZED_SNAPSHOT_REQUIRED |
| `backend/app/services/minute_refresh.py`：`_effective_interval`、`_loop`、`_run_round` | repair_only 最低 **60秒**；连续竞价/交易日门控；下一轮=max(起点+间隔,完成)，无补跑；整轮拿到 DataFrame 后才写分钟分区 |
| `backend/app/services/kline_sync.py`：`fetch_intraday_custom_batch`、`_try_custom_minute` | 本 Provider 无 get_intraday_latest / get_intraday_batch，修复轮调用 get_minute(全池、当天固定起止)；显式分钟源失败不静默切 TickFlow |
| 同文件 `sync_minute_batch`、`sync_and_persist_minute` | **盘后流式路径按50股分块**，5891股为118次HTTP/窗口；盘中整轮100股为59次，两条路径不能混算。默认增量从已存last_dt起，不会无条件重拉5个交易日 |
| `backend/app/jobs/daily_pipeline.py::run_now`、`start_scheduler` | 15:35 开始日K→因子→enriched→指数/ETF→可选分钟；股票因子异常中止后续阶段，finally 刷缓存不能变成整链成功 |
| `backend/app/services/sector_rotation.py::_compute` | 读 `kline_minute` 全市场分区及真实成分映射；API/前端30秒重请求只读缓存，不产生新的1m行情 |
| `backend/app/services/ext_presets.py`、`ext_pull.py::_apply_auth`；`backend/app/api/settings.py` | 概念/行业为 Fuyao snapshot，目录→成员 fanout，每1440分钟；无 time_field/date_param 的快照写入时间不等于交易日期；capability 路由彼此独立 |
| `backend/app/services/instrument_sync.py::_fetch_instruments_via_provider`；`index_sync.py::_fetch_instruments_by_type` | 股票可选 get_instruments 公开入口存在，但 QuantDash Provider 未实现；随后走 TickFlow。ETF/指数列表直接用 TickFlow exchanges，当前没有相同 Provider 替换入口 |

一个关键配置陷阱：**只把线上 `TSP_QUANTDASH_RPM` 改为30，会让现有公共 worker 的分钟上限缩为6，而非获得“宿主30+公共24”。** S3 的宿主30、公共可借用24，是明确的插件协调方案模拟，需要下一轮同时调整上述两个独立实现。

## 3. 功能→来源→消费者→粒度→目标→缺口

目标时效是本轮 SLO。表中“可供数”指来源和调用链存在，实盘时效验收仍待开盘。

| 功能 | 实际来源 / 消费者 | HTTP 与返回粒度 | 目标时效 | 缺口 / 是否可保证 |
| --- | --- | --- | --- | --- |
| 股票全池快照 | QD quotes(CN_Stock)→Provider→QuoteService | 每轮1 HTTP，全池响应 | p95≤60秒 | 可供数；5891主数据与5579快照池非同一覆盖口径 |
| ETF实时/核心指数 | QD CN_ETF + symbols→QuoteService | 各1 HTTP；指数还合并基准、规则、可见详情 | p95≤60秒 | ETF/指数具体证券权限和报价完整性未新增实测 |
| 详情/自选补报价 | get_realtime_symbols，按stock/etf分别去重 | 每个缺报价资产组≤100股/HTTP；不足即跳过 | 按需≤120秒 | 同一 host 后台6预算，不能假设无成本 |
| 原生日K/原始价 | QD daily→kline_sync→官方库 | 股票59批，raw；ETF/指数另计 | 17:30前全链完成 | 日K日期11-09已经存在，不能据此认定全链成功 |
| 事件因子/官方复权 | QD ex_factors→sync_adj_factor→enriched/分钟读取投影 | 股票59批、完整事件链；遗漏≠无事件 | 盘后完整且可核实 | **当前真实 FAIL / UNVERIFIED**；不能吞错继续报成功 |
| 全市场MA55 | 公共raw5m→真实三根聚合15m→冻结pipeline/engine | 59批/5分钟；16个完成15m信号窗口 | 15m完成后p95≤5分钟，并测3分钟 | 热缓存容量可预测；真实全池时延与冻结BUY等价未新增验收 |
| 原生15m候选输入 | QD klines.batch(period=15m)→未来适配 | 59批/15分钟，不能冒充三根5m | 同上 | **NOT_VERIFIED**；当前MA55输入契约拒绝虚假source_count |
| 工作台15/30/60m | 公共HTTP v1→lan.kline只读视图 | 无新增商业请求；30/60m要2/4根完整native15候选或6/12根真实5m | 与对应完成桶一致 | 当前工作链保留；native比较仅离线，未部署 |
| 筛选/题材点击跳转 | lan.ma55已发布结果→lan.theme/lan.kline | 公共扩展表/深链，保留event/confirmed/setup/defense | 随已有结果 | 不重算BUY；KPL真实分类当前SOURCE_MISSING |
| 概念/行业快照排名 | 日K/enriched/实时报价 + 既有Ext分类 | 行情复用；分类独立snapshot | 行情≤60秒，分类需真实asof | 旧映射可注明来源使用，当日自动分类 **UNSUPPORTED** |
| 官方盘中板块轮动 | 全市场1m分区 + 分类映射 | 每修复轮59批，返回当日累计1m | p95≤5分钟；另问30/60秒 | 默认预算约25分钟p95；安全协调仍失败。15m曲线不是该原生功能 |
| 历史RPS | 官方历史日K/enriched + 分类 | 复用历史日线，非实时1m生产者 | 盘后 | 分类历史/当前映射需区分；依赖因子/日线链可靠性 |
| 分钟信号/分钟回测 | 官方minute契约/订阅池、真实历史1m | 按股/批量1m，12自然日分窗 | 关注≤120秒；全池≤5分钟目标 | 仅少量按需可供数；历史缺1m不能由15m补造 |
| 单股分时/近端与历史 | Provider get_minute(freq=1m) | 小池≤100股/批，count≤5000 | 按需≤120秒 | 与 full_minute 共享同一 host 后台预算；更深历史需更多窗口 |
| 五档盘口 | QD depth.get→get_depth_batch | **每股1 HTTP**；1/5/10股每120秒=0.5/2.5/5 RPM | 按需≤120秒 | 大监控池当前关闭；默认6后台预算很快被耗尽 |
| ETF/指数日K/分钟覆盖 | QD K线能力 + 宿主已保存证券主数据 | ETF估算1500证券=15批；核心指数1批 | 盘后/按需 | 数据权限未全验；官方自动更新ETF/指数名单仍另依赖TickFlow |
| 股票主数据/新股/复牌 | 公共collector的QD instruments；宿主另有stock维表同步 | 观察候选8275约83批，代码取ceil=83；现场累计84非单轮保证 | 盘前/新股入池 | 两张主数据用途不同；宿主Provider缺get_instruments，stock可用公开契约最小补齐 |
| 分类源/财务 | Fuyao Ext快照、Fuyao financial；KPL需授权快照 | 分类每表1目录+N成员；QD插件不声明financial | 真实有效日期 | **只有QD时不保证自动分类、财务及相关因子**；不使用DEMO填补 |
| 冷启动/回补/历史更正 | 同一公共collector LIVE/COLD/WARM/GAP | 16/64交易日，5000根/股上限，缺口有界重取 | 给盘中/15:35让路 | 源缺、停牌、新股、历史更正均保留明确状态；不增第三个producer |

同源不等于可删除重复库：现MA55私库保存状态、确认事件和分析收据；公共DuckDB保存完成K/MA；TSP官方1m用于原生轮动、分钟信号和回测。工作台旧SQLite正常不写，旧MA55 collector不进入生产包。5891股每天5m原始输入282,768根、1m为1,413,840根，后者为5倍；两个周期单轮都59批。只在启用全1m后才出现这两种周期的并行采集，当前全1m关闭。把1m复用于MA55需要真实完整性、价格/单位和公开读取契约证明，不能据“同一个供应商”直接删5m采集或信号库；本轮保留全部回退数据。

## 4. 限额证据、假设和数学下界

[QuantDash公开页](https://quantdash.net/)展示1m/5m/15m/30m/60m与股票池快照。公开[定价页](https://quantdash.net/pricing/)未向本次读取返回个人额度明细，文档索引也未成功取得。因此下表供应商数字来自任务指定的 **Pro场景假设**，不是个人账户实测。

| 类别 | 假设上限 / 批次 | 实证边界 |
| --- | --- | --- |
| 快照 | symbols 120/min、universe 60/min | SDK0.1.0：≤20symbols/≤5universes为GET，更多为POST，单次方法各1 HTTP；stocks+ETF+indices共3，不是5891次 |
| 日K | 60/min，100股/批 | 本地SDK与现有Provider形状已核实，账户额度NOT_VERIFIED |
| 分钟批量 | 30/min，100股/批，每股≤5000根，近一年 | 默认1m/5m/15m共池；历史深度/账户权限NOT_VERIFIED |
| 分钟单股 | 60/min | 当前Provider即便1股也走batch端点；不能借用单股额度抵扣59批 |
| 五档 | 60/min | 生产实现逐股；没有假定batch深度权限 |
| 因子 | 60/min，100股/批 | 明确代码响应才能确认无事件；账户缺响应故障已存在 |
| 元信息 | 模型假设60/min；collector本地24/min | 供应商元信息额度未验证，不作为扩量依据 |
| 宿主 | 总12、后台6、实时优先；滚动60秒 | 当前代码/环境和运行态reserve一致 |
| 公共collector | minute24，其它类别各24；滚动60秒 | 独立对象；没有账户级共享状态 |

稳定的两个当前实例中，**host后台≤6 + collector分钟≤24 ⇒ 分钟请求滚动峰值≤30**；实际模拟也到30。这是根据真实两个rolling实现及本场景实例数得到的上界，并非把12+24=36算成“分钟能力”。它不保证80%余量，也不保证供应商将quotes/factors/metadata另计。如果供应商所有HTTP共用账户30池，独立对象在仿真中超额；现场429证据仍只有公共worker记录为0。

下界不含响应耗时、缺口和重试：

| 股票数 | 100股批次 | 30 RPM下界/分 | 12 RPM下界/分 | 6 RPM下界/分 | 5m平均RPM | 15m平均RPM |
| --- | --- | --- | --- | --- | --- | --- |
| 5500 | 55 | 1.8333 | 4.5833 | 9.1667 | 11.0000 | 3.6667 |
| 5891 | 59 | 1.9667 | 4.9167 | 9.8333 | 11.8000 | 3.9333 |
| 6100 | 61 | 2.0333 | 5.0833 | 10.1667 | 12.2000 | 4.0667 |

59批在30/12/6 RPM下为1.97/4.92/9.83分钟。每5分钟扫一次11.8 RPM；每15分钟3.93 RPM。两者都不是20批。当前120秒目标仅full1m就需29.5 RPM，加5m与关注1m约41.8 RPM，超过假设分钟30；在独立固定节奏排队模型中，宿主缺口至少29.5−6=23.5请求/分钟。实际宿主以错过节奏代替无限排队。

## 5. 模拟方法和参数

实现分为 `capacity_sim/model.py`（虚拟时钟/请求/配额/队列）、`scenarios.py`（负载和穷举）、`metrics.py`（按证券与时间加权统计）、`contracts.py`（数据故障与原生15m比较）、`cli.py`（参数/JSON）。默认没有网络、Key、生产导入或写库操作。可选输出仅允许工程内被忽略的 `data/diagnostics/`，避免相对路径退到工程外。

模拟09:00–17:30，交易日为本地已确认11-09；显式周末/holiday不发盘中或盘后新增请求。快照模型09:15开始；09:30–11:30/13:00–15:00竞价；完成5m/15m在结束后60秒释放，午间/收盘可以完成最后一桶，午休不创造新桶。盘后15:35按顺序处理；17:30截止后不派发/提交模拟任务。**生产公共worker实际仍在18:30预热，模拟截止不是已存在的夜间静默配置。**

host full1m只有一轮在途，查询末端固定为轮起点可获得的最新完成1m（假设供应商晚60秒），整轮完成后统一可见；因此不是每个HTTP完成就立刻给宿主发布。公共collector按批串行、LIVE优先；新目标合并旧待取窗口，in-flight不能抢占。关注证券放入首批并保留独立按需队列；统计所有证券，不以首批代表全池。

统计定义：

- 所有rolling窗口是 **(t−60,t]**。峰值在每个实际派发事件计算，覆盖瞬时重合；没有整点窗口或累积令牌。失败HTTP也扣额度。
- MA55延迟按每根已完成15m、每只证券首次具备输入的时间加权，加假设消费者轮询/处理延迟；缺失是无穷大，不按零延迟。30/60m同样只统计完整2/4根native15候选或现有真实5m来源。
- 快照按交易时间每15秒取样，股票/ETF/指数按覆盖人数加权并单列；已保存快照的时间戳不随文件时间推进。
- 1m主表按交易分钟序号算落后，午休不增加期望bar；JSON另有 `full_1m_wall_clock` 保留真实墙钟年龄。盘初无当日覆盖计无穷大；表中另外列已覆盖最大值与缺覆盖比例，SLO不只用covered_p95。
- 请求“超时”单列断网30秒transport timeout；deadline_missed是排队/完成超过业务期限，不等于HTTP超时。pending=17:30仍待处理；missed_slots是宿主未兑现的目标起点，不会被pending=0掩盖。
- 名义S1/S2/S3/S6假设已保存主数据可用、证券响应/因子完整，用于测吞吐；S0/S5注入真实已知的因子缺覆盖。盘后默认施加1200根1m/股的五交易日回补**压力负载**，按官方50股流式路径共118次；这不是实际getter=5天的必然返回量。已有last_dt通常只需增量尾部，可用 `--post-minute-bars` 重放较小负载。`PASS_CAPACITY_ONLY`仅代表假设负载在截止前处理，业务整链状态仍需来源和阶段成功证据。

| 参数 | 默认值 | 定义 / 单位 / 来源 |
| --- | --- | --- |
| universe | 5891 | stocks; scenario 5500/5891/6100, 5891 observed master denominator |
| etfs | 1500 | securities; assumed 1500 for full ETF snapshot/daily load, not measured coverage |
| metadata_symbols | 8275 | securities; observed candidate master 8275, batches of 100 |
| day | "2026-11-09" | Beijing replay date; locally confirmed 2026-11-09 session |
| holiday | false | boolean; explicit closed-session fault, no holiday-library assumption |
| seed | 11 | integer; deterministic lognormal latency RNG seed |
| stop_s | 30600 | seconds after 09:00 Beijing; 30600=17:30, no later sends or commits |
| host_rpm | 12 | actual HTTP / rolling 60s; observed default 12, proposed 30 only offline |
| reserve | 6 | HTTP / rolling 60s; existing quote reserve 6, background cap host_rpm-reserve |
| market_rpm | 24 | 5m batch HTTP / rolling 60s; existing collector ceiling 24 |
| policy | "independent" | independent=current objects; static=explicit minute split; broker=shared rolling admission |
| minute_budget | 24 | minute-batch HTTP / rolling 60s; conservative scenario 24=80% of assumed 30 |
| static_host_minute | 12 | minute HTTP / rolling 60s; proposed host share, remainder to marketdata |
| vendor_scope | "minute_shared" | minute_shared=1m/5m/15m same 30 pool; account=assumed total 30 HTTP pool; broker caps 24 with 6 quote reserve |
| independent_periods | false | boolean; unverified hypothetical separate 30 pools per period |
| full_interval_minutes | 2 | minutes; host max(start+interval,finish), no catch-up/overlapping rounds |
| post_minute_bars | 1200 | 1m bars/security; explicit catch-up stress 1200=five sessions, not the official days=5 incremental getter; host stream chunks 50 symbols |
| quote_seconds | 30 | seconds; existing host sleeps after whole 3-HTTP snapshot cycle |
| ma_period | 5 | minutes; existing raw 5m or experimental raw native 15m, same stock coverage |
| focus | 10 | securities; assumed 10/50/100, ceil(focus/100) HTTP per refresh |
| depth | 1 | securities; assumed 1/5/10, one single-depth HTTP each |
| focus_seconds | 120 | seconds; assumed on-demand refresh period 120 |
| depth_seconds | 120 | seconds; assumed depth refresh period 120 |
| latency_p50_s | 0.5 | seconds; assumed per-HTTP network p50 before payload/processing |
| latency_p95_s | 2.0 | seconds; assumed per-HTTP network p95 before payload/processing |
| bandwidth_MB_s | 20 | decimal MB/s; assumed 20 effective payload bandwidth |
| parse_rows_s | 200000 | rows/s; assumed 200000 SDK/Polars decode throughput |
| write_rows_s | 100000 | bar entries/s; assumed 100000 effective rolling-compute/SQL throughput; retained-window upserts count |
| transaction_ms | 1 | ms/security; assumed 1 per symbol transaction overhead |
| history_rows_s | 5000 | rows/s; assumed history aggregation/indicator processing, S6 derived from 60/120min CPU work |
| consumer_s | 10 | seconds; assumed 10 extra MA55 poll/publish delay, not measured full-market CPU latency |
| enriched_s | 120 | seconds; assumed 120 serial post-market enriched compute |
| wire_row_bytes | 120 | bytes/bar; assumed 120 compact JSON average; quotes assume 500 bytes/security |
| disk_row_bytes | 400 | bytes/bar entry; assumed 400 including JSON/MA/provenance, excludes WAL/index/compaction |
| live_rewrite_bars | 465 | bar entries/security/live batch; assumed 465=144+48+24+12+6+3+128 from bounded raw/derived/2d upserts, same cost for hypothetical native15 |
| warmup_minutes | 0 | minutes; imposed 64-session CPU workload 60/120, not a measured duration |
| fault | "" | injection: 429 / outage / factor_unverified / late_bar; empty=nominal synthetic source |
| outage_minutes | 10 | minutes; injected network interruption starting 10:00, candidate 5/10 |

网络延迟用确定性lognormal（p50/p95为下表假设，尾部截60秒），另加体积/带宽、解析、窗口处理、写盘、单股事务；断网路径显式30秒超时。快照500 bytes/证券、原始K线响应120 bytes/bar，带宽20 MB/s。存储按400 bytes/条目估算JSON/MA/provenance：热缓存LIVE每股每批465条回写（144+48+24+12+6+3+128，2d取满期上界）；native15按同等回写成本假设比较。COLD/WARM按各周期保留上限计算，而非把3072根raw全写入。64交易日5m原始输入约1809.7万bar，响应需求约2.17 GB；各股1136条有界历史落盘约2.68 GB。长期保留144+6×256条的全池逻辑体量约3.96 GB。表中的逻辑回写包括重复INSERT OR REPLACE，不是物理文件增长或实测磁盘流量；未计WAL/索引、元信息、官方快照enriched写入、压缩和峰值内存。处理率是显式假设，未做本机商业全池写盘基准；宿主整轮最后一次合并写入的成本按批分摊，总轮完成仍原子可见。失败情景体积为派发工作量上界。

## 6. S0–S6 的结果

以下是离线预测，基础p50/p95网络0.5/2秒，seed=11。S3-recommended为7分钟目标的同功能基准，S5网络故障与10组敏感性都以该7分钟目标对比；其它S3基础表保留2分钟目标以展示欠账。S0当前开关下关注/盘口为0，稳态已预热假设；当前真实WARM冲突另由S6覆盖。S1和S2均保留同一股票池、ETF/指数快照、关注10股、盘口1股、日K/因子/enriched及1200根/股的盘后1m回补压力负载。S3改变预算/调度，S4为明确降级的轻量页面设想，S5故障，S6冷启动/历史负载。

| 情景 | 快照p50/p95/max 秒 | MA55 p50/p95/max 分 | 全1m p50/p95/max 交易分 | 全1m已覆盖max 分 | 盘初等无覆盖占比 | 关注p50/p95/max 秒 | 盘口p50/p95/max 秒 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S0 | 17.21/31.68/40.03 | 2.31/3.39/3.55 | ∞/∞/∞ | ∞ | 100.00% | N/A | N/A |
| S1 | 17.19/31.63/39.49 | 2.31/3.40/3.56 | 18.00/25.00/∞ | 25.00 | 4.72% | 3.32/5.67/7.56 | 0.45/2.46/4.27 |
| S2 | 17.22/31.79/40.80 | 2.30/3.39/3.54 | 18.00/25.00/∞ | 25.00 | 4.72% | 2.94/4.10/7.51 | 0.40/1.74/3.85 |
| S3-static | 17.05/31.54/40.70 | 3.34/5.44/5.57 | 9.00/11.00/∞ | 12.00 | 2.20% | 0.58/2.87/4.27 | 0.52/2.08/3.48 |
| S3-broker | 17.18/31.71/39.71 | 2.65/4.55/10.44 | 8.00/11.00/∞ | 12.00 | 0.94% | 1.79/16.16/67.88 | 0.55/2.46/4.17 |
| S3-recommended | 17.30/31.81/41.18 | 2.48/4.51/5.57 | 8.00/11.00/∞ | 12.00 | 0.94% | 1.09/5.18/22.53 | 0.46/2.20/7.02 |
| S4 | 17.05/31.69/37.84 | 2.32/3.41/3.63 | ∞/∞/∞ | ∞ | 100.00% | 0.55/2.92/12.79 | 0.51/2.46/6.54 |
| S5 | 17.18/31.71/39.71 | 2.65/4.55/10.44 | 8.00/11.00/∞ | 12.00 | 0.94% | 1.79/16.16/67.88 | 0.55/2.46/4.17 |
| S6-60 | 17.15/31.93/43.03 | 2.67/4.47/10.55 | 8.00/11.00/∞ | 12.00 | 0.94% | 2.38/18.80/37.06 | 0.42/2.47/8.43 |
| S6-120 | 16.71/31.33/38.05 | 2.85/4.62/5.54 | 8.00/11.00/∞ | 12.00 | 0.94% | 1.51/17.44/35.23 | 0.56/2.97/7.31 |

| 情景 | 完成HTTP | 待处理 | 业务超期 | HTTP超时 | 拒绝429 | 本地跳过 | 轮失败取消 | 错过目标起点 | 首次错过时刻 | 队列峰值/请求 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S0 | 4518 | 0 | 0 | 0 | 0 | 0 | 31 | 0 | - | 150 |
| S1 | 6205 | 0 | 756 | 0 | 0 | 6 | 0 | 111 | 09:33:00 | 268 |
| S2 | 4314 | 0 | 754 | 0 | 0 | 3 | 0 | 111 | 09:33:00 | 268 |
| S3-static | 7739 | 0 | 632 | 0 | 0 | 0 | 0 | 96 | 09:33:00 | 268 |
| S3-broker | 7845 | 0 | 176 | 0 | 0 | 0 | 0 | 96 | 09:33:00 | 268 |
| S3-recommended | 7029 | 0 | 96 | 0 | 0 | 0 | 0 | 0 | - | 268 |
| S4 | 2904 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - | 150 |
| S5 | 7695 | 0 | 176 | 0 | 0 | 0 | 149 | 96 | 09:33:00 | 268 |
| S6-60 | 7958 | 0 | 146 | 0 | 0 | 0 | 0 | 96 | 09:33:00 | 313 |
| S6-120 | 7981 | 0 | 198 | 0 | 0 | 0 | 0 | 96 | 09:33:00 | 319 |

| 情景 | 单次59批1m轮p50/p95/max 分 | 单次59批源扫描p50/p95/max 分 | 响应需求GB | 逻辑写GB | 盘后终态/结束 | 30m输入p95 分 | 60m输入p95 分 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S0 | N/A | 2.26/2.37/2.48 | 1.802 | 52.597 | FAIL 15:54:07 | 3.40 | 3.41 |
| S1 | 11.91/11.99/12.03 | 2.28/2.40/2.44 | 4.501 | 61.579 | PASS_CAPACITY_ONLY 16:20:26 | 3.40 | 3.39 |
| S2 | 11.93/11.98/12.01 | 2.26/2.37/2.37 | 4.476 | 26.533 | PASS_CAPACITY_ONLY 16:20:19 | 3.36 | 3.35 |
| S3-static | 5.07/5.58/5.76 | 4.34/4.41/4.44 | 6.670 | 68.835 | PASS_CAPACITY_ONLY 15:52:05 | 5.43 | 5.43 |
| S3-broker | 5.02/5.25/5.56 | 3.49/4.42/5.00 | 6.711 | 68.819 | PASS_CAPACITY_ONLY 15:47:47 | 4.53 | 4.44 |
| S3-recommended | 3.60/4.57/4.59 | 3.02/4.40/5.00 | 5.695 | 65.549 | PASS_CAPACITY_ONLY 15:47:46 | 4.41 | 4.28 |
| S4 | N/A | 2.30/2.46/2.46 | 1.798 | 17.593 | PASS_CAPACITY_ONLY 15:42:30 | 3.39 | 3.37 |
| S5 | 5.02/5.25/5.56 | 3.49/4.42/5.00 | 5.862 | 65.991 | FAIL 15:39:29 | 4.53 | 4.44 |
| S6-60 | 5.02/5.19/5.51 | 3.40/5.00/5.00 | 9.424 | 72.992 | PASS_CAPACITY_ONLY 15:47:45 | 4.50 | 4.26 |
| S6-120 | 5.02/5.25/5.51 | 3.51/4.48/5.00 | 9.452 | 73.171 | PASS_CAPACITY_ONLY 15:47:47 | 4.62 | 4.65 |

| 预热情景 | COLD16完成 | WARM64完成 | 5m分钟峰值 | 关注是否超120秒 |
| --- | --- | --- | --- | --- |
| S6-60 | 09:17:11 | 16:25:20 | 24 | 否 |
| S6-120 | 09:45:48 | 17:25:18 | 24 | 否 |

| 情景 | 供应商各池实际派发峰值/60s | 本地各限流对象峰值/60s | 模拟协调器各池峰值/60s |
| --- | --- | --- | --- |
| S0 | daily=6; factors=24; metadata=24; minute_shared=24; quote_pool=5; quote_symbols=2 | host_all=6; host_background=6; marketdata_daily=1; marketdata_factors=24; marketdata_metadata=24; marketdata_minute_batch=24; marketdata_quote_pool=1 | - |
| S1 | daily=6; depth=1; factors=24; metadata=24; minute_shared=30; quote_pool=5; quote_symbols=2 | host_all=12; host_background=6; marketdata_daily=1; marketdata_factors=24; marketdata_metadata=24; marketdata_minute_batch=24; marketdata_quote_pool=1 | - |
| S2 | daily=6; depth=1; factors=24; metadata=24; minute_shared=30; quote_pool=5; quote_symbols=2 | host_all=12; host_background=6; marketdata_daily=1; marketdata_factors=24; marketdata_metadata=24; marketdata_minute_batch=24; marketdata_quote_pool=1 | - |
| S3-static | daily=24; depth=1; factors=24; metadata=24; minute_shared=24; quote_pool=5; quote_symbols=2 | host_all=24; host_background=24; host_minute_static=12; marketdata_daily=1; marketdata_factors=13; marketdata_metadata=24; marketdata_minute_batch=12; marketdata_quote_pool=1 | - |
| S3-broker | daily=24; depth=1; factors=24; metadata=24; minute_shared=24; quote_pool=5; quote_symbols=2 | host_all=30; host_background=24; marketdata_daily=1; marketdata_factors=19; marketdata_metadata=24; marketdata_minute_batch=24; marketdata_quote_pool=1 | daily=24; depth=1; factors=24; metadata=24; minute_shared=24; quote_pool=5; quote_symbols=2 |
| S3-recommended | daily=24; depth=1; factors=24; metadata=24; minute_shared=24; quote_pool=5; quote_symbols=2 | host_all=30; host_background=24; marketdata_daily=1; marketdata_factors=24; marketdata_metadata=24; marketdata_minute_batch=24; marketdata_quote_pool=1 | daily=24; depth=1; factors=24; metadata=24; minute_shared=24; quote_pool=5; quote_symbols=2 |
| S4 | daily=24; depth=1; factors=24; metadata=24; minute_shared=24; quote_pool=5; quote_symbols=2 | host_all=24; host_background=24; marketdata_daily=1; marketdata_factors=24; marketdata_metadata=24; marketdata_minute_batch=24; marketdata_quote_pool=1 | daily=24; depth=1; factors=24; metadata=24; minute_shared=24; quote_pool=5; quote_symbols=2 |
| S5 | daily=24; depth=1; factors=24; metadata=24; minute_shared=24; quote_pool=5; quote_symbols=2 | host_all=30; host_background=24; marketdata_daily=1; marketdata_factors=19; marketdata_metadata=24; marketdata_minute_batch=24; marketdata_quote_pool=1 | daily=24; depth=1; factors=24; metadata=24; minute_shared=24; quote_pool=5; quote_symbols=2 |
| S6-60 | daily=24; depth=1; factors=24; metadata=24; minute_shared=24; quote_pool=5; quote_symbols=2 | host_all=30; host_background=24; marketdata_daily=1; marketdata_factors=14; marketdata_metadata=24; marketdata_minute_batch=24; marketdata_quote_pool=1 | daily=24; depth=1; factors=24; metadata=24; minute_shared=24; quote_pool=5; quote_symbols=2 |
| S6-120 | daily=24; depth=1; factors=24; metadata=24; minute_shared=24; quote_pool=5; quote_symbols=2 | host_all=30; host_background=24; marketdata_daily=1; marketdata_factors=14; marketdata_metadata=24; marketdata_minute_batch=24; marketdata_quote_pool=1 | daily=24; depth=1; factors=24; metadata=24; minute_shared=24; quote_pool=5; quote_symbols=2 |

名义情景全部证券都被更新：5m为48次/证券，native15为16次/证券，min/max=1，没有把关注股覆盖当成全池覆盖。S5断网通过合并旧扫描回补，最少/最多更新次数约0.98，但最终没有遗漏批组；缺失历史bar的可用延迟照常累积。慢网络/处理组合会失去全池公平性，见敏感性。

数据日期：S1/S2/S3/S5/S6在首次有效full1m整轮完成后可提供**模拟11-09**分钟数据；S0/S4不提供当天原生全市场1m，运行态实际仍为11-06。按需1m只让关注池具备当天输入，不等于全市场分钟信号。板块分类所有情景仍为 `UNSUPPORTED / SOURCE_MISSING`，因此日期齐备也不代表当日分类可靠。

S6将COLD16放盘前、WARM64放15:20，分别施加60/120分钟历史CPU工作量（按18,097,152行反推处理率），不是两次真实测速。WARM完成约16:25/17:25，盘后管道约15:48，且盘口/关注有响应；每股事务/整批计算耗时再上升时需推迟未完成warmup，不应暂停宿主风控或掩盖阶段错误。

## 7. 容量搜索、Pareto与敏感性

穷举：8种full1m目标间隔 × 4种快照间隔 × 2种MA输入 × 静态/协调2种策略 = **128**。每项同样保留全市场1m、关注10、盘口1、ETF/指数、盘后因子及分钟；分钟总预算24。约束为快照p95≤60秒、MA55 p95≤5分钟（另测3分钟）、全1m p95≤5分钟、盘后≤17:30、关注/盘口≤120秒、无429、目标轮次不欠账、截止无待取请求。

| MA源/分 | 全1m目标间隔/分 | 分钟平均需求RPM | MA55 p95/分 | 1m p95/交易分 | 错过起点数 | 目标节奏 | 除1m时效外SLO |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5 | 1 | 71.3 | 4.55 | 11.00 | 228 | FAIL | FAIL |
| 5 | 2 | 41.8 | 4.55 | 11.00 | 96 | FAIL | FAIL |
| 5 | 3 | 31.9667 | 5.24 | 11.00 | 46 | FAIL | FAIL |
| 5 | 4 | 27.05 | 5.19 | 11.00 | 45 | FAIL | FAIL |
| 5 | 5 | 24.1 | 4.42 | 11.00 | 32 | FAIL | FAIL |
| 5 | 7 | 20.7286 | 4.51 | 11.00 | 0 | PASS | PASS |
| 5 | 10 | 18.2 | 5.22 | 14.00 | 0 | PASS | FAIL |
| 5 | 15 | 16.2333 | 5.40 | 19.00 | 0 | PASS | FAIL |
| 15 | 1 | 63.4333 | 4.31 | 8.00 | 198 | FAIL | FAIL |
| 15 | 2 | 33.9333 | 4.31 | 8.00 | 94 | FAIL | FAIL |
| 15 | 3 | 24.1 | 5.35 | 8.00 | 23 | FAIL | FAIL |
| 15 | 4 | 19.1833 | 5.19 | 8.00 | 7 | FAIL | FAIL |
| 15 | 5 | 16.2333 | 5.43 | 9.00 | 0 | PASS | FAIL |
| 15 | 7 | 12.8619 | 4.57 | 10.00 | 0 | PASS | PASS |
| 15 | 10 | 10.3333 | 5.23 | 13.00 | 0 | PASS | FAIL |
| 15 | 15 | 8.3667 | 5.48 | 19.00 | 0 | PASS | FAIL |

| MA源/分 | 策略 | 全1m间隔/分 | 快照配置/秒 | 快照p95/秒 | MA55 p95/分 | 1m p95/交易分 | 逻辑写GB |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5 | broker | 7 | 15 | 24.13 | 4.50 | 11.00 | 65.549 |
| 15 | broker | 7 | 15 | 28.21 | 4.62 | 10.00 | 30.520 |
| 15 | broker | 7 | 30 | 31.70 | 4.57 | 10.00 | 30.520 |

**全SLO满足0项；仅放宽全1m的5分钟时效后5项满足。** Pareto目标为快照、MA55、1m p95和逻辑写量；这是名义处理条件下的局部容量边界。主表推荐既有5m输入、快照30秒、full1m 7分钟，见S3-recommended。10分钟组合的平均需求更小，但当前批次老化/相位调度仍使MA55 p95略超5分钟，不能因间隔更长就宣布SLO达标。7分钟平均分钟需求20.73/min，10分钟18.2/min，均由rolling24仲裁，余量未借给常驻历史扫描。静态12+12在基本S3里MA55 p95超过5分钟，说明“合计没超额”不等于SLO达到。

**更严格的账户总池模型**：如果供应商全部HTTP共用30，协调器以总24、其中6给快照、所有后台合计18执行；这层跨客户端预留已在仿真实现。当前独立对象模拟峰值超过30，出现拒绝；这是反事实压力测试，非现网429证据。

| MA源/分 | 1m间隔/分 | 快照p95/秒 | MA55 p95/分 | 1m p95/交易分 | 目标节奏 | 其余SLO |
| --- | --- | --- | --- | --- | --- | --- |
| 5 | 7 | 31.89 | 50.20 | 15.00 | FAIL | FAIL |
| 5 | 10 | 32.02 | 9.34 | 17.00 | PASS | FAIL |
| 5 | 15 | 31.85 | 9.53 | 21.00 | PASS | FAIL |
| 15 | 7 | 32.12 | 6.45 | 12.00 | PASS | FAIL |
| 15 | 10 | 31.94 | 6.61 | 15.00 | PASS | FAIL |
| 15 | 15 | 31.55 | 7.41 | 21.00 | PASS | FAIL |

| 严格账户情景 | 所有HTTP滚动峰值 | 429拒绝 | 后台协调峰值 |
| --- | --- | --- | --- |
| S0 | 32 | 16 | 未协调 |
| S1 | 33 | 453 | 未协调 |
| S2 | 33 | 159 | 未协调 |
| S3-static | 32 | 336 | 未协调 |
| S3-broker | 24 | 0 | 18 |

在该更严格假设下，6组额外搜索均没有达到其余SLO。一个**遵守额度的降级起点**是：全账户24 rolling、后台18、快照30秒、原5m、full1m15分钟、关注10/盘口1各120秒；平均总HTTP约22.73/min，源日常因子/元信息需盘前或低峰让路。它保证的是模拟额度，不保证MA55≤5分钟；当前首次因子取数与批次调度仍会错过该目标。启动前刷新因子、按证券公平游标和本机真实CPU成本是下一轮需要解决的具体条件。

独立周期配额只作为 `--independent-periods` 反事实：1m与5m各峰值24，1m p95约6分钟；该模式既无账户证据，也未达到全部SLO，不能用于线上扩量。

| seed | 股票数 | 关注/盘口 | 网络p50/p95 秒 | 解析/写入 行/秒 | 事务ms/股 | 消费者秒 | 5m MA55 p95 分 | 15m MA55 p95 分 | 5m目标 | 15m目标 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 5500 | 10/1 | 0.1/0.5 | 200000/100000 | 1 | 10 | 4.29 | 4.27 | PASS | PASS |
| 2 | 5891 | 50/5 | 0.5/2 | 100000/50000 | 5 | 30 | 5.15 | 5.30 | FAIL | FAIL |
| 3 | 6100 | 100/10 | 2/5 | 50000/20000 | 10 | 60 | 67.59 | 7.76 | FAIL | FAIL |
| 4 | 5500 | 10/1 | 5/10 | 20000/10000 | 20 | 180 | 115.86 | 14.97 | FAIL | FAIL |
| 5 | 5891 | 50/5 | 0.1/0.5 | 200000/100000 | 1 | 10 | 4.40 | 4.41 | PASS | PASS |
| 6 | 6100 | 100/10 | 0.5/2 | 100000/50000 | 5 | 30 | 5.57 | 5.56 | FAIL | FAIL |
| 7 | 5500 | 10/1 | 2/5 | 50000/20000 | 10 | 60 | 11.93 | 7.16 | FAIL | FAIL |
| 8 | 5891 | 50/5 | 5/10 | 20000/10000 | 20 | 180 | 116.16 | 15.54 | FAIL | FAIL |
| 9 | 6100 | 100/10 | 0.1/0.5 | 200000/100000 | 1 | 10 | 5.18 | 5.19 | FAIL | FAIL |
| 10 | 5500 | 10/1 | 0.5/2 | 100000/50000 | 5 | 30 | 4.85 | 4.91 | PASS | PASS |

10个种子在full1m目标间隔7分钟下，各做相同功能覆盖的5m/native15配对，含5500/5891/6100证券、10/50/100关注、1/5/10盘口。p50/p95为0.1/0.5、0.5/2、2/5、5/10秒，并同步降低解析/写入、增加事务和消费者成本。快照目标在这10组可达；MA55≤5分钟只在较快组合成立，≤3分钟没有通过。5秒网络叠加慢处理时5m吞吐落后桶推进，部分批组反复被新目标覆盖，时延急剧放大；原生15m减少扫描在这种条件下显著有益，仍未达到本轮MA55 SLO。此结果要求后续插件做公平调度，不能解释为实际股票行情已延迟对应分钟。

## 8. 故障与15m等价性

| 故障 | HTTP超时 | 429 | 最短重试等待秒 | 合并旧请求 | MA55 p50/p95/max 分 | 1m p50/p95/max 分 | 最旧快照 秒 | 全池恢复 秒 | 故障期功能 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S5-429-10 | 0 | 1 | 31 | 2 | 2.47/4.44/5.56 | 8.00/11.00/∞ | 40.13 | - | FAIL |
| S5-outage-5 | 17 | 0 | 5 | 38 | 2.55/5.50/9.51 | 8.00/12.00/∞ | 322.63 | 270.527 | FAIL |
| S5-outage-10 | 38 | 0 | 5 | 95 | 2.60/10.81/13.92 | 8.00/15.00/∞ | 622.63 | 235.053 | FAIL |
| S5-late_bar-10 | 0 | 0 | None | 2 | 2.52/5.18/∞ | 8.00/11.00/∞ | 41.18 | - | FAIL |

429实际扣一次额度，collector等待Retry-After30秒+1秒余量后重试；全池恢复时间按断网结束时最新完成目标、所有批组恢复计，不用第一只股票代替。故障期间的业务失败/不可用均保留。周末、显式节假日、跨日、关停无生产写入、真实1m源错误不切TickFlow、真实grid缺K/更正、冻结BUY测试夹具等也由定向测试覆盖。

| 故障 | 实际识别状态 | 功能状态 | 准入检查 |
| --- | --- | --- | --- |
| cross_day_cache | INVALID_TIME | FAIL | PASS |
| factor_unverified | FACTOR_PENDING | FAIL | PASS |
| historical_correction | REBUILD_PENDING | FAIL | PASS |
| late_bar | DATA_PENDING | FAIL | PASS |
| lunch_gap | DATA_PENDING | FAIL | PASS |
| missing_symbol_200 | UNVERIFIED | FAIL | PASS |
| new_listing | NOT_YET_LISTED | EXPECTED_EXCLUSION | PASS |
| price_mismatch | FAIL | FAIL | PASS |
| single_1m_gap | DATA_PENDING | FAIL | PASS |
| stale_frontend | STALE_DISPLAY | FAIL | PASS |
| suspension | SUSPENDED | EXPECTED_NO_BAR | PASS |
| timestamp_start_end | 0 | EQUAL_SYNTHETIC_ONLY | PASS |
| unclosed_bar | INVALID_TIME | FAIL | PASS |

数据故障表中的check_passed表示**合成输入的准入检查正确识别结果**；`feature_status=FAIL` 就是该功能失败，不是生产容错已经修好。其中实际5m grid聚合、MA55 process_symbol、宿主 `_try_custom_minute` 另有真实源码隔离测试；没有修改/猴补生产worker或系统时间。

原生15m证据分级：

| 检查 | 结果 |
| --- | --- |
| SDK0.1.0实际HTTP hook + MockTransport，股票/ETF/指数快照各一次 | PASS，3次合成HTTP，无隐式逐股请求 |
| klines.batch 100证券、period=15m、adjust=none、to_dataframe=False | PASS_MOCK_TRANSPORT，1次合成HTTP；只证明SDK发送/返回形状 |
| native15起/止标签、手→股、完整2/4根30/60m、午休、未完成桶、价格差异检测 | PASS合成夹具；保留 `QUANTDASH_NATIVE_15M`，无伪造source_count=3 |
| 同日真实5m聚合 vs 原生15m的OHLC、量、额、午休、收盘、除权日 | **NOT_VERIFIED**；样本数0，差异计数/误差为N/A |
| 个人账户native15批量权限/100股完整响应/历史深度 | **NOT_VERIFIED** |
| 冻结真实BUY全量零漂移与并行影子 | **NOT_VERIFIED**；本轮只核验合成既有确认记录保留，未运行生产MA55改写或真实全量对照包 |

## 9. 下一轮路径与回退

### A. 仅靠已有设置

维持当前独立capability路由和30秒快照；1m按需保留，小盘口先限定1股。full_minute当前关闭，本轮未开启；如以后仅用现有配置开启，最低节奏为60秒但默认预算一轮约10分钟下界、含关注负载约12分钟，120秒设置不能实现全池120秒。可先接受15分钟级轮动更新目标，并由开盘有界测试决定是否可用；它不满足本轮全1m 5分钟目标。

盘后分钟可用既有开关，当前默认关、天数默认5；已有历史完整时可提出仅补当日（days=1），历史缺口仍需按真实窗口补齐。分类只有旧快照的QuantDash-only环境，可停用无法取得合法源的Fuyao拉取，保留原快照及来源日期；本轮未实际修改。现有 warm_symbols 限定深预热范围，**没有时间窗设置**，不能虚构一个配置字段实现15:35自动让路。

补充9组**现有配置可以表达**的静态预算实验：宿主总14/16/18，公共分钟16/14/12，分别让host后台8/10/12与公共分钟合计24。条件仍是其它端点独立计费。较合理的配置候选为宿主16、公共14、full1m7–10分钟；预测MA55约4.5分钟，但盘口仍有非阻塞跳过，不能承诺保留全部交互SLO。这比只改host RPM准确，也无需先改生产源码。它没有账户总HTTP保护，本轮仅模拟，没有写环境/设置或重启服务。

| host总/后台 | 公共分钟 | 1m间隔/分 | 分钟峰值 | MA55 p95 分 | 1m p95 交易分 | 错过起点 | 盘口跳过数 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 14/8 | 16 | 7 | 24 | 4.39 | 18.00 | 30 | 4 |
| 14/8 | 16 | 10 | 24 | 4.38 | 19.00 | 0 | 16 |
| 14/8 | 16 | 15 | 24 | 4.39 | 23.00 | 0 | 9 |
| 16/10 | 14 | 7 | 24 | 4.60 | 14.00 | 0 | 17 |
| 16/10 | 14 | 10 | 24 | 4.71 | 16.00 | 0 | 16 |
| 16/10 | 14 | 15 | 24 | 4.62 | 21.00 | 0 | 12 |
| 18/12 | 12 | 7 | 24 | 5.44 | 13.00 | 0 | 20 |
| 18/12 | 12 | 10 | 24 | 5.45 | 15.00 | 0 | 11 |
| 18/12 | 12 | 15 | 24 | 5.43 | 20.00 | 0 | 12 |

### B. 最小插件改动，留到下一轮

| 范围 | 具体改动 / 验证 | 回退 |
| --- | --- | --- |
| Provider `client.py` + 公共 `request_budget.py`、`collector.py`、`runtime.py` | 将真实HTTP admission接到同一账户预算，保留端点分类、快照预留和统计；替换当前host_background静态扣减；测试两个对象同窗、429、重启预算延续、冷启动/盘后让路 | 官方管理器回退原Provider1.2.6/公共1.0.2；本轮未安装新版本 |
| 同上调度 | 完成K锚点/同请求合并、关注批优先且公平游标轮转；缺响应股票保留待核实状态；历史预热在盘中/15:35主动让路，既有事务完成后再暂停历史任务 | 保留原5m生产模式及旧库；恢复旧包，未改宿主调度器 |
| Provider `provider.py` | 用官方已有stock get_instruments入口映射QD元信息，缺字段保留既有可核实值；因子遗漏继续显式失败/待核实，不填1 | 回退包；保留宿主既有维表、因子文件 |
| 公共 `grid.py`/`store.py`、MA55 `pipeline.py` 的未来输入边界 | 先取得真实native15成对证据；显式 native provenance/完整性契约；2/4根聚合图表；新旧输入并行影子和冻结真实BUY全量零漂移，之后才讨论适配，冻结买点公式不变 | 原5m链随时恢复；不迁移或覆盖旧BUY，原始备份保留 |
| 公共历史缓存 | 只淘汰已经提交并可重取的历史窗口/SDK缓存，保留指标所需真实状态、因子修订、缺口与ACK；测试已预热不重拉及历史更正 | 关停后保留原数据库副本；不自行删旧1m或信号库 |

当前同进程可用一个共享预算对象；跨进程需要插件自有的IPC/锁保护额度账本、原子记录实际派发时间、崩溃后保留未过期60秒额度和Retry-After、进程存活与重连处理。成本明显高于改一个RPM数字；预算服务只仲裁，不成为第三个行情producer，不使用宿主私有模块替换。升级时核对官方插件/扩展v1公开契约，保留旧包回退。

### C. 不改宿主仍不能保证的能力

便宜的全市场1m增量接口未获证实，官方全市场1m轮动30/60秒无法保证；不能把15m写进1m库。分类当日成分缺合法独立源时为SOURCE_MISSING；KPL示例、DEMO和文件mtime不能填补。因子UNVERIFIED仍使官方盘后失败；没有公开阶段错误策略允许插件跳过并标整链成功。

新增发现的ETF/指数自动主数据链直接走TickFlow，现有公开Provider接口不能替换该路径；严格唯一QD时只能使用注明来源的既有主数据，自动更新应标不可保证。股票主数据则已有公开适配点，可以按B做最小Provider补齐。财务及依赖它的原生功能同样不属于当前QD插件已声明能力。

### D. 最终预算判断

同Key、不同对象已经证实；当前分钟静态6+24在稳定实例下能守住假设30，但没有余量，也没有保护潜在账户全HTTP共池。本轮负面仿真不能当现场429。下一轮优先实现**账户总24、快照预留6、后台18**这一严格协调模式，接受full1m15分钟及MA55延迟降级；确认供应商端点配额独立后，才评估分钟24的7分钟候选。上线条件是有界开盘验证实际额度scope、bar完成时间、全池处理/写盘成本、关注响应和盘后真实终态，本轮没有改线上任何值。

## 10. 验收、运行与交付

实际命令（所有Python均为既有nga；示例从工程根目录执行）：

```powershell
$PY='D:\Python_Envs\envs\nga\python.exe'
& $PY -B -X utf8 -m pytest -q -p no:cacheprovider examples/quantdash-data-source/tests/test_capacity_sim.py
& $PY -B -X utf8 -m pytest -q -p no:cacheprovider examples/quantdash-data-source/tests
& $PY -B -X utf8 -m pytest -q -p no:cacheprovider addons/lan_marketdata/tests_backend
& $PY -B -X utf8 -m ruff check examples/quantdash-data-source/capacity_sim examples/quantdash-data-source/tests/test_capacity_sim.py
Set-Location examples/quantdash-data-source
& $PY -B -X utf8 -m capacity_sim.cli --search --sensitivity 10 --output ../../data/diagnostics/capacity_sim_20261109/run.json
& $PY -B -X utf8 -m capacity_sim.cli --vendor-scope account --output ../../data/diagnostics/capacity_sim_20261109/account.json
& $PY -B -X utf8 -m capacity_sim.cli --scenario S3-broker --independent-periods --output ../../data/diagnostics/capacity_sim_20261109/independent.json
& $PY -B -X utf8 -m capacity_sim.cli --scenario S1 --host-rpm 16 --market-rpm 14 --full-minutes 7 --output ../../data/diagnostics/capacity_sim_20261109/config16-14.json
```

结果：新模拟测试 **22 passed**；Provider目录合计 **69 passed + 12 subtests**；公共行情既有测试 **27 passed**（一条既有Starlette/httpx弃用警告，无依赖调整）；Ruff **All checks passed**。CLI的同参数重复输出逐字节相等；交易日/周末/显式holiday、滚动边界和两客户端争用均通过。128组合+10配对敏感性、14个情景及严格账户6组合、9组现有配置切分已执行；仅保存私有汇总JSON，不提交逐请求trace。

未执行：任何全市场商业采集、真实native15比较、真实BUY全量影子、生产轮动/分类补采、插件安装、服务重启、前端构建、全量pytest。所需真实15m样本/冻结实盘对照包与开盘窗口仍缺，因此没有实盘SLA或BUY等价性PASS。

交付文件白名单：`examples/quantdash-data-source/capacity_sim/*.py`、`examples/quantdash-data-source/tests/test_capacity_sim.py` 和本报告。工程外指令文件、SDK、行情、Key、数据库、缓存、ZIP和构建输出不提交。首轮相对输出路径曾多退一层，留下一个工程外**合成汇总**副本；自动审批以“blocked by policy”拒绝了移动/清理，该副本保留。正式结果已在被忽略的私有目录重跑，CLI现已拒绝工程外输出路径。

交付前再次核对：相对官方v0.3.32及相对开发基线，`backend/app/**`、`frontend/src/**`、`dev.ps1` 差异均为空；仅新增6个模拟模块、1个测试文件和本报告。`git diff --cached --check`、精确8文件白名单检查用于提交验收，原 `REVIEW_SUMMARY.md` 留在未跟踪状态。

个人Fork开发分支：[feature/tsp-quantdash-capacity-sim-20261109](https://github.com/DMshimakaze/tick-stock-panel/tree/feature/tsp-quantdash-capacity-sim-20261109)；[相对核实基线的完整差异](https://github.com/DMshimakaze/tick-stock-panel/compare/a65d60d9ee5eeb60592dc728d6a3236960c192c1...feature/tsp-quantdash-capacity-sim-20261109)。没有合并、squash或推送main，也没有推送官方仓库。

本轮没有部署动作，生产回退不需要操作；若只移除这次诊断工具，使用原开发分支即可，原未跟踪文件保留。交付提交的完整SHA及远端复核在聊天交付信息中给出。
