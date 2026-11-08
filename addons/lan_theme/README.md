# 兰 · 题材与MA55 1.0.0

TSP **v0.3.32** 独立扩展包：导入有授权来源的 KPL 分层快照，在 `/lan-theme` 查看大题材 → Level1/Level2 路径 → 已发布 MA55 BUY/WATCH 的交集。默认规则为 `MA55_15M_MINIMAL_V5_R1`，旧 V5 单独切换。

第一版已实现离线导入、更新、拒绝异常快照、五版本保留、回退和公开视图筛选。**在线 KPL 未接入、未验证：`ONLINE_KPL_NOT_VERIFIED`。正式安装初始状态为 `AUTHORIZED_SNAPSHOT_REQUIRED`，题材/节点/关系均为 0。** 本项目没有 KPL 在线客户端，不请求商业接口；`SourceAdapter.documents()` 是后续获授权来源的输入边界。

## 构建与官方安装

在 `E:\tick stock panel` 执行，Python 固定使用已有 `nga`：

```powershell
pnpm -C addons/lan_theme install --frozen-lockfile
pnpm -C addons/lan_theme run build
& 'D:\Python_Envs\envs\nga\python.exe' -B -X utf8 addons/lan_theme/build_backend.py
```

在官方导入中心预检 `addons/lan_theme/dist/lan-theme-1.0.0.zip`、确认本地代码来源并安装，重启 TSP 后访问 `/lan-theme`。宿主兼容范围为 `>=0.3.32,<0.3.33`，不升级宿主。ZIP 仅包含 manifest、独立 backend 和自包含页面；不含导入原文件、测试 fixture、凭据或数据目录。CLI 从本仓库运行。

需要已安装 `lan.ma55` 才有信号可读。`lan.kline` 启用时股票名称直接打开已验证的 15m 深链；未启用时显示原因，保留官方个股详情入口。旧信号是否仍在滚动 K 线范围内由工作台显示，题材页不生成 K 线或补采数据。

## 导入合法获取的快照

输入可以是一题材一个 `Theme/InfoGet` JSON、装有多个详情的目录，或显式 manifest。原始响应需要明确 `errcode=0`，有效 `ID/Name`，真实的 `Table[].Level1`、同一 Table 行的 `Level2[]`、各节点 `Stocks`，以及可选顶层 `StockList/Stocks`。`ClassLayer=2/3` 均按实际 Table 解析。

```powershell
& 'D:\Python_Envs\envs\nga\python.exe' -B -X utf8 -m addons.lan_theme.import_snapshot --data-dir 'E:\tick stock panel\data' import --input 'C:\AuthorizedKPL\details' --manifest 'C:\AuthorizedKPL\manifest.json' --asof '2026-11-09 15:00:00' --source '本人授权账户于2026-11-09导出的题材详情' --symbols-file 'C:\AuthorizedKPL\tsp-symbols.json'
```

路径和来源名称换成自己的合法文件。`--asof` 是数据快照日期，不是随意填写的导入日期；无时区按北京时间，带时区自动转换。所有输入为 UTF-8（可带 BOM）。`--source` 必填。成功退出 0，拒绝退出 2；stdout 仅返回结果、snapshot ID 和覆盖核对数，不输出原始股票响应。

Manifest v1 示例为协议说明，不是生产题材数据：

```json
{"format":"lan-theme-manifest","format_version":1,"files":["theme-a.json","theme-b.json"],"expected_theme_ids":["来源ID-A","来源ID-B"],"scope":"PARTIAL"}
```

路径相对 manifest 所在目录。存在 manifest 时仅导入列出的文件；否则目录读取 `.json/.jsonl/.csv`，应将 manifest 和 symbols 文件放在输入目录外。输出 `expected/fetched/valid/missing/rejected`：fetched 是文件数，valid 是通过的唯一题材数。缺项、失败项、重复题材 ID 或未声明 ID 均拒绝整批覆盖。`MANIFEST_COMPLETE` 仅表示显式声明的 ID 已齐，**不证明 KPL 全目录**；没有权威目录时覆盖为 UNKNOWN。不存在猜测 ID 遍历。

`tsp-symbols.json` 为从可信 TSP 已知身份导出的 canonical symbol 字符串数组。六位代码仅在唯一匹配时解析；如同时存在 `000001.SH`、`000001.SZ` 则保留 `UNRESOLVED_SYMBOL`。页面也会对照所选日 MA55 已发布 symbol 解析，无法核实的关系不进入 BUY 交集。股票名称不用于猜交易所。

### 版本化标准导出

JSON 使用显式 envelope；`branch_path` 最多保留 Level1/Level2 两层，`branch_ids` 可选，但来源有 ID 时应保留。

```json
{"format":"lan-theme-export","format_version":1,"asof_cn":"2026-11-09 15:00:00","rows":[{"theme_id":"来源ID","theme_name":"题材名称","branch_path":["一级细分","二级细分"],"branch_ids":["L1-ID","L2-ID"],"stock_code":"300863.SZ"}]}
```

JSONL 首行为不含 rows 的同一版本 header，后续每行一条 row。CSV 必须有 `theme_id,theme_name,branch_path,stock_code`，导入时额外指定 `--export-version 1`；branch_path 可写 JSON 数组或 `一级 / 二级`，留空表示细分未确认。CSV 可选 `branch_ids`（JSON 数组）、`prod_name/IsZz/IsHot/Reason`。三种规范导出统一记录 `source_kind=AUTHORIZED_EXPORT`，不冒充原始 InfoGet。

### 拒绝、保留与回退

本插件只在 `data/user_data/lan_theme/themes.sqlite3` 存题材元数据，不存 K 线。导入持有文件 lease，并以 SQLite 事务切换 active snapshot；失败保存有界错误，保留原合法数据并显示 STALE。时间倒退须显式 rollback；同一题材层级下降、节点/关系数下降超过一半，或整批题材/节点/关系骤降超过一半，均拒绝覆盖。真实分类调整触发此规则时应核对来源，不能将截断输入强行写入。

```powershell
& 'D:\Python_Envs\envs\nga\python.exe' -B -X utf8 -m addons.lan_theme.import_snapshot --data-dir 'E:\tick stock panel\data' list
& 'D:\Python_Envs\envs\nga\python.exe' -B -X utf8 -m addons.lan_theme.import_snapshot --data-dir 'E:\tick stock panel\data' rollback 'snapshot-列表中的ID'
```

最多保留最近五次合法快照，重复输入返回 UNCHANGED。`source_hash` 对输入内容、来源、as-of、manifest、版本和符号映射生成一次摘要，直接用于跳过重复规范化和发布，并保留来源回执。一次输入及规范化快照各不超过 32 MiB、2,000 题材；异常字段/空响应/JSON 截断都拒绝。错误元数据只保留最近一次，应用日志由宿主管理。

顶层 Tag.ID 只有在本题材节点中唯一精确匹配才附分支；未知、歧义 Tag 保留「题材成分，细分未确认」。同股的多题材、多分支关系保留，各题材命中按 symbol 去重，全局统计不将分支人数相加。

## 发布、认证与读写边界

后台通过官方 `/api/ext-data` 创建本插件的 `lan_theme_tree`、`lan_theme_status`，通过官方 ingest 写入。通常每个题材一条嵌套 JSON；大题材按 512 KiB 目标切片，单关系超过 256 KiB 拒绝发布。使用 A/B 两个发布槽，完成全部分片后才原子切换单条状态；每槽最多 4,096 行，两槽最多 8,192 行，低于 SDK 的 20,000 上限。旧槽多余位置清空复用，不无限追加。

页面只调用 Page SDK `extDataRows/extPagesList`。MA55 的 `lan_ma55_summary/day/history` 是唯一信号事实来源；当前日核对 generation，历史从已发布 history 读取。超出 limit 的结果明确报错，刷新失败保留上一个页面结果。题材、路径、来源、规则切换均为浏览器本地过滤；日期切换只读官方已发布数据。不会调用 QuantDash、公共行情 demand 或 1m/5m 接口，也不重算 BUY。

确认事件的 `event_id/confirmed_at/first_seen_at/setup_at/defense/adjust_factor/rule_version` 原样读取；深链仅将日期空格转换为 URL 的 T 分隔符。LIVE/DELAYED/REPLAY 分列计数，WATCH 独立页签，历史 WATCH 未发布规则/来源时显示未知。快照日期晚于信号日时提示「按当前题材归属回看历史，不代表当时分类」。页面不提供收益回测结论。

无密码本机安装可直接初始化视图。启用认证时，先用有管理权限的合法会话创建这两张表（字段见 `backend/publisher.py::configs`）；后台后续读取/写入可使用官方 `read:ext`、`write:ext` Token。可在私有 `data/user_data/lan_theme/settings.json` 配置 `api_base`、`api_token` 或 `api_session`，重启生效；不要提交该文件，不把凭据发送浏览器。`tf_session` 会过期，不能视作永久服务凭据。发布遇到认证/权限问题显示 PUBLICATION_UNAVAILABLE 并保留本地快照。自定义 GET status/snapshots 路由遵守宿主会话权限，不能假定 read:ext Token 能访问它们。没有自定义 HTTP 写入入口。

DEMO 只供独立 data-dir 测试：私有 settings 必须有 `allow_demo:true` 并在 CLI 显式传 `--demo`，UI 显著标注 DEMO。正式目录保持默认关闭；包内不含样例题材。设置改变需重启后台，运行中不会自动放开 DEMO。

## 验证与结构来源

```powershell
& 'D:\Python_Envs\envs\nga\python.exe' -B -X utf8 -m pytest -q -p no:cacheprovider addons/lan_theme/tests_backend
node --test addons/lan_theme/tests/*.test.mjs
& 'D:\Python_Envs\envs\nga\python.exe' -B -X utf8 -m ruff check addons/lan_theme
git diff --check
```

实际官方安装、启停、空源和 DEMO 验收见同目录 `KPL_THEME_FILTER_ACCEPTANCE_20261109.md`。需要停用本功能时，在官方扩展管理器禁用 lan.theme 并重启；不会删除原三插件或它们的数据。题材数据回退使用上面的独立 snapshot rollback。

规范化算法由本项目独立实现，仅参考公开结构：

- [KPL-post 固定提交](https://github.com/zensu357/KPL-post/tree/f44b609f92b2bf91da51aeebca6c50372911ddb4)：66 请求的成功响应数组均为空；鉴权参数名称不等于访问许可，不作为生产客户端。
- [Trade 文档 InfoGet 示例](https://github.com/xiyuxifeng/Trade/blob/main/trade-strategy-ai/docs/bak/kaipan.md)：Table、Level1/Level2、StockList/Tag 和 ClassLayer=3。
- [dsh-plugin-stock 固定源码](https://github.com/yllyx/dsh-plugin-stock/blob/b3723662d11337b6b6263cd647709d5847fbb67a/plugin/backend/kpl.py)：get_theme_detail_socket 的详情解析与全目录来源分开。
- [daily_trading_review 固定源码](https://github.com/yanbixing8818/daily_trading_review/blob/e385dacfb14f7e5f53d2abe08d10d8b6240b3674/demo2/stockTool/kaipanla/kaipanla_ticai.py)：仅研究字段结构；未采用其 ID 探索或 ClassLayer=2 限制。

未复制外部实现、未运行外部仓库代码、未取得商业在线成功响应。历史平面成分和可浏览题材网页均不能证明当前全量目录。
