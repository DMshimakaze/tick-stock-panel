# lan.theme 1.0.0 验收记录 · 2026-11-09

结论：`PLUGIN_STRUCTURE_PASS`、`OFFLINE_IMPORT_PASS`、`MA55_INTERSECTION_PASS`。

**生产来源状态：`AUTHORIZED_SNAPSHOT_REQUIRED` / `ONLINE_KPL_NOT_VERIFIED`。真实授权题材、节点、股票关系均为 0。** 本机没有可核实的合法 KPL 授权会话或真实导出，未发起 KPL 在线请求，未声明全目录同步成功。隔离演示数据显著标注 DEMO，未进入正式目录。

## 代码与版本

- 工作目录：`E:\tick stock panel`，解释器 `D:\Python_Envs\envs\nga\python.exe`，未创建环境或升级依赖。
- 开发分支：`feature/tsp-kpl-theme-filter-v1-20261109`，仅交付 `DMshimakaze/tick-stock-panel`。
- 起点及交付前再次核实的远端基线：`5608b55a4760d949403b475638120a26c34f8745`。
- 官方宿主固定 `v0.3.32 / ca3a44252edb414e3f4c1df1b7c381bad9f55c8b`，manifest `>=0.3.32,<0.3.33`。未升级 v0.3.34。
- MA55 冻结研究版本 `5d1d0f6e8913f528110c93e054429f050b9851f6`；未进入或修改研究仓库。新扩展没有跨插件 Python import，也没有打开 MA55 私有库的运行时代码。
- 对官方基线执行 `git diff --name-only ... -- backend/app frontend/src dev.ps1`，输出为空。原三个 addon 对本轮基线的 diff 为空，代码对象保持原样。
- 原有未跟踪 `REVIEW_SUMMARY.md` 保留、未加入提交；本地 AGENTS.override.md 和忽略规则未改动。

相对基线仅新增 `addons/lan_theme/` 的 25 个源文件：

| 文件 | 实现职责 |
| --- | --- |
| backend/__init__.py、schema.py、normalize.py | 独立类型、时间和股票身份校验，真实层级与来源属性保留 |
| backend/store.py、importer.py | 本插件快照事务、单写入 lease、导入、异常保留、五版本回退 |
| backend/publisher.py、extension.py | 官方 Ext Data 双槽发布、官方生命周期和只读状态 |
| import_snapshot.py、build_backend.py、extension.yaml | 实际 CLI、九文件 ZIP、官方扩展清单 |
| src/extension.mjs、model.mjs、page.mjs、view.mjs | SDK 读结果、交集、三段式界面和主题布局 |
| tests_backend/conftest.py、test_import.py、test_publisher.py | 合成结构、导入、事务与发布切换测试 |
| tests/fixtures.mjs、model.test.mjs、page.test.mjs | 事件语义、去重、日期、深链和 DOM 测试 |
| package.json、pnpm-lock.yaml、vite.config.mjs | 独立构建；复用已缓存的 Vite/JSDOM 固定版本 |
| README.md、本记录 | 操作说明、来源边界和实际验收 |

不提交用户任务文档、商业响应、实际行情/数据库、凭据、安装目录、node_modules、dist、ZIP、截图或临时日志。提交 SHA 由 Git 提交及远端回执记录，不在提交内容内自引用。

## 定向验证

| 实际命令/验证 | 结果 |
| --- | --- |
| `& 'D:\Python_Envs\envs\nga\python.exe' -B -X utf8 -m pytest -q -p no:cacheprovider addons/lan_theme/tests_backend` | 18 passed，退出 0 |
| `node --test addons/lan_theme/tests/*.test.mjs` | 11 passed，退出 0 |
| `pnpm -C addons/lan_theme run build`（在插件目录执行等价 `pnpm run build`） | Vite 5.4.21，自包含 ES module，退出 0 |
| `& 'D:\Python_Envs\envs\nga\python.exe' -B -X utf8 addons/lan_theme/build_backend.py` | lan-theme-1.0.0.zip，退出 0 |
| `... -m ruff check addons/lan_theme` / `ruff format --check` | 通过，退出 0 |
| `git diff --check` 及最终 staged diff 检查 | 通过，退出 0 |
| CLI 回退不再保留的 ID | 明确返回 SNAPSHOT_NOT_RETAINED，退出 2，合法快照不变 |

测试包括 ClassLayer 2/3、Level1 直接股、Level2、顶层 Stocks/StockList/Tag 精确关联、未知/歧义分支、同名不同 ID、同股多分支、六位代码歧义、JSON/JSONL/CSV v1、坏字段/时间/空响应/截断、空 manifest、缺 ID、文件数/题材数核对、整批/单题材结构骤降、幂等、五版本保留、lease 冲突、重开数据库和 rollback。

页面测试核对 R1/V5 分离、LIVE/DELAYED/REPLAY 事件与股票数、WATCH 不计 BUY、全局股票去重、子路径包含、无标签、历史分类提示、history 缓存、读取超过 20,000 明确拒绝、发布缺片拒绝、刷新失败保留旧结果、工作台正常/禁用和深链原始参数。SDK 测试确认切换题材/路径/反查不新增 API 请求。

## 官方真实安装与现场页面

隔离环境采用同一官方 v0.3.32 宿主源代码和已有官方 frontend build；独立 DATA_DIR、空环境配置、端口 3338，不复制生产凭据、不安装第二个行情采集器。通过官方 API 创建/写入 MA55 公开投影副本：当前日 5,891 行，另保留一个历史日用于日期切换；不是另一个运行中的 MA55 算法。生产数据另外做了只读一致性备份。

最终 ZIP 经真实 `/api/import-center/inspect` 返回兼容错误空数组，官方 frontend 试载返回 24,336 字节。随后 `/api/extension-packages/install` 保存，重启后 runtime_version=1.0.0，独立 `/lan-theme` 页面正常加载。

实际验证结果：

- 空源：明确提示待导入授权快照，不显示假题材，BUY 交集为空。
- DEMO：2 大题材、2 节点、5 股票关系、2 唯一股票；真实 MA55 公开信号与**合成归属**交集为 2 股票，页面有 DEMO 标记。声明 manifest 的 2 个 ID 全齐，仅为 MANIFEST_COMPLETE，非 KPL 全量证明。
- 同日两次相同输入返回同一 snapshot ID、UNCHANGED；更新 brief 产生新快照；后续结构骤降退出 2，状态 STALE，上一合法快照仍可读。
- 实际 CLI rollback 恢复第一份 snapshot；官方禁用 → 重启后 enabled=false、runtime_version=null、Page Pack 不在列表；官方启用 → 重启后 READY、worker_alive=true、原 snapshot ID 和关系仍在。
- 实际页面展开「供应商服务商 / 整机」，保留 Level1/Level2 两层；相同股票的多题材、多路径并存。历史归属提示清晰，未把 WATCH 变为 BUY。
- 选择 2026-11-05 实际加载历史：86 R1 事件、80 去重股票、4,588 WATCH，错误栏为空；当前快照覆盖命中 0/80，未补造分类。
- 手机宽度 390 px 时 body.scrollWidth=390、题材布局宽度 360，未出现页面级横向溢出；恢复默认尺寸。
- 隔离宿主最终停止，端口 3338 已释放，DEMO 文件仅保留在本机临时证据目录。

正式环境也经同一官方预检/安装并重启，实际 runtime 版本如下：

| 包 | 正式运行版本 |
| --- | --- |
| lan.theme | 1.0.0 |
| lan.marketdata | 1.0.2 |
| lan.ma55 | 0.3.2 |
| lan.kline | 2.0.1 |

本机 `http://127.0.0.1:3011/lan-theme` 已实际打开并保留。正式题材数量为 0/0/0、demo=false、online_enabled=false，题材插件 source_requests/market_requests/period_1m 均为 0。页面当前日 2026-11-06：保存总事件 36,929，R1 BUY 442 事件 / 428 股票，全为 REPLAY；WATCH 3,035。切换旧 V5 单独显示 4,407 事件 / 3,541 股票。

原公共源观察为 WARM / QUOTA_WAIT，writer_alive=true、数据库已连接、错误为空；8GB 配置解析为 7.4 GiB、2 线程，分钟预算 24、宿主预留 6 未改。采集器原本的有界 5m 预热继续运行，不能将其后台请求计为本题材插件新增采集。没有新增 1m 请求路径。

## 原确认和 K 线回归

操作前在线只读备份原 MA55 SQLite，操作后用 mode=ro 打开原库及备份逐条比对 signal_event 的**全部列和 payload 原文**：

- before=36,929，after=36,929，added=0，removed=0，changed=0。
- 当前发布视图中的 4,849 个唯一事件逐个核对 event_id、confirmed_at、first_seen_at、rule_version，与原 SQL 一致。
- 题材运行时不读取这个私有库；上面的读取只用于用户要求的操作验收。

正式页面切到旧 V5、全部信号并搜索 300863.SZ，显示该日三条既有确认。实际点击首条打开 `/lan-kline?symbol=300863.SZ&period=15m&event=2026-11-06T15%3A00%3A00&setup=2026-11-06T15%3A00%3A00&defense=32.25&factor=1`。工作台显示卡倍亿、原确认和支撑测试均为 2026-11-06 15:00:00、防守 32.250，定位按钮有效，256 根 15m 连续数据；对应收盘 32.51，MA5=32.416、MA55=32.298、MA233=31.943。打开前 inventory 已有完整保存数据，未为此重抓历史。

## 未验与已知问题

在线 KPL 授权、成功实时响应、商业全目录完整性和交易时段实时表现未验证；正式筛选需要用户合法快照。未进行全市场重拉、全面因子审计、全仓库 pytest 或无关前端构建。

官方宿主在应用报告 shutdown complete 后曾出现 `PyEval_SaveThread / GIL` 终结错误；**首次尚未加载 lan.theme 的隔离进程已复现**。端口释放、重启、包启停、快照恢复及原确认比对均通过。该宿主退出问题根因未定位，本轮按核心冻结约束记录，不改宿主或依赖；不能将此过程描述为所有宿主退出均正常退出码 0。

## 证据与回退

实际回执及日志仅保存在 `%TEMP%\tsp-kpl-theme-20261109\`：

- isolated-install.json、production-install.json：官方预检、试载和安装回执。
- isolated-import-receipts.json、isolated-disabled-observed.json、isolated-reenabled-observed.json：幂等、异常拒绝、禁用和恢复事实。
- pytest-final.txt、node-final.txt、ruff-final.txt：实际执行输出。
- facts-after-isolated.json、facts-after-production.json、facts-final.json：SQL 和公开事件只读比对。
- production-theme-status.json、production-theme-dom.txt、production-theme-empty.png：正式空源状态与页面。
- isolated-empty.png、isolated-demo-hierarchy.png、300863-deeplink-dom.txt、300863-deeplink.png：现场空源、DEMO 层级和 K 线锚点。
- ma55-before.sqlite3：私有只读一致性备份；不会上传。

停用本功能：官方扩展管理器禁用 lan.theme 并重启，不改原三插件；只回退题材：CLI list 后 rollback 保留的 snapshot ID。生产目前没有题材快照，保留空源即可。源码可切回保留的基线分支，不 reset/clean，不删除 data。
