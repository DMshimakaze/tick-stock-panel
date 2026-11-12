# 兰 · 题材与MA55 1.0.3

TSP v0.3.36 独立扩展：合法快照 → 大题材 → Level1/Level2 路径 → 已发布 MA55 BUY/WATCH → 原15m工作台。实现包内 CLI 导入、原子更新、五版本回退、严格股票身份和缓存交集；默认规则为 MA55_15M_MINIMAL_V5_R1，旧 V5 单独切换。

**真实业务状态：BLOCKED_AUTHORIZED_KPL_DATA。** 当前没有合法真实 KPL 快照，正式题材/节点/关系为 0。在线保持 ONLINE_KPL_NOT_VERIFIED。DEMO 和公开接口文档不构成真实数据接入证明。具体已验/未验见 [本轮验收](KPL_REAL_DATA_ACCEPTANCE_20261109.md)。

## 构建和安装

在工程根目录执行，使用已有 nga 环境，不升级宿主或依赖：

```powershell
pnpm -C addons/lan_theme install --frozen-lockfile
pnpm -C addons/lan_theme run build
& 'D:\Python_Envs\envs\nga\python.exe' -B -X utf8 addons/lan_theme/build_backend.py
```

官方导入中心预检 addons/lan_theme/dist/lan-theme-1.0.3.zip，确认本地代码来源并安装，重启后访问 /lan-theme。兼容范围 >=0.3.32,<0.3.37。ZIP 包含 manifest、独立 backend（含 cli.py）和自包含页面，不含用户数据、凭据、DEMO 或第三方响应。

v0.3.32 Page SDK 的表读写针对官方扩展表，没有本插件原始文件的预检/应用方法，也没有通用自定义路由调用入口。直接 fetch 会绕过 SDK；用原文暂存表转发文件需要增加任务队列协议。本版采用安装包自带 CLI：后端校验所有输入，再原子应用；页面提供操作指引，不增加匿名 HTTP 写入口。

## 只安装 ZIP 也能导入

下面是操作模板，文件路径、来源、时间和授权说明须替换成实际材料。进入已安装版本目录，运行其中的 backend.cli，不依赖开发仓库：

```powershell
Push-Location 'E:\tick stock panel\data\extensions\lan.theme\versions\1.0.2'
& 'D:\Python_Envs\envs\nga\python.exe' -B -X utf8 -m backend.cli --data-dir 'E:\tick stock panel\data' import --input 'C:\AuthorizedKPL\details' --manifest 'C:\AuthorizedKPL\manifest.json' --asof '2026-11-09 15:00:00' --source '实际授权导出来源' --provenance '实际导出日期、授权依据及允许本地使用的范围' --identity-map 'C:\AuthorizedKPL\identity-map.json'
Pop-Location
```

安装目录按实际 DATA_DIR 和版本调整。源码用户仍可运行 python -m addons.lan_theme.import_snapshot，调用同一份实现。--asof 为真实快照时间，无时区按北京时间；带时区自动转换。--source 和 --provenance 必填并持久保存；说明由操作者提供，填入文字不会自动取得 KPL 许可。成功退出 0，拒绝退出 2；stdout 仅包含结果、覆盖与身份计数，不输出商业原文。拒绝时给出具体原因和修正动作，保留原合法快照。

支持 UTF-8（可带 BOM）：单个原始 Theme/InfoGet JSON、多 JSON 目录、显式 manifest、JSON/JSONL/CSV v1。InfoGet 校验 errcode=0、ID/Name、Table[].Level1、同一 Table 行下的 Level2[]、节点 Stocks 和顶层 StockList/Stocks/Tag。ClassLayer 2/3 均按实际层级处理。SourceAdapter.documents() 是获授权来源的输入边界；没有 KPL 在线客户端、ID 穷举或新行情采集器。

Manifest 协议示例（仅说明格式）：

```json
{"format":"lan-theme-manifest","format_version":1,"files":["theme-a.json","theme-b.json"],"expected_theme_ids":["来源ID-A","来源ID-B"],"scope":"PARTIAL"}
```

路径相对 manifest。存在 manifest 时仅导入列出的文件；否则目录读取 JSON/JSONL/CSV，应将 manifest 和映射文件放在目录外。输出 expected/fetched/valid/missing/rejected：fetched 是文件数，valid 是唯一题材数。失败、缺项、重复/额外 ID 均拒绝整批覆盖。MANIFEST_COMPLETE 仅表示声明的 ID 已齐，不能证明 KPL 官方全目录；缺少权威目录时覆盖 UNKNOWN。

## 股票身份不会随日期变化

解析优先级：来源显式交易所代码，然后是导入时的可追溯管理员映射或完整权威证券主数据。名称、数字前缀、BUY/WATCH 名单和所选交易日都不参与解析。保存的 UNRESOLVED_SYMBOL 始终为 null，不进入交集。1.0.0 旧快照中没有映射证据的裸代码也需显式重导；旧 --symbols-file 数组可读取，但不再作为唯一身份依据。

--identity-map 的管理员映射协议示例（需逐项核实后填写）：

```json
{"format":"lan-theme-identity-map","format_version":1,"kind":"ADMIN_EXPLICIT_MAP","source":"管理员核对原始授权导出的依据","asof_cn":"2026-11-09 15:00:00","mapping":{"300863":"300863.SZ"}}
```

完整证券主数据使用同一 format/version，kind=AUTHORITATIVE_UNIVERSE、complete=true、symbols 为完整 canonical 身份数组，source/asof_cn 真实。若同组六位代码含 SH/SZ 两个身份，仍保持未解析。每条关系保存 symbol_resolution，快照保存 identity_provenance；修正须重导，日期切换不改变事实。

## 版本化标准导出

JSON 示例是协议，不是生产分类：

```json
{"format":"lan-theme-export","format_version":1,"asof_cn":"2026-11-09 15:00:00","rows":[{"theme_id":"来源ID","theme_name":"题材名称","branch_path":["一级细分","二级细分"],"branch_ids":["L1-ID","L2-ID"],"stock_code":"300863.SZ"}]}
```

JSONL 首行为不含 rows 的同一 header，后续每行一条 row。CSV 必须含 theme_id/theme_name/branch_path/stock_code，另传 --export-version 1；branch_path 为 JSON 数组或“一级 / 二级”，空表示细分未确认。可选 branch_ids（JSON 数组）、prod_name/IsZz/IsHot/Reason。标准导出记 AUTHORIZED_EXPORT，不伪称原始 InfoGet。

## 保留、错误和回退

本插件只在 DATA_DIR/user_data/lan_theme/themes.sqlite3 存题材元数据，无 K 线。导入持有文件 lease，以事务切换 active；失败保留旧快照并显示 STALE，无合法快照时 SOURCE_UNAVAILABLE。时间倒退须显式 rollback；同题材层级下降、节点/关系或整批数量下降超过一半时拒绝覆盖。真实分类变化触发规则应核对来源，不强行写入截断数据。

在已安装版本目录执行：

```powershell
& 'D:\Python_Envs\envs\nga\python.exe' -B -X utf8 -m backend.cli --data-dir 'E:\tick stock panel\data' list
& 'D:\Python_Envs\envs\nga\python.exe' -B -X utf8 -m backend.cli --data-dir 'E:\tick stock panel\data' rollback 'snapshot-列表中的ID'
```

保留最近五份合法快照。输入及规范化快照各上限 32 MiB、2,000 题材；错误只保存最近一次。source_hash 包含来源/授权说明、输入、as-of、manifest、规范化版本和映射，用于跳过重复规范化与发布；重复输入返回 UNCHANGED。顶层 Tag.ID 仅唯一精确匹配才附分支，否则保留“细分未确认”。同股多题材/分支保留，全局股票按 symbol 去重，不累加分支人数。

## 发布、权限和缓存

后台仅经官方 Ext Data 配置/ingest 发布 lan_theme_tree/status。大题材按 512 KiB 分片，单关系超过 256 KiB 拒绝发布。A/B 两槽每槽最多 4,096 行，总共 8,192 行，低于 SDK 20,000 上限；所有分片写完才切换状态，旧位置复用。

页面仅调用 ctx.api.extDataRows/extPagesList，MA55 summary/day/history 为信号唯一来源，当前日检查 generation。ProjectionModel 按 snapshot_id、MA55 revision/日期及数据更新失效；树、反向索引、分支集合和确认事件解析一次，最多缓存两规则×四来源×两页签的视图。题材/路径/搜索/分页不重复解析全量关系或 fetch。并发刷新仅最新请求提交，坏结果保留原视图。

事件 ID、confirmed_at、first_seen_at、setup_at、defense、adjust_factor、rule_version 保持原值；LIVE/DELAYED/REPLAY 分列统计，WATCH 独立。分类快照晚于信号日提示“按后来的题材归属回看，不代表当时标签”。本快照缺标签不能证明股票不属于任何题材。lan.kline 启用时深链保留原15m参数；未启用时保留官方个股详情。滚动历史不足由工作台明确显示，题材插件不补采。

启用密码的宿主，先在安装版本目录执行一次管理员初始化；命令交互读取合法访问密码，不保存密码/会话：

```powershell
& 'D:\Python_Envs\envs\nga\python.exe' -B -X utf8 -m backend.cli init-views --api-base 'http://127.0.0.1:3018'
```

后台私有 settings.json 配置 api_base 和仅有 read:ext/write:ext 的 api_token，重启生效。Token 可读写已创建表，不能创建配置或访问自定义 /api/extensions/lan.theme 路由。api_session 仅为已有配置兼容项，30天会话不能视作永久服务凭据。认证/权限错误显示 PUBLICATION_UNAVAILABLE，保留本地快照。私有文件不提交，凭据不发送页面。

DEMO 仅用于独立 DATA_DIR：私有 allow_demo=true，并显式 --demo；UI 标记 DEMO，业务仍 BLOCKED。正式目录默认禁止，设置改变须重启。停用本功能在官方管理器禁用 lan.theme 并重启，不影响原三插件和数据；扩展版本回退用管理器 rollback，题材回退用上面的 snapshot CLI。

## 验证和结构来源

```powershell
& 'D:\Python_Envs\envs\nga\python.exe' -B -X utf8 -m pytest -q -p no:cacheprovider addons/lan_theme/tests_backend
node --test addons/lan_theme/tests/*.test.mjs
& 'D:\Python_Envs\envs\nga\python.exe' -B -X utf8 -m ruff check addons/lan_theme
git diff --check
```

- [KPL-post 当前固定提交](https://github.com/zensu357/KPL-post/tree/f44b609f92b2bf91da51aeebca6c50372911ddb4)：本轮复查 main 未推进；66/56 两集合成功响应均为空。README 声明学习用途且不代表官方授权。未使用任何他人凭据或复制未许可代码。
- [Trade InfoGet 结构示例](https://github.com/xiyuxifeng/Trade/blob/0553f59c5902a53b37f39146b6e2726f4e3e94d3/trade-strategy-ai/docs/bak/kaipan.md)：Table、Level1/Level2、StockList/Tag、ClassLayer=3。
- [dsh-plugin-stock 固定源码](https://github.com/yllyx/dsh-plugin-stock/blob/b3723662d11337b6b6263cd647709d5847fbb67a/plugin/backend/kpl.py)：详情解析与全目录来源分开。
- [daily_trading_review 固定源码](https://github.com/yanbixing8818/daily_trading_review/blob/e385dacfb14f7e5f53d2abe08d10d8b6240b3674/demo2/stockTool/kaipanla/kaipanla_ticai.py)：仅研究字段，不采用 ID 探索或 ClassLayer=2 限制。
- [猫爪题材目录文档](https://meoz.cn/docs/query/theme/lib-kp)、[详情文档](https://meoz.cn/docs/query/theme/lib-detail-kp)：公开文档要求个人 API Key，完整性未确认，只保留最新快照；本机未配置已获授权的此来源，未调用数据接口。

交付仍需要真实授权 InfoGet 目录及详情、带来源/日期/许可的标准导出，或用户明确取得权限的正式 API。不会用文档示例、同花顺替代分类或 DEMO 冒充业务完成。
