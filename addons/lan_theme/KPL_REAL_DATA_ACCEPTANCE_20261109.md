# lan.theme 真实数据接入与歧义修复验收 · 2026-11-09

**代码交付可用；真实题材业务未通过：BLOCKED_AUTHORIZED_KPL_DATA。** 正式题材/节点/关系/已解析/未解析均为 0，在线保持 ONLINE_KPL_NOT_VERIFIED。没有宣称 BUSINESS_READY_PASS 或全目录同步。

| 栏目 | 状态 | 实证范围 |
| --- | --- | --- |
| CODE | PASS | 固定身份、可追溯映射、包内 CLI、缓存与并发刷新 |
| TEST | PASS | 23 Python、15 Node；合成规模、失败/回退和信号保护 |
| PACKAGE | PASS | 官方 v0.3.32 预检/试载/安装、重启、启停恢复、版本回退；正式运行 1.0.2 |
| REAL_DATA | BLOCKED | 未提供获授权真实 KPL 文件或已许可正式接口 |
| ONLINE_COVERAGE | BLOCKED | 未发起 KPL 数据请求，无真实全目录完整性证据 |
| MA55_INTERSECTION | BLOCKED | 真实题材交集缺数据；交集算法、生产信号读取与合成链路已验证 |

## 起点、改动与版本

实际起点及重新 fetch 的个人远端 HEAD 均为 `eca53194772202000fe551108a360ac6d233c602`，分支 `feature/tsp-kpl-theme-filter-v1-20261109`。宿主仍为官方 v0.3.32 / `ca3a44252edb414e3f4c1df1b7c381bad9f55c8b`。

最终版本 **1.0.2**：先按任务安装 1.0.1；安装后的 CLI 检查发现省略 --data-dir 会向不可变版本目录写入默认 data。修复为数据操作必须显式传绝对 DATA_DIR，并实测退出 2、未生成包内 data。官方管理器拒绝同版本不同内容，故递增版本，不覆盖已安装文件。manifest、前端 metadata/页面、ZIP 和 runtime 一致。

所有生产源码改动仅在 addons/lan_theme：

- schema.py/normalize.py/importer.py：显式交易所优先；完整权威证券主数据或管理员映射需有版本、来源、日期；关系保存 symbol_resolution，快照保存授权说明及 identity_provenance。裸代码、歧义和无证据旧映射不因 MA55 日期而变更。
- backend/cli.py：安装包自带实际导入、list、rollback、管理员 init-views；原仓库 import_snapshot.py 调用同一实现。构建器包含 CLI，非仓库用户可直接运行。
- src/model.mjs/page.mjs：ProjectionModel 按快照、MA55 revision/日期缓存树、反向索引、分支集合、确认事件和规则/来源视图；筛选不重建全量数据；只提交最新并发刷新。页面显示授权阻塞及安装后操作指引。
- extension.py、manifest、构建配置、定向测试与 README：状态和计数、包版本、实际回归证据。

原三插件源码及宿主 backend/app、frontend/src、dev.ps1 差异为零；研究仓库未改。原未跟踪 REVIEW_SUMMARY.md 保留，未纳入提交。没有升级环境或依赖、增加行情采集器、1m 或 BUY 算法。

## 有界来源调查

仅检查工程文件名与 lan_theme 运行目录，没有扫描其它磁盘、私有账号或无关工程。未发现真实授权导出或已明确配置的 KPL 接入。用户补充了 KPL-post URL，随后直接检查当前仓库和两个集合：

| 来源及核查日期 | 结果 | 授权/覆盖判断 |
| --- | --- | --- |
| [KPL-post main](https://github.com/zensu357/KPL-post/tree/f44b609f92b2bf91da51aeebca6c50372911ddb4)，2026-11-09 | ls-remote 仍为 f44b609f92b2bf91da51aeebca6c50372911ddb4；最近 push 2026-07-22 UTC；66 条集合与“56个公共接口”集合分别 66/56 条请求，成功 response 数均 0 | README 声明学习用途、不代表官方授权；GitHub license=null。不是授权数据导出或成功联通证明 |
| [题材请求定义](https://github.com/zensu357/KPL-post/blob/f44b609f92b2bf91da51aeebca6c50372911ddb4/%E5%BC%80%E7%9B%98%E5%95%A6.postman_collection.json) | Theme InfoSearch/InfoGet 是 POST；66 集合列有 UserID/Token/DeviceID 参数名称；未使用值 | 未尝试 App 私有身份、签名、socket、他人凭据或猜测 ID |
| [开盘啦官网](https://www.kaipanla.com/about)，2026-11-09 | 公开产品信息，没有取得可供本项目使用的目录/详情授权 | 未将官网可浏览等同接口许可 |
| [猫爪题材目录](https://meoz.cn/docs/query/theme/lib-kp) / [详情](https://meoz.cn/docs/query/theme/lib-detail-kp)，文档 v1.0.52，2026-11-09 | 文档要求个人 API Key，最新快照、无历史版本；完整覆盖未确认 | 未配置用户获准使用的 Key/来源，本地缓存及转用权未核实；仅读文档，未调用数据接口 |

没有把公开文档的字段样例、同花顺概念或 DEMO 导入正式库；没有复制外部未许可实现。

## 定向测试和性能

| 命令 | 结果 |
| --- | --- |
| `& 'D:\Python_Envs\envs\nga\python.exe' -B -X utf8 -m pytest -q -p no:cacheprovider addons/lan_theme/tests_backend` | 23 passed，退出 0 |
| `node --test addons/lan_theme/tests/*.test.mjs` | 15 passed，退出 0 |
| `pnpm -C addons/lan_theme run build` | Vite 自包含 ES module，退出 0 |
| `...python.exe -B -X utf8 addons/lan_theme/build_backend.py` | lan-theme-1.0.2.zip，含 backend/cli.py，退出 0 |
| `... -m ruff check addons/lan_theme` / `ruff format --check` | 通过，退出 0 |
| `git diff --check` 和 staged diff 检查 | 通过，退出 0 |

新增验证：当前日只有 SZ 候选、历史日含 SH/SZ 同码时，保存的 unresolved 和无证据旧映射始终为 null；完整证券主数据仍有歧义则不解析；管理员映射保存来源/日期。覆盖 snapshot/day/revision 更新、失败原子保留、并发旧响应迟到、损坏/空数据、manifest 越界、同 ID 冲突、幂等、五版本、事务中断回滚、lease 冲突和 A/B 中途失败不切读槽。没有实际切断电脑电源，事务中断由异常触发验证。

合成规模明确为 **DEMO：300 大题材、1,500 节点、30,000 关系、6,000 事件/证券**。其中 R1 5,400、旧 V5 600；R1 LIVE/DELAYED/REPLAY 各 1,800。模型/DOM 测试循环筛选 300 个题材，分页与输入后额外 API 请求为 0；缓存树和事件对象保持相同引用。

一次本机测量：模型初载约 38 ms，过滤中位数约 0.73 ms、最大约 2.4 ms；JSDOM 初载约 122 ms、切换约 29 ms，进程 RSS 约 285 MiB。精确值保留在 node-final.txt 的 DEMO_SCALE_MEASUREMENTS。真实浏览器已打开同规模页面并切换题材/分支，40 行分页；自动化点击连同观察往返约 764 ms，这不是浏览器纯渲染计时。浏览器控制接口没有暴露 performance.memory，改记录承载大快照的隔离宿主 RSS **512.32 MiB**；同次生产宿主 RSS **1860.22 MiB**，是整宿主快照，不能当作题材插件增量。

未做跨交易日长时 soak，不将这些定向测量写成长时稳定性全部通过。

## 官方安装、只装 ZIP 和权限

使用已有 nga，在隔离 DATA_DIR、空环境文件、3339/3340 端口运行官方 v0.3.32；不复制生产凭据或安装第二个采集器。真实 inspect 返回兼容错误空数组，frontend 试载有 1.0.2，install 暂存后重启生效。

- **只装 ZIP**：从 data/extensions/lan.theme/versions/1.0.2 运行 python -m backend.cli，显式绝对 DATA_DIR；无需 addons 源码。执行标准 JSON v1 的合成 300 题材/30,000 关系导入，更新前后快照一致；重导 UNCHANGED。1.0.2 升级后保留同一 snapshot ID。
- 损坏 JSON 退出 2，逐文件错误 INVALID_JSON_OR_UTF8，页面 STALE 且继续展示 300/1500/30000 和 40 条原列表；CLI rollback 后 READY，原快照保留。
- 禁用并重启后 enabled=false、runtime_version=null，Page Pack 不在列表；启用重启后关系仍在。最终 1.0.2 在隔离环境按官方管理器 rollback 到 1.0.1，再 rollback 恢复 1.0.2，源 snapshot ID 未变。
- 带密码宿主：管理员安装后运行**包内** init-views，真实创建两张视图；初始化会话不作为服务凭据。后台仅配置 read:ext/write:ext Token，没有 api_session。Token 读行和 ingest=200，创建配置=403，自定义 status=403；匿名 status=401，管理员会话 status=200。最终版本发布正常，worker_alive=true。
- 正式环境真实运行 lan.theme **1.0.2**；原 lan.marketdata **1.0.2**、lan.ma55 **0.3.2**、lan.kline **2.0.1** 保持启用。正式题材 0，demo=false，授权说明为空；source_requests/market_requests/period_1m 均为 0。

没有通过匿名管理路由或浏览器自行 fetch 上传文件；包内 CLI 已履行后端结构、来源、身份和时间复核。原行情采集器按原有逻辑运行，不能把它自身的行情/元数据请求归为本题材插件新增采集。

## 原行情源的现场问题

操作前 before-runtime.json 已记录 lan.marketdata 为 ERROR，原因是“DuckDB initialization/metadata OOM; worker stopped, committed data retained”。重启后曾观测 LIVE；后续 source-final.json 记录 SOURCE_UNAVAILABLE / INVALID_5M_OHLCV，writer_alive=true、database_connected=true，已保存 K 线仍可读。原三包保持启用，不等于当前供应商行情质量全部通过。

这项源故障未解决，本轮按约束未改采集器、QuantDash、数据校验或 K 线。它影响新交易日行情及 MA55 数据可用性，不能用当前日空结果证明“当日无 BUY”；已发布 2026-11-06 的历史确认单独核验。也不据一次重启后恢复认定原 OOM 已长期修复。

## MA55 原事实与工作台

操作前在线只读一致性备份原 MA55 SQLite；操作后 mode=ro 逐条对比 signal_event **全部列及原文 payload**：before=36,929，after=36,929，changed=0、removed=0、added=0。快照证据记录时成立；后续正常市场扫描产生的确认由原插件管理，不是题材写入。运行时代码未读原 MA55 私有库，私有库只读比对仅用于此次验收。

切换到 2026-11-06 历史投影，R1 为 442 事件 / 428 股票、WATCH 3,035；旧 V5 单独显示，300863.SZ 保留三条既有确认。操作前已发布投影 4,849 个唯一事件逐条核对 event_id、confirmed_at、first_seen_at、rule_version 与 SQL 一致。进入新交易日后当前日视图可为空，不把历史事件当当日新增。

实际点击原 300863.SZ 15m 深链，参数仍为 confirmed/setup=2026-11-06 15:00:00、defense=32.25、factor=1；打开前库存已有连续 256 根 15m，不重抓历史。工作台锚点、均线和原始结束时间以现场 DOM/截图记录，不将事件时间替换成当前时间。

## 阻塞输入、使用与回退

需要用户提供可验证许可和日期的真实 KPL **目录及 InfoGet 详情**，或 JSON/JSONL/CSV v1 授权导出；裸股票代码还需显式管理员映射或完整权威证券身份表。也可提供已明确开通的正式 API 文档和私有配置位置，不把密钥放入聊天或仓库。仅提供 KPL-post 链接尚不满足这些条件。

完整可复制的包内 CLI 命令见 [README](README.md#只安装-zip-也能导入)。无授权文件时保持空源即可，不需要运行第三方风险脚本。停用本功能用官方管理器 disable + 重启；即时上一版本为 1.0.1，可通过官方 rollback；1.0.0 安装文件/状态及题材库已有本机备份。题材回退用包内 list/rollback，不删除、重建或覆盖原三插件库。

宿主既有退出期 PyEval_SaveThread/GIL 错误仍有记录，首次未加载本题材包的前轮隔离运行已复现；本轮未改宿主或依赖。没有把退出后端口释放描述为所有 Python 退出码均正常。

## 本机证据

所有实际回执、截图、性能、密码测试私有配置与一致性备份仅存 `%TEMP%\tsp-kpl-real-20261109\`，不提交：

- before-runtime.json、ma55-before.sqlite3、theme-before.sqlite3、theme-package-before/、lan-theme-1.0.0.zip：操作前备份。
- isolated-install.json、production-install.json、auth-install.json、installed-cli-*.json：官方安装及包内 CLI。
- auth-prepare.json、auth-final-cli-init.json、auth-probe.json：脱敏权限结果；凭据单独保留，不写报告。
- isolated-disabled.json、package-rollback-observed.json、isolated-status.json：停用、回退、恢复。
- pytest-final.txt、node-final.txt、ruff-final.txt、host-rss.json、scale-browser-timing.json：定向测试及测量。
- scale-DEMO.png、scale-STALE.png、300863-deeplink*.png/txt、production-theme*.png/txt：实际画面，DEMO 与正式空源分开。
- facts-historical-check.json、facts-production.json：只读信号比对；github-delivery.json：推送后的远端提交/父提交/文件级差异。

隔离宿主与 DEMO 标签页在验收后停止，生产保留官方入口 /lan-theme。源码仅推送个人 Fork 的原开发分支，不覆盖 main，不推送官方上游。
