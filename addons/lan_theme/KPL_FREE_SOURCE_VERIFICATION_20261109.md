# 开盘啦三级题材零新增费用来源核查（2026-11-09）

本轮没有证明存在可免费、获允许地持续同步完整“开盘啦大题材 → Level1/Level2 → 全部成分股”的来源。公开代码足以核对字段结构，但完整目录、访问条件和本地更新权利尚未同时成立。业务数据接口主动请求 **0 次**，没有 `LIVE_VERIFIED` 来源。本轮只交付本报告。

## BASELINE

| 项目 | 实际核对结果 |
| --- | --- |
| 个人仓库 | `DMshimakaze/tick-stock-panel`；`personal` 指向该 Fork |
| 工作分支 | `feature/tsp-kpl-theme-filter-v1-20261109` |
| 开始时本地 HEAD | `61fdc9daa116a3f7f7f08e59e554d139b76fdfc1` |
| fetch 后远端分支 HEAD | `61fdc9daa116a3f7f7f08e59e554d139b76fdfc1`，与本地一致 |
| 官方宿主 | `v0.3.32` / `ca3a44252edb414e3f4c1df1b7c381bad9f55c8b` |
| 源码清单版本 | `lan.marketdata 1.0.2`、`lan.ma55 0.3.2`、`lan.kline 2.0.1`、`lan.theme 1.0.2` |
| Python | 已核对任务指定的现有 `nga` 解释器；没有安装或升级依赖 |
| 已有未提交文件 | `REVIEW_SUMMARY.md`，保持原状，不读取、不暂存 |
| 本轮变更 | 仅新增本报告；没有新增验证程序、适配器或测试代码 |

已阅读本地开发规范、`AGENTS.md`、`CONTRIBUTING.md`、二次开发、插件和外部扩展契约。本轮是文档核查，不新增 L1/L2/L3 运行能力；宿主核心对官方 tag 的差异为零。清单版本是源码核对结果，没有通过启动、重启或访问运行时数据库重新验收安装状态。

## CANDIDATES

### 版本与软件许可

以下是 2026-11-09 联网读取的 GitHub `main` SHA；同时通过 `git ls-remote` 核对分支。日期为 GitHub 仓库 `pushed_at`（UTC），不是接口最后成功日期或数据同步日期。MIT 等软件许可均不替代上游数据许可。

| 候选及固定版本 | 仓库最近 push 日期 | 软件许可核对 |
| --- | --- | --- |
| [zensu357/KPL-post](https://github.com/zensu357/KPL-post/tree/f44b609f92b2bf91da51aeebca6c50372911ddb4) / `f44b609f92b2bf91da51aeebca6c50372911ddb4` | 2026-07-22 | GitHub license 为 null；作者说明供学习研究，未提供官方授权 |
| [yllyx/dsh-plugin-stock](https://github.com/yllyx/dsh-plugin-stock/tree/b3723662d11337b6b6263cd647709d5847fbb67a) / `b3723662d11337b6b6263cd647709d5847fbb67a` | 2026-10-10 | README 声称 MIT；GitHub license 为 null，完整 Git tree 未发现 LICENSE/COPYING 文件 |
| [yanbixing8818/daily_trading_review](https://github.com/yanbixing8818/daily_trading_review/tree/e385dacfb14f7e5f53d2abe08d10d8b6240b3674) / `e385dacfb14f7e5f53d2abe08d10d8b6240b3674` | 2026-01-08 | GitHub license 为 null，未取得数据复用授权依据 |
| [xiyuxifeng/Trade](https://github.com/xiyuxifeng/Trade/tree/0553f59c5902a53b37f39146b6e2726f4e3e94d3) / `0553f59c5902a53b37f39146b6e2726f4e3e94d3` | 2026-07-11 | GitHub 标识 MIT |
| [Rainynitesky/kaipanla-data-parser](https://github.com/Rainynitesky/kaipanla-data-parser/tree/7bfae30ab420e10af816584b7aa77e21c25c9e14) / `7bfae30ab420e10af816584b7aa77e21c25c9e14` | 2026-05-23 | GitHub 标识 MIT |
| [hssqz/plate-rotation-skill](https://github.com/hssqz/plate-rotation-skill/tree/ebac550cefa2ab7cc03cc4c87c62ba39352dbe1e) / `ebac550cefa2ab7cc03cc4c87c62ba39352dbe1e` | 2026-08-22 | GitHub 标识 MIT；上游服务另有权利边界 |
| [Niceck/hhxg-top-hhxg-python](https://github.com/Niceck/hhxg-top-hhxg-python/tree/ca04ebc88185279b43efa41116bb880d72eaaa63) / `ca04ebc88185279b43efa41116bb880d72eaaa63` | 2026-11-08 | GitHub 标识 MIT；最新公告撤回免费 AI/API 通道 |

### 免费条件、结构与未通过点

| 来源 | 免费及访问条件 | 目录 / 层级 / 更新证据 | 本轮在线取数 |
| --- | --- | --- | --- |
| 开盘啦官方 | 官网说明用户工具免费；免费 API 自动化、本地缓存及每天更新许可为 `UNKNOWN` | 官方教程有题材检索、热度排序和多级分支；没有找到公开完整目录 API、JSON/CSV 导出说明及同步授权 | `NOT_TESTED`；许可前置条件未成立 |
| KPL-post | 数据访问条件 `UNKNOWN`；请求定义包含 Token、UserID、DeviceID | `Theme/InfoGet` 详情、`InfoSearch` 搜索；两集合均无保存的响应，搜索不能证明全目录 | `NOT_TESTED`；无可据以测试的已许可免登录文档 |
| dsh-plugin-stock | 数据许可 `UNKNOWN`；详情依赖登录会话，目录依赖私有 Socket/签名器 | 源码可核对小表格及扁平成分字段；“全列表”代码不是本轮取得的目录证据 | `NOT_TESTED`；不运行签名器或借用会话 |
| daily_trading_review | 数据许可 `UNKNOWN`；包含会话参数的第三方调用实现 | 按 ID 1..N 遍历；只在 `ClassLayer == "2"` 时解析层级，不能作为完整三级目录方案 | `NOT_TESTED`；不执行 ID 穷举 |
| Trade | 实时访问和数据许可 `UNKNOWN` | 有 `ClassLayer="3"` 的静态 JSON 示例，可离线解析；没有当日目录、连续更新或当前成功响应 | `NOT_TESTED`；仅做结构核对 |
| kaipanla-data-parser | 数据许可 `UNKNOWN`；要求 App Token/UserID/DeviceID，说明涉及抓包、特定 UA 和私有 Socket | 主要是 `RealRankingInfo`、`SonPlate_Info`、`ZhiShuStockList_W8` 的板块排行/股票 CSV；不能证明是完整 Theme 题材库 | `NOT_TESTED`；未安装或运行抓包、会话刷新、Socket 程序 |
| plate-rotation-skill | 作者称无需 Key；上游自动化/缓存许可 `UNKNOWN` | 板块强度与 Top5 龙头；能力上限为 `FREE_PARTIAL_ONLY`，完整题材目录和全部分支成分不成立 | `NOT_TESTED`；不运行其伪装浏览器头、Cookie 读取和重试流程 |
| hhxg.top | 当前 AI/API 接入为会员权益，按本轮范围记 `PAID` 并排除 | 2026-11-08 仓库公告称免费端点/公开数据目录下线；旧 README 免费描述不再适用 | `NOT_TESTED`；没有试探已关闭或收费接口 |
| BigAMap（补充） | 官网有免费网页展示；部分增强需赞助；API/数据复用许可 `UNKNOWN` | 官方产品教程描述题材层级、成分和每小时更新，但未提供免费完整层级文件或自动同步契约 | `NOT_TESTED`；仅查阅公开产品说明 |

### 决定性源码及官方文档

1. **官方免费界面与导出边界。** [官网](https://www.kaipanla.com/omain/index.html)说明面向用户的免费工具；[题材库教程](https://kaipanla.com/article/38490)介绍检索、排序及多级分支；[官方功能案例](https://www.kaipanla.com/article/39810)说明产业链表格与分支股票。上述页面没有建立完整目录、机器可读导出和定期本地同步的接口契约。未发现原生 JSON/CSV 导出的公开依据，不能声称 App 可以一键导出。
2. **详情与目录是两条链。** [dsh-plugin-stock 的 kpl.py](https://github.com/yllyx/dsh-plugin-stock/blob/b3723662d11337b6b6263cd647709d5847fbb67a/plugin/backend/kpl.py#L1898)中，目录 `get_themes_socket()` 调用 `kpl_socket.get_themes()`，要求签名器；[详情解析](https://github.com/yllyx/dsh-plugin-stock/blob/b3723662d11337b6b6263cd647709d5847fbb67a/plugin/backend/kpl.py#L3609)实际读取 HTTP `Theme/InfoGet` 的 Table/StockList。函数名带 socket 不表示这两者具有相同获取条件。
3. **旧遍历脚本不能证明全量。** [kaipanla_ticai.py](https://github.com/yanbixing8818/daily_trading_review/blob/e385dacfb14f7e5f53d2abe08d10d8b6240b3674/demo2/stockTool/kaipanla/kaipanla_ticai.py)使用 ID 范围遍历，并将 Level1/Level2 追加到平面结果；没有权威目录和当前层级覆盖证明。没有执行该脚本。
4. **CSV 父子板块与题材小表格不同。** [parser README](https://github.com/Rainynitesky/kaipanla-data-parser/blob/7bfae30ab420e10af816584b7aa77e21c25c9e14/README.md)及 [crawler_batch.py](https://github.com/Rainynitesky/kaipanla-data-parser/blob/7bfae30ab420e10af816584b7aa77e21c25c9e14/crawler_batch.py)的来源是板块排行、子板块和排序股票接口。README 的 Top9 多排序合并也不构成全部成员证明；App 会话、UA、Socket 取得方式未采用。
5. **无 Key 不等于已许可完整来源。** [plate-rotation 的请求器](https://github.com/hssqz/plate-rotation-skill/blob/ebac550cefa2ab7cc03cc4c87c62ba39352dbe1e/scripts/fetch.py)指向 `duanxianxia.com`，注入 Safari UA、Referer、Origin，可读取 Cookie，并带缓存/重试。[作者声明](https://github.com/hssqz/plate-rotation-skill/blob/ebac550cefa2ab7cc03cc4c87c62ba39352dbe1e/DISCLAIMER.md)说明服务非其运营、上游无正式 API 文档，条款需另查。本轮未找到该上游授予本用途的说明，因此未进入实测。其[接口说明](https://github.com/hssqz/plate-rotation-skill/blob/ebac550cefa2ab7cc03cc4c87c62ba39352dbe1e/references/api_getlongbyplate.md)明确是每日最多五个领涨股，不能补齐全部成员。
6. **其他排除项。** [hhxg 最新公告](https://github.com/Niceck/hhxg-top-hhxg-python/blob/ca04ebc88185279b43efa41116bb880d72eaaa63/README.md)明确免费通道关闭并转 Max；这里记录的是作者公告，未实测其所称 404/401。Meoz 按任务要求排除收费方案，未调用、未申请 Key。TuShare 按任务排除；当日可读的[官方成分文档](https://tushare.pro/document/2?doc_id=351)仍标 5000 积分，字段是题材/股票映射，没有 Level1/Level2。历史 `kpl_concept` 文档地址 `doc_id=350` 当日显示文档不存在；任务提及的停更说明未在这张当前页面重新取得，不将旧说明包装成在线更新证据。
7. **补充候选也未证明免费导出。** [BigAMap 题材教程](https://bigamap.cn/docs/theme-library/)描述每小时更新及分组股票；[官网](https://bigamap.cn/)说明免费展示和赞助增强。其[选股器教程](https://bigamap.cn/docs/stock-selector/)中的导出是筛选结果场景，没有建立免费的完整 KPL 层级导出能力。没有登录、赞助、反查私有接口或下载商业数据。

## REAL_HTTP_EVIDENCE

**业务数据端点主动请求数：0 / 6；每个候选均为 0 / 3。** 已联网核查 GitHub 源码/元数据和公开说明页；这里的六次限额按任务“最小合法实测”中的业务取数请求计，不把文档阅读计为在线数据验证。搜索结果中的索引片段也不是本轮源站数据响应。

| 数据端点（仅列源码定义，未请求） | 源码所述方法与条件 | 本轮结果及停止原因 |
| --- | --- | --- |
| `applhb.longhuvip.com/w1/api/index.php`，`c=Theme, a=InfoGet/InfoSearch` | POST；历史定义有 Token/UserID/DeviceID | `NOT_TESTED`；未取得明确免登录使用文档或本轮合法凭据 |
| `duanxianxia.com/api/getPlateRotatData` | POST；第三方代码注入浏览器头；输出 `first` string、`html` string | `NOT_TESTED`；本地复用及更新许可未确认 |
| `duanxianxia.com/api/getLongByPlate` | POST；输出 `html` string，说明为 Top5 领涨 | `NOT_TESTED`；同上；也不满足完整成员结构 |
| hhxg / Meoz / TuShare 业务接口 | 已按本轮免费约束排除 | 请求数 0；不探测收费/关闭通道 |
| BigAMap 业务接口 | 未取得可据以测试的公开数据 API 契约 | 请求数 0；不从产品页面推测私有 API |

因此 HTTP 状态、业务状态、真实响应时间、服务端同步时间、响应大小、真实 JSON 类型/计数、脱敏响应摘要 SHA256 均为 **N/A（未发送数据请求，没有响应）**。上表的类型来自代码或文档，未标为 `LIVE_VERIFIED`。未生成空响应摘要或拿 README 样例冒充证据。

没有满足许可前置条件，故没有执行 `--live`，也无需新增一个不会被本轮合法调用的网络验证程序。没有发送不带 Token 的试探请求、伪造 UA/Referer、跟随认证升级、重试或扫描 ID。实际请求数为零，不存在并发、超时、TLS 或响应上限的实测结果可报告。

## KPL_POST_VERSION_ASSESSMENT

对固定 SHA 中两份 Postman JSON 递归计算：请求叶子分别 **66 / 56**，保存的 `response` 条目均 **0**。题材详情/搜索都只是请求定义；共同历史参数包括 `apiv=w47`、`VerSion=6.2.20.2`。可从[固定版本目录](https://github.com/zensu357/KPL-post/tree/f44b609f92b2bf91da51aeebca6c50372911ddb4)复核这两份集合；本报告不复制认证值或商业响应。

| 判断项 | 结论 | 依据 |
| --- | --- | --- |
| 作为当前免费生产接口的证据质量 | `STALE_RISK` | 固定历史请求参数，没有成功响应、当前许可或完整目录证明 |
| `w47/6.2.20.2` 是否已经被源站废弃 | `UNDETERMINED` | 没有源站废弃公告、已许可在线测试或版本拒绝响应 |
| `CONFIRMED_DEPRECATED` 是否成立 | 否 | 仓库年代、缺少样本及别的项目使用其他版本均不能单独证明废弃 |

## SCHEMA_AND_COVERAGE

### 已核对的离线模型

`Theme/InfoGet` 的顶层 `ID/Name` 是大题材身份；`ClassLayer` 描述层级形态。`Table[]` 每行包含 `Level1` 对象和同一行所属的 `Level2[]`；Level1 和 Level2 都可能直接挂 `Stocks[]`。股票可含 `StockID/prod_name/Reason/IsZz/IsHot/Hot`。同一股票出现在不同分支时应保留各条关系。

顶层 `StockList[]` 或 `Stocks[]` 是扁平成分补充；`StockList[].Tag[]` 是标签关系，不能仅因名称近似就生成 Level2。扁平成分没有确认分支时属于细分未确认，不能通过猜测补全三级树。`InfoSearch` 的搜索结果和板块强度榜也不能当作权威全目录。

[Trade 的固定版本文档示例](https://github.com/xiyuxifeng/Trade/blob/0553f59c5902a53b37f39146b6e2726f4e3e94d3/trade-strategy-ai/docs/bak/kaipan.md#L2590)经内存 JSON 解码成功：`ClassLayer="3"`，1 个 Table 行、1 个 Level1、1 个 Level2；两层 Stocks 各 1 行，顶层 StockList 1 行，Tag 为对象数组。**这些是静态文档示例计数，不是当前市场成员数；股票行可能重复，不能相加当独立成员。** 文档列出了 UpdateTime 等字段，但该示例本身没有可核验的当前同步时间。

### 真实覆盖结果

| 指标 | 本轮取得的真实源侧证据 |
| --- | --- |
| 权威 `theme_id + name` 全量目录条目数 | 未取得；`CATALOG_UNVERIFIED` |
| 新增/下架、分页终止及完整性核对依据 | 未取得 |
| 从实际目录抽样的 2–3 个题材 ID | 未取得；不使用示例 ID 或枚举 ID 替代 |
| `nonempty_theme_count` / `level1_count` / `level2_count` | N/A（没有真实响应） |
| `verified_stock_edges` | 0 条经过本轮在线验证的边；不表示源站没有成员 |
| `unassigned_count` / 源侧去重股票数 | N/A |
| 交易所后缀准确率 / 歧义率 | N/A；未取得独立源侧样本及身份真值，未按代码前缀猜交易所 |
| 响应日期与源同步日期一致性 | 未验证 |
| 连续更新与 30 日历史成员快照 | 未验证；当前资料不能用于回填历史题材归属 |

## SAFETY

零新增费用；没有购买、注册或配置收费服务，没有 Meoz adapter 或 API 请求。没有获取、扫描、使用或上传用户 Cookie、Token、设备标识和密钥；没有利用第三方示例中的凭据。没有运行候选仓库代码、App 抓包、私有 Socket、签名器或反编译工具。

没有修改官方宿主、四个插件的运行代码/清单/构建、数据库或行情文件；没有进入 MA55 研究工程，没有重算 BUY、改动 K 线或新增 1m 依赖。冻结算法 SHA `5d1d0f6e8913f528110c93e054429f050b9851f6` 仅作为任务给定边界记录。没有重启、安装 ZIP、注册服务或运行后台同步。

外部原文只用于本次内存审阅；未落入正式快照、数据库、Git、临时源码副本或数据目录。本报告仅保存出处、版本、结构和必要统计，不保存原始题材响应。

## TESTS

本轮没有代码改动，不运行 pytest、Ruff、前端构建或行情冒烟；没有创建测试框架。

| 直接验证 | 结果 |
| --- | --- |
| `git fetch personal feature/tsp-kpl-theme-filter-v1-20261109` | 退出 0；开始时本地与远端 SHA 一致 |
| 七个候选 `git ls-remote ... refs/heads/main` | 退出 0；对应上表固定 SHA |
| 两份 Postman 集合内存递归统计 | 完成；66/56 个请求、0/0 个响应样本 |
| Trade 示例内存 JSON 解码 | 完成；结构及计数如上，不产生在线验收结果 |
| 清单版本定向读取 | 退出 0；四个源码版本与任务基线一致 |
| `git diff v0.3.32 --name-only -- backend/app frontend/src dev.ps1` | 退出 0、输出为空；核心源码零差异 |
| `git diff --check`、`git diff --cached --check` | 均退出 0；暂存区仅包含本报告 |

## DELIVERY

仅将本报告以 DCO 提交推送至个人分支 `feature/tsp-kpl-theme-filter-v1-20261109`。报告中的 base/head 指开始核查时实际读到的 `61fdc9daa116a3f7f7f08e59e554d139b76fdfc1`；交付提交 SHA 由包含本报告的提交确定，避免在报告内自引用自己的 Git SHA。

推送后须实际读取 GitHub 分支 SHA，并比较该 base 与交付 SHA；实际完整 SHA、不可变 compare 链接和文件数在交付消息给出。期望且唯一允许的差异是本报告。没有创建 PR、推送官方仓库或改动 main。

## RESULT

本轮公开候选未证明存在满足全部要求的免费可重复来源。具体缺口是：**获允许的全量目录获取方式、真实非空分支成分响应、零费用本地缓存/定期更新依据**。环境可以联网；未发出业务请求的原因是访问条件没有成立，而不是网络故障。

现阶段不能进入正式来源适配。也没有已取得的合法真实文件，故**无已证实可执行的免费快照获取备选**；不声称 App 有原生导出，不把静态样例、排行榜或其他供应商扁平概念凑成 KPL 真层级。

若以后取得官方允许个人本地使用的真实 JSON/CSV，可先走现有 `lan.theme 1.0.2` 的文件导入边界：`backend/importer.py` 的 `decode_document/import_source` → `backend/normalize.py` 的 `normalize_info/normalize_export`。原始 InfoGet 或标准导出必须有真实来源、快照时间及使用依据；CSV v1 已支持 `theme_id/theme_name/branch_path/stock_code`，目录完整性另需来源清单。这里仅指出已有能力，没有确认该文件获取渠道存在，也没有执行导入。需要新的在线源集成时，先补齐上述证据，再由用户批准另轮开发。

**唯一最终状态：`NO_COMPLIANT_FREE_SOURCE_VERIFIED`**
