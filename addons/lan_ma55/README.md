# 兰 MA55 V0.2 · QuantDash Pro 后台

`lan.ma55` 0.2.8 是面向官方 TSP v0.3.32 的前后端一体化外置扩展。TSP 后端启动后采集 QuantDash 原始完成5m，聚合15m，执行冻结 `MA55_15M_MINIMAL_V5`，写入自身 SQLite，再经官方扩展数据 API 发布。页面 `/lan-ma55` 只查看结果，关闭网页仍继续计算。

周末默认展示后台最近真实交易日，支持此前日期、BUY、观察、数据状态、全市场搜索分页和官方个股详情。实际覆盖与剩余限制见 [本轮验收](QUANTDASH_PRO_ACCEPTANCE_20261108.md)。此前的 `RUNTIME_ROOT_CAUSE.md` 和 `FULL_MARKET_STATUS_20261107.md` 保留为旧版本记录。

## 构建与安装

使用已有 nga 解释器和 Node 工具，不升级依赖、不构建宿主。在仓库根目录执行：

```powershell
$ma55Python = 'D:\Python_Envs\envs\nga\python.exe'
pnpm --dir addons/lan_ma55 build
& $ma55Python -B -X utf8 addons/lan_ma55/build_backend.py
```

产物 `addons/lan_ma55/dist/lan-ma55-0.2.8.zip` 仅含清单、后端源码和自包含页面。Vite 会清空插件 dist，先构建页面再打 ZIP，产物不提交。

TSP **设置 → 导入与备份** 安装 ZIP；先停用并备份旧独立 `lan_ma55` Page Pack，避免重复路由。重启 TSP 后端并刷新页面，侧栏进入 **MA55 结构观察**。前后端 ID 均为 `lan.ma55`，统一更新、启停和回退。

后端复用已有 `QUANTDASH_TOKEN` 环境变量。启用接口认证时，以私有环境变量 `MA55_TSP_EXT_TOKEN` 提供官方 ingest 所需的 `write:ext` Token；创建展示表另需管理权限。密钥不进入页面或源码。本机未配置接口认证，使用回环地址。

安装时通过官方管理接口显式准备三个插件专属展示配置：

```powershell
& $ma55Python -B -X utf8 -c "import os; from addons.lan_ma55.backend.result_publisher import Publisher; p=Publisher('http://127.0.0.1:3018', os.getenv('MA55_TSP_EXT_TOKEN')); p.prepare(); p.close()"
```

`prepare()` 只创建自己的命名空间，已有不同 schema 会报错。生产 worker 不修改管理配置。本机三表已经创建并验证读回，重启无需重复准备。

## 数据与运行口径

- 股票候选来自 QuantDash 股票池与公开 `context.repository.get_name_map(None)`。QuantDash 元信息确认 CN / stock / SH、SZ、BJ；源遗漏类型可用官方 instruments search 确认并记录来源。快照与股票分母分开显示。无报价、缺 tick 的股票仍在分母；上市状态证据缺失时明确显示，不猜停牌或退市。
- 固定 `period='5m', adjust='none'`，每批最多100只、每股最多5000根；冷启动仅预热最近10个真实交易日。采用北京时间、源指数日K日历及60秒完成余量。09:45为首根15m；每三个真实有效5m才形成15m，缺K不合成。
- 沿用冻结 MA55 的滚动预算和429机制，MA55 分钟24次/滚动60秒。现有 QuantDash v1.2.6 给实时保留6、非实时最多6次，因此合计分钟预算不超过 Pro 30。各能力分别计数，标准 HTTP hooks 包含失败请求；结构化429、网络错误、配额及冷却检查点可恢复。
- 本机通过官方设置 API 关闭争用分钟额度的全市场1m自动修复；正常快照、日K、按需分钟保留。最新完成5m优先，随后历史补齐，最后渐进修复旧缺口。
- 外置后端没有正式宿主因子读取契约，按本轮授权采用 QuantDash 真实事件比率：`qfq = raw / product(晚于K线日期的ex_factor)`，tick同口径调整。缺失/无效因子为 DATA_PENDING，不能用1冒充。量保持源单位，量比无量纲。不增加 ETF 因子审计。
- `context.data_dir/user_data/lan_ma55/state.sqlite3` 是唯一权威库。一个工作线程持有进程租约；同一事务提交必要15m、5m合成尾巴、缺点、重试时间、水位、确认和轮次。已落盘完成K不重复请求，缺股单独修复，未返回不等于停牌或0 BUY。
- 原始确认 ID/载荷不变，后续风险独立保存。周末为 REPLAY，源迟到为 DELAYED；历史不会改成当前自然日 LIVE。旧浏览器算法/IndexedDB仅保留为回退源码和测试，不进入新生产包。
- 必要原始15m保留10个交易日；派生历史默认60个交易日，可配置20–252。只裁剪插件派生结果，不改 TSP 行情或用户资料。

私有 `user_data/lan_ma55/settings.json` 可设置 `retention_sessions`、`api_base`，以及必要的 `api_token` / `quantdash_token`。启动只读设置，不写回凭据。

## 官方展示通道

| 配置 | 内容 |
| --- | --- |
| `lan_ma55_summary` | 固定 scope snapshot，实际日期、进度、分母、分类、HTTP计数与错误 |
| `lan_ma55_day` | 每股 snapshot，最新交易日全体股票、原始确认列表、状态与原因 |
| `lan_ma55_history` | 固定 scope snapshot，保留窗口内各日概况及 BUY / WATCH / 异常行 |

官方 v0.3.32 没有删除单个 timeseries 日期分区的公开 API，因此使用有界 snapshot 投影。历史正常无结构行按需省略，概览保留完整统计。每股展示缓存按日期与股票池 generation 过滤；扩展数据不存原始行情。

后端只用官方 HTTP ingest，前端只用 `ctx.api.extDataRows`。页面不私拼 fetch、不读 SQLite/Parquet、不请求自选或行情、不生成 BUY。自有 `/api/extensions/lan.ma55/{status,events,symbols,cycles}` 只用于运维，页面不依赖它们。

## 实现入口与验证

`backend/extension.py` 注册生命周期；`runtime.py` 调度；`collector.py` / `request_budget.py` 负责 QuantDash 与 HTTP 预算；`market.py` 处理交易时间、聚合与比例；`live_store.py` 负责原子状态；既有 `engine.py` / `pipeline.py` 是唯一生产算法；`result_publisher.py` 发布。`src/results.mjs` 为新只读页面，`src/observer.mjs` 留存旧观察器。

```powershell
& $ma55Python -B -X utf8 -m pytest addons/lan_ma55/tests_backend/test_engine.py addons/lan_ma55/tests_backend/test_extension.py addons/lan_ma55/tests_backend/test_pro_runtime.py addons/lan_ma55/tests_backend/test_pro_collector.py addons/lan_ma55/tests_backend/test_store_service.py -k 'not 5000' -q -p no:cacheprovider
& $ma55Python -B -X utf8 -m ruff check addons/lan_ma55/backend addons/lan_ma55/tests_backend addons/lan_ma55/build_backend.py
node --test addons/lan_ma55/tests/logic.test.mjs addons/lan_ma55/tests/results.test.mjs addons/lan_ma55/tests/page.test.mjs addons/lan_ma55/tests/supply.test.mjs
```

原 Python 与私有15正/7抑制/2负冻结样例对照需 `MA55_PYTHON`、`MA55_REFERENCE_REPO`、`MA55_FROZEN_PACK`，只读固定生产提交 `23477622536d1206b2af3c164b4c905aa2e15e8d`。私有行情不打包或上传；未挂载时相应对照明确 skip。

## 回退

**设置 → 导入与备份 → 已安装扩展** 选择 `lan.ma55` 回退并重启；管理器保留旧代码，不删除 SQLite。本轮已真实验证跨版本回退、再升级及事件/水位对账。停用同样需要重启。如回到旧浏览器版，再启用独立 Page Pack 并刷新，保持一个生产者。

本机原偏好备份在忽略的 `dist/ma55-quantdash-pro-20261108/backup/preferences.json`。停用专用后台后，可经官方 `PUT /api/settings/preferences/realtime-monitor` 恢复 `minute_refresh_enabled=true` 及原120秒间隔。SQLite 备份同处，后端停止时拷贝，保留原库用于恢复。
