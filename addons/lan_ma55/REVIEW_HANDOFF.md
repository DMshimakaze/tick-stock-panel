# MA55 V0.2 纯插件交付验收

2026-11-07，北京时间。**Page Pack 已实现、构建并安装；官方 TSP 核心源码差异为零。**

## 基线与交付位置

- 目标：个人 Fork `DMshimakaze/tick-stock-panel`，分支 `feature/tsp-ma55-v02-plugin-20261107`，远端名称 `personal`。不推送官方仓库或main。
- 本轮基线：`282a8cabadc8bdd6ffa83ab26bed4c2280e05415`，已通过 `git ls-remote personal` 确认上一轮提交推送完成。继承 QuantDash 插件源码 `1385c90147d1ad2100c9a949b88e7d69b782fda7` 及其验收说明。
- 官方宿主：v0.3.32 / `ca3a44252edb414e3f4c1df1b7c381bad9f55c8b`；本轮没有升级宿主。QuantDash v1.2.6 沿用，源码、配置、凭据与行情采集/复权链路不改动。
- 业务改动全部在 `addons/lan_ma55/`：6个运行模块、5个测试文件、构建配置、package/lock及两份说明。
- 本地安装产物：`addons/lan_ma55/dist/extension.js`，单文件ES Module，构建输出约43.14 kB / gzip15.74 kB；dist、截图、依赖和真实行情不提交。
- 初始未跟踪的根目录 `REVIEW_SUMMARY.md` 是旧审查资料，已原样保留，不进入本轮提交。

## 权威规则来源

生产规则从私有 `DMshimakaze/DM-Ma55-Method` 的固定提交 **`23477622536d1206b2af3c164b4c905aa2e15e8d`** 只读取得，版本 **`MA55_15M_MINIMAL_V5`**。使用Git对象，未采用该仓库工作区的未提交修改。

下表均为Git blob SHA，不建立额外文件指纹或校验体系：

| 固定源文件 | Git blob SHA |
| --- | --- |
| `ma55/structure.py` | `0e777b61d9c33c9fdae4892508491e24824b0a5e` |
| `ma55/features.py` | `e682a304c493b1ff8c78a67980f51096056451ab` |
| `ma55/intraday_unified.py` | `f7aef99f87dd483723638c4c5cd924ca0033579a` |
| `config.py` | `4464479913b2ac5324317b87e7531a87ae9d51c4` |
| `docs/RULES.md` | `2334f7ada7857de9edcf4114c326441b2b7ea697` |
| `tests/test_ma55_v02_frozen.py` | `d5d46093fd6204f2f48bb100b819cf650511b846` |

研究定版为 `ab272ef6c700d5aece135d3e6f8e140cf7146630`，其中 `algorithm.py` blob 为 `c8f55be70d06085004c22f29b2e45f9f3fbce8c7`，`V02_FOCUSED_REPLAY.md` blob 为 `b95018d6fff0f8e5bbf91bf8e359612846d461ed`。冻结行情来源提交为 `56d670416e4919852ef8929a5fa42241aaab37f3`，只读使用，不提交行情或manifest。

生产插件移植 `minimal_features` 与 `advance` 的原分支和字段；没有使用V4状态机、旧候选器或15m观察雷达代替BUY规则。MA233、W、日/60m背景不参与BUY硬门槛。

## 官方接入与数据流

```text
既有Provider → TSP分钟存储与官方复权读取
  → ctx.api（自选、资产、逐日价格参考、最近10/20日本地分钟、当日增量）
  → 真实1m → 5m → 15m完整性检查
  → 冻结特征与逐根advance
  → 每股状态 + 首次确认冻结 + 后续风险分离
  → 插件列表/SVG + ctx.ui官方个股详情
```

已核对 `docs/page-sdk.md`、`extension-packages.md`、`secondary-development.md`、`plugin-development.md`、`strategy.md` 及实际类型/调用链。Page Pack 的 `ctx.api` 未公开任意自身扩展端点请求方法，`ExtensionContext.repository` 仅公开 `get_name_map`。因此本轮选择可独立安装的Page Pack。

**受现有官方扩展契约限制，未实现：** 配套SQLite后端、页面关闭期间后台监控/推送、全市场后台扫描。没有为这些功能补核心API、访问私有仓库、读取TSP数据库/Parquet或建立第二套行情库。

若以后需要后台形态，最小官方需求是：命名空间限定的页面→自身扩展路由桥；后台只读批量历史K与计划任务合同。若需要覆盖特殊报价品种和更精确分钟定版，还需公开逐标的/逐日原始tick、分钟价格基准/调整比例及完成标志。本轮未实现这些宿主接口。

## 实际验证

所有Python操作均使用现有 **nga**，已核对 `sys.executable`；没有新建环境或升级QuantDash/MA55依赖。

| 执行项 | 实际结果 |
| --- | --- |
| 插件目录 `pnpm build` | 通过；最终单文件产物43.14 kB |
| 带 `MA55_PYTHON / MA55_REFERENCE_REPO / MA55_FROZEN_PACK` 的 `pnpm test` | **59 passed，0 failed，0 skipped**，约35秒 |
| 最后搜索输入生命周期修正后的 `node --test tests/page.test.mjs` | **9 passed，0 failed，0 skipped**；随后窄屏样式经构建及实际浏览器验证 |
| `nga python -B -X utf8 -m ruff check tests/oracle.py` | 通过 |
| TSP原生导入中心 | 上传JS、信任确认、浏览器加载校验通过；侧栏 `/lan-ma55` 可进入 |
| 启停/卸载/恢复 | 停用刷新后菜单消失；官方卸载接口返回ok，列表无该包且 `/health` 仍为ok；已重新安装最终构建并启用 |
| 实际页面 | 官方搜索股票/ETF、临时观察、数据不足、完成K图、官方个股详情正常；自选本来为空，未添加宿主自选 |
| 视觉检查 | 默认桌面与720×960窄屏；窄屏表格水平滚动、详情两列、标题避开宿主菜单按钮；最终页面控制台无error |

合成行情经过真实1m→5m→15m聚合，再同时进入插件和固定提交的原Python `minimal_features / advance`，每根比较决定及全部结构字段。覆盖预热、09:45/午休/15:00、单K/多K、去重、离线回踩/深脚/新推动解锁、4根有效期与跨日清理、缺分钟/空open/冲突行、股票及ETF调整tick、同槽至少3样本、滚动窗口与JSON重启、不可变确认、时效分类、缺历史因子与价格基准变化。确认价距MA55过远时的提示及三个同根图表标记也有页面断言。

**冻结私有包实际在本机可用。** 测试临时导出固定提交的源码，真正运行原Python `analyze`，与插件对照最终决定、结构状态及全部可见确认身份、时间、支撑起点和单K/多K分类：**15个正向确认、7个指定抑制、2个否定窗口全部通过**。这批检查不是skip，也不是仅JS镜像；测试未运行原研究GUI、生产数据库或其持久化/分页整套回归。结果用于规则语义和指定样本验收。

真实TSP页面烟测与私有冻结回放分开计量：

- `600519.SH` 的公开本地分钟API返回2026-11-06单日241行，包括09:30行；插件聚合为16根真实完成15m，显示 **数据不足，等待56根连续完成K**，无BUY。
- `588170.SH` 的公开本地分钟范围当前无会话，页面显示暂无完成K；ETF标准tick显示0.001，不用股票tick替代，不私自补历史。
- 直接通过 `ctx.ui.openStockPreview` 打开了茅台的原生个股详情，展示官方日K与分时。
- 本轮为休市日，未现场验证交易时段连续增量、提供商配额争用或延迟。现有真实TSP输入不足以执行端到端BUY确认，需用户通过官方分钟同步准备历史。没有为凑验收重拉全市场。

## 存储、时效及剩余限制

运行缓存仅为浏览器IndexedDB内有界派生状态：最多80只，每股55根必要特征输入、每槽5份量、120次确认及风险。事件ID包括规则、标的、支撑开始和转强结束时刻。`processed_at`单调，重复投递不再发确认，JSON重启与窗口滑动保留波段身份。清理站点数据或换设备后只能从当前TSP可见历史恢复。

普通A股0.01与ETF0.001按TSP资产类型区分。逐日调整比例只来自同日公开 `close/raw_close`；历史分钟收盘与日K不一致、缺字段或调整尺度改变时停发BUY、重新预热。报价类型与历史比例的合成/冻结验证已通过，当前真实ETF分钟为空，**ETF在本机TSP上的完整BUY路径尚未实证**。

只观察当前20只的一页，初次10日、必要时20日，最多3并发；不扫描全市场、不私拉源。15m结束后等60秒确认，SDK无完成标志所带来的这段延迟已在README说明。页面关闭停止后续请求和计时器；官方API不接收AbortSignal，已发请求可完成，其结果会被丢弃。

确认 `setup_at / confirmed_at / data_asof / first_seen_at` 分开，重放不会标记成刚发生的实时消息。价格基准变化时原确认保留，其旧防守点不参与新价格的风险比较。关闭页面后的无人值守通知未实现。

## Diff与回退

交付提交相对本轮基线的 `git diff --name-status 282a8cabadc8bdd6ffa83ab26bed4c2280e05415..HEAD` 只新增 `addons/lan_ma55/`。官方核心零差异命令：

```powershell
git diff --name-only ca3a44252edb414e3f4c1df1b7c381bad9f55c8b -- backend/app frontend/src dev.ps1
git diff --check
git diff --cached --check
```

核心比较无输出；提交前检查不包含数据、密钥、运行库、构建JS、ZIP、缓存、日志、截图或任务附件。没有新建宿主路由、schema、行情或复权实现。最终提交SHA及远端确认见交付回执，也可在本分支执行 `git rev-parse HEAD`。

回退时在官方已安装扩展中停用或卸载此页面并刷新浏览器。QuantDash和宿主用户数据独立；旧QuantDash分支与提交保留。本轮自审未发现阻断问题；尚未验证的真实盘中范围如上。
