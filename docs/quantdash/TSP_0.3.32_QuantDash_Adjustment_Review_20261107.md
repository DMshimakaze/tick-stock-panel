本轮已升级并运行 TSP v0.3.32，QuantDash v1.2.6 已激活，定向代码验收通过。源事件覆盖仍有 **1,635 个未核实代码和 1 个非正因子代码**；本轮完成了原因核查与定向在线复核，没有把缺响应改成无事件。日期：2026-11-07，北京时间。

**版本、分支与运行环境**

| 项目 | 实际结果 |
|---|---|
| 升级前 | TSP 0.3.29；`cc7919d0e2719795ec5754c334eb3307aa5218cc` |
| 官方正式标签 | `v0.3.32` → `ca3a44252edb414e3f4c1df1b7c381bad9f55c8b`，VERSION 内容为 v0.3.32 |
| DCO 合并提交 | `e0dba24246b4f522ea27aa6ac703a1bce3e2bdbe`；两个父提交分别为原 QuantDash 分支与正式标签 |
| 完成修复的代码验收 SHA | `20ab6dc0090315c59cb1d60998b4d533c783e74f`；本报告随后单独提交 |
| 新开发分支 | [feature/tsp-quantdash-v0.3.32](https://github.com/DMshimakaze/tick-stock-panel/tree/feature/tsp-quantdash-v0.3.32) |
| 旧开发分支 | 保留 `feature/tsp-quantdash` 及 b82f76e2、9a11768c、5af8f753、cc7919d0 全部历史 |
| main | Fork main 基线为 `ea4d38e0dc9259ad3500f3c248ab991cd12d3ea1`；本轮推送目标仅为新开发分支 |
| 实际运行 | 后端 OpenAPI 0.3.32；前端页面显示 v0.3.32；源码方式运行，数据根仍为项目 `data/` |
| 机器 | Windows 10 x64（10.0.19045）；PowerShell 7.6.9；时区 Asia/Shanghai |

`origin` 保留原作者地址；新增只读 `upstream` 后 fetch 正式标签，从 personal 的最新 QuantDash 分支建立新分支再 merge。只解决实际冲突：`api/kline.py`、`services/kline_sync.py`；仓库其余官方改动正常合入，未用官方目录覆盖插件源码。最终含本报告的提交 SHA 及远端核验结果记录在本地 `dist/quantdash_upgrade_20261107/final_git_receipt.json`，并在交付回复中提供。

`conda env list`、`conda run -n nga` 和 `sys.prefix` 均确认已有环境为 **`D:\Python_Envs\envs\nga\python.exe`**，Python **3.12.7**。测试与最终真实后端进程均使用该解释器；升级前后端由项目旧 `.venv` 启动，实际子进程是 base 的 `D:\Python_Envs\python.exe`，与 nga 不同。本轮没有创建环境或向 base/系统 Python 安装依赖。

nga 原来已有 QuantDash **0.1.0**，缺少 Polars 等 TSP 依赖。先检查 MA55 的包约束及 pip check，再按正式版本 `backend/uv.lock` 补齐必要依赖；最终 Polars **1.44.1**，满足宿主与插件 `>=1.44,<1.45`。tdxaidata 从 1.0.2 升为宿主要求的 1.2.2；原 NumPy 2.4.6、pandas 3.0.3、QuantDash 0.1.0 未变，安装前后 pip check 均通过。没有写 MA55 的代码、数据库、策略或持久环境变量。

`dev.ps1` 增加显式 `-PythonExecutable` 参数：指定已准备好的解释器时不执行 uv sync、不创建 `.venv`。本机原有、被 Git 忽略的 `Start-TSP-Local.ps1` 已接入 nga；两个 PowerShell 文件语法检查通过。真实服务按同一 nga 的原生 uvicorn 命令启动，正常 Ctrl+C 停止旧进程，没有强杀。原有未跟踪 `REVIEW_SUMMARY.md` 保留。

**插件生效与六项能力**

源码 **1.2.6 / active 1.2.6 / pending null / previous 1.2.5**，available=true，Key 已配置=true。凭据和能力选择沿用既有配置，没有重新填 Key或手改 state.json。生命周期仍由官方插件管理器负责；生产实际执行 inspect、install、正常重启激活，切换/卸载/失败回退由 Mock 生命周期测试验证。

包：`dist/quantdash_upgrade_20261107/TSP-QuantDash-Plugin-v1.2.6.zip`；SHA256：`94329721c143252b0fd1025623d2eeb2f5e958f25ae5584044f146de9d1f33e5`。原生 inspect 无兼容错误，ZIP 根为 plugin.yaml、provider.py、client.py、mapping.py、README.md，未携带 SDK、Key 或测试数据。

| 能力 | 实际路由 | 本轮有界验证 |
|---|---|---|
| daily | quantdash_user | 六个源参考样本；另对缺因子的股票/ETF 各取到 10 条原始日线 |
| minute | quantdash_user | 600519.SH、588170.SH、159516.SZ、000001.SH 各 241 条 2026-11-06 分钟；后两类包含源查询 |
| realtime | quantdash_user | 手动原生刷新取得股票 5,573、ETF 1,633、指数 9；核心四指数另行核对 |
| adj_factor | quantdash_user | 未完成集合 17 次请求；六个源参考段及两个缺键代码单点复核 |
| depth5 | quantdash_user | 600519.SH 单股五档成功；未开启大池监控 |
| full_minute | quantdash_user | 保留原生 get_minute 修复轮契约；周六单股当日试拉 0 行、无错误，未当作盘中通过 |

财务路由仍为 fuyao；实时轮询 30 秒，自动分钟历史同步、全市场分钟刷新、大池五档监控均保持关闭。所有行情请求通过已加载 provider 的同一 SDK 实例、同一滚动预算，实际配置 12 次/60 秒，预留 6 次给实时；没有提高 RPM 或启动独立取数客户端。此轮日志共 40 次 QuantDash HTTP 请求，均 HTTP 200；其中 17 次为未完成集合审计。实际出现一次约 57.97 秒的背景额度等待；真实 429 未发生，429 恢复由 Mock 验证。

新增可选 `get_realtime_symbols(symbols, asset_type=...)` 有真实需求：现有 ETF 快照缺少维表中的 152 个代码。实现按股票/ETF 隔离、去重、单批最多 100、额度不足立即返回、共享原预算且不占用实时预留。原生订阅补拉实际对 158015.SZ 请求 1 个、返回 0 个，保留缺失事实；没有宣称 152 个缺口已补齐。ETF 权限失败保留股票成功结果，覆盖对应 Mock。

报价时间戳为北京时间 **2026-11-06 15:00:03/06**；周六运行状态为 closed。日线/分钟属于真实数据日期 11 月 6 日。指数日线 8 条的 raw_close 与 close 一致；指数使用原始点位。VWAP 价量核验适用于股票、ETF，指数的成份成交汇总不作为点位 VWAP。

**复权实现与修复**

仍使用原生 `_apply_adj_factor`、`apply_minute_adjustment`，没有重写算法或另建复权系统：

```text
ratio(D) = product(ex_factor where event_date <= D) / product(all ex_factor)
adjusted_OHLC(D) = raw_OHLC(D) * ratio(D)
```

原始日线/分钟请求始终 adjust=none；源 forward 数据仅作只读参考。股票/ETF 因子分别存正式 adj_factor、adj_factor_etf；指数不引入这些因子。日线 volume 保留原值，分钟 amount 与价格同比例投影、volume 保留原值，最新价锚定真实价。

- 修复同日 UNVERIFIED 被 through=today 跳过；`?retry_only=true` 仅重试未确认代码，跨日也不刷新其他已确认证券。普通续跑保留中断进度，`?refresh=true` 可明确重新核实完整范围。
- 待重算账本只在正式发布成功后，按实际产出证券清除；没有历史的证券不伪造重算，零产出/中断保留待办。源漏掉旧事件时保留正式旧值并记录差异，未擅自认定撤销；单个坏因子不阻断同批其他代码。
- 合并官方 daily_bootstrap 的按需补齐与共享任务行为，复用已存 raw，保持选定因子源、ETF 路由与 strict 失败语义；已选 QuantDash 时不无声走 TickFlow。
- 新增复现确认了旧 raw 缓存完全位于请求窗口外时会遮住源请求；已限定缓存日期窗口，正常补齐仍经原生服务。
- 保留上一轮当日真实价锚点、反向事件比率、分钟 amount/价格同步投影、文件版本缓存失效和旧 ETF 分钟只读兼容。没有运行真实库的分钟基准迁移；原 `.raw_basis` 仍在。
- Windows 实测发现损坏 Parquet 检查构造失败时未释放句柄，阻止后续原子替换；改为显式流上下文，复现用例通过。ZIP 检查改用原始成员名，避免 Windows 的 ZipInfo 规范化隐藏反斜线；测试包也保留故意构造的原始名称。

这些改动分别位于 provider、services、indicators、已有启动器；前端业务源码只合入正式版本，未另写计算器或修改 MA55。因子/派生发布沿用仓库视图、generation、缓存失效与数据更新通知。

**全证券覆盖与未完成原因**

本轮重新读取实际维表、raw 日线/分钟并集与审计账本，仍为 7,676 个唯一股票/ETF 代码，包含维表外历史代码。按维表识别旧目录 ETF，未重复算成股票。

| 资产 | 范围 | 有事件 | 窗口内明确无事件 | 未核实 | 失败 | 正式事件行 |
|---|---:|---:|---:|---:|---:|---:|
| 股票 | 5,891（SH 2,468 / SZ 3,068 / BJ 355） | 4,072 | 1,673 | 145 | 1 | 7,497 |
| ETF | 1,785（SH 998 / SZ 787） | 9 | 286 | 1,490 | 0 | 12 |

已确认 6,040/7,676（78.69%），未核实 1,635（21.30%），失败 1。本轮新增确认 0；未完成合计仍为 21.31%。明确无事件限定于各码实际审计窗口，不代表整个上市历史。

本轮任务 `c2ba541ac5` 只请求股票 146 个、ETF 1,490 个：2+15 个 HTTP 批次，126 秒，新增/更正事件 0、历史重算 0、待重算队列空。任务据实 failed，明确报告 1,636 个未完成覆盖；没有重拉全部 7,676 个，也没有无变化强制重建历史。

原因统计：股票缺证券键 SH 77 / SZ 27 / BJ 41；ETF 缺证券键 SH 815 / SZ 675。另 **920427.BJ 为非正 ex_factor**。缺键集合中股票 4 个、ETF 145 个没有本地 K 线；ETF 另 1,345 个只有本地 11 月 6 日快照。没有证据把缺键归为停牌、退市或无事件。

已检查已安装 SDK 0.1.0：单批 ex_factors 直接返回源 data，未发现客户端把显式空列表删键的逻辑；本轮可读取的[官方示例](https://github.com/quantdash-net/QuantDash)没有提供“省略键等于无事件”的保证。额外对 001239.SZ、158000.SZ 分别单点复核：原始日线各成功 10 条，因子单点仍缺证券键；对应 HTTP 均 200。故已排除本地没有历史是唯一原因、普通日线接口不可用、SDK 分批合并丢键等解释，仍不能证明事件完整性。下一步最小行动是让源方确认这两个脱敏实例的省略键契约/覆盖，再仅重试未完成集合；未自动向外发送工单。

**重新采集的数值与现场核对**

六个样本均由同一运行 provider 重新取得相同参数的 none、forward、ex_factors，未复用旧参考 JSON。源 raw/forward 日期对齐缺失 0，OHLC 绝对容差为 1e-7；最大实际误差 **6.8213e-13**，最新原始价锚点误差均为 0。未对入库价格机械四舍五入。

| 样本 | 源参考条数 / 事件数 | 与现有 API 重叠条数 | 源 OHLC 最大误差 |
|---|---:|---:|---:|
| 600519.SH | 484 / 4 | 245 | 6.83e-13 |
| 588170.SH | 385 / 1 | 368 | 2.23e-16 |
| 159516.SZ | 484 / 2 | 484 | 1.12e-16 |
| 000002.SZ | 484 / 0 | 245 | 0 |
| 920116.BJ | 447 / 2 | 245 | 1.43e-14 |
| 510300.SH | 484 / 2 | 1 | 8.89e-16 |

每个事件均核对前后各 10 根交易日 K 线。159516 两次事件为 2026-03-30、2026-07-10，两个 21 根窗口及累计链均通过；600519 四次分红窗口、588170 拆分窗口也通过。API OHLC 最大误差 4.55e-13，MA5/10/20/60 最大误差 2.28e-13。

API 比较限定于实际持有历史：例如 600519 的源参考另 239 条不在本地历史，未自动补下载。510300 的本地无事件窗口只有 2026-11-06 一条，较长源参考有两次事件；该单日不具备均线预热长度，未把空 MA 判为均线验证通过。两只持有长历史的 ETF 均完成了均线对照。

600519、588170、159516 的日线、MA 和分时已在实际前端打开核对，数据日期均为 2026-11-06；股票按两位、ETF 详情按三位小数显示，API 与可见值在半个显示最小单位内一致。分钟成交量为手、成交额为元，累计 amount/(volume×100) 与可见均价一致。现场出现过旧 Vite 缓存仍提供图表库 v4 的错误；正常停止前端并重新预构建依赖后恢复，复核日线/分时均已渲染。

上一轮全库 1,349,395 条日线及 1,349,841 条分钟收盘投影一致性记录属于 **2026-11-06 历史提交报告，未重新采集或重跑全库**。本轮证据是上述新采集的六样本、当次有界 API/图表核对及未完成集合在线审计，未把历史统计冒充新一轮全市场验收。

**测试、数据保护及复现**

后端指定集合共收集 296 项，分批验证后 **294 通过、2 跳过**；两个跳过均因当前 Windows 没有符号链接权限。中间真实失败已经由相应模块复测关闭：Windows 源码包/修补模块 75 通过、2 跳过；最终图表/分钟/审计/自选复测 53 通过；其余 166 项使用本轮已经成功的定向回归结果。插件 47 项通过，前端 9 文件 52 项通过，pnpm build 通过；只按锁文件更新 1 个图表依赖，没有全量 npm 重装。17 个相关 Python 文件 Ruff 基线/当前均 71 个既有问题，新增 0；审计与核对脚本 Ruff 通过，git diff --check 通过。

Mock 覆盖双事件、同日冲突、反向事件、确认无事件/缺响应/非法因子、ETF 分区和旧分钟、指数恒等、缓存失效、重复幂等、更正后局部重算、当日锚点、403、429/中断恢复及插件安装/切换/卸载。所有测试均在现有 nga，DATA_DIR 隔离到本轮临时目录。以下为实际使用的命令形式：

```powershell
Set-Location 'E:\tick stock panel\backend'
$env:PYTHONUTF8='1'; $env:PYTHONDONTWRITEBYTECODE='1'
$env:DATA_DIR='E:/tick stock panel/dist/quantdash_upgrade_20261107/test_runtime'
$py='D:/Python_Envs/envs/nga/python.exe'
& $py -m pytest tests/test_source_packages.py tests/test_precise_daily_repair.py -q --disable-warnings -p no:cacheprovider --basetemp ../dist/quantdash_upgrade_20261107/pytest_tmp
& $py -m pytest tests/test_kline_daily_provider_route.py tests/test_minute_adjust.py tests/test_unified_adjustment_audit.py tests/test_watchlist_prepare.py -q --disable-warnings -p no:cacheprovider --basetemp ../dist/quantdash_upgrade_20261107/pytest_tmp
# 其它 166 项及完整收集清单见本地 final_backend_tests.log、backend_collection.log。
Set-Location 'E:\tick stock panel'
$env:PYTHONPATH='E:/tick stock panel/examples/quantdash-data-source'
foreach ($group in @('provider','mapping','client','external_loader')) {
  & $py -m unittest discover -s examples/quantdash-data-source/tests -p "test_$group.py"
}
$env:PYTHONPATH='E:/tick stock panel/backend'
& $py scripts/verify_unified_adjustment.py --base-url http://127.0.0.1:3018 --symbols 588170.SH 600519.SH 159516.SZ 000002.SZ 920116.BJ 510300.SH --output-dir dist/quantdash_upgrade_20261107/regression
Set-Location 'E:\tick stock panel\frontend'
pnpm build
pnpm exec vitest run src/components/StockDailyKChart.test.tsx src/components/StockMinuteSync.test.tsx src/components/StockPanel.quote-date.test.tsx src/components/stock-table/pricePrecision.test.tsx src/lib/klinePriceMeta.test.ts src/lib/intraday-scale.test.ts src/lib/quote-focus.test.ts src/lib/quote-freshness.test.ts src/lib/useQuoteSubscription.test.tsx --maxWorkers=2
```

真实复核使用 `POST /api/pipeline/adj-factor/run?retry_only=true` 并轮询任务；测试没有触发全市场分钟、整库重建或全量历史重下载。运行前后股票/ETF 四类 raw 和两类正式因子 Parquet 的文件数、大小、mtime 均未变化：新增/删除/变更均为 **0**。本轮核对脚本只读，源 forward 参考不回写。自选、策略、主题、现有数据路径与六项路由保留。

本轮隔离测试目录、pytest 临时目录和测试生成的 backend/MagicMock 已清理，原有报告、行情、环境及运行所需生产构建保留。本地完整证据仅在 `dist/quantdash_upgrade_20261107/`：validation_summary.json、regression/sample_verification.json、原生安装回执、定向审计与测试日志。商业源参考、日志、ZIP、缓存和环境未纳入公开提交。暂存内容逐次扫描真实已配置密钥值、凭据模式及禁止文件路径，命中 0。

**回退与盘中待验**

宿主回退：正常停止前后端，切回保留的 `feature/tsp-quantdash`，按该分支锁文件执行必要的 pnpm install --frozen-lockfile/build，再用已验证 nga 直接运行 uvicorn。旧分支的 dev.ps1 不识别新加的 PythonExecutable 参数，回退时使用直接启动命令：在 backend 目录执行 `D:\Python_Envs\envs\nga\python.exe -m uvicorn app.main:app --env-file "E:\tick stock panel\.env" --host 127.0.0.1 --port 3018`，并沿用原财务凭据环境注入。保留当前 data 与凭据，不能恢复整个旧 data 覆盖用户活动数据。插件独立回退：官方管理器选择上一版本 1.2.5，正常完整重启，再核对 active/pending；本轮没有实际执行生产回退。

北京时间 **2026-11-09 09:40–10:00** 待核验：股票/ETF/指数 quote_ts 与新鲜度、30 秒轮询下共享 12 RPM 的实际占用、缺代码按需补拉与额度不足返回、日线/分钟/均线当前锚点及前端连续刷新。周六结果不能替代该盘中并发时效验收；自动全量分钟和大池五档在完成此项前继续关闭。
