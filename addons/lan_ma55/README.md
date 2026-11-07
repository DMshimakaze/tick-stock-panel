# MA55 结构观察 · 兰 V0.2

独立 Page Pack，版本 `0.2.0`，面向 TSP 官方 `v0.3.32` / Page Pack SDK v1。运行时只有一个自包含 ES Module；删除页面包即可移除，不需要修改或重建宿主。

## 构建与安装

在本目录执行：

```powershell
pnpm install --frozen-lockfile --ignore-scripts
pnpm build
```

产物为 `dist/extension.js`。模板来自官方 `frontend/page-pack-template` 的 ES Module / mount 契约；本插件使用原生 DOM 和简单 SVG，不需要额外 React 或图表运行库。

在 TSP **设置 → 导入与备份** 上传此文件，页面标识填写 `lan_ma55`，确认自己的代码来源并安装。宿主加载校验通过后刷新浏览器，侧栏出现 **MA55 结构观察**，路由 `/lan-ma55`。包清单的扩展 ID 是 `lan.ma55`，存储用的页面标识是 `lan_ma55`。

在 **已安装扩展 → MA55 结构观察** 中启用、停用或卸载；刷新浏览器后生效。页面包安装不需要重启后端。菜单排序和可见性由 TSP 设置管理。

## 使用与数据

- 默认读取自选股，可切换自选分组、股票/ETF。每页最多20只，只有当前页参与更新，历史加载并发最多3只。搜索通过官方 `instrumentSearch` 添加临时观察，不写入宿主自选；临时搜索集合在离开页面后结束。
- 首次读取官方本地分钟接口最近10个交易日；10日窗口已满但有效历史不足时扩大到20日。至少56根连续有效完成15m K才能确认。原始分钟继续由 TSP 存储、同步和复权。
- 交易日来自官方上证指数日K日期；每股行情仅经 `ctx.api.klineMinuteRange / klineMinuteBatch / klineDaily` 读取。分钟量单位为宿主的“手”，量比与研究样本的“股”无量纲等价。
- 09:31–09:45 构成首根15m，午后从13:01开始；09:30集合竞价行不计入。15m由3根真实5m构成。缺分钟、空 open、无效OHLCV或无量时停止确认并重新预热。
- SDK没有分钟定版标志，因此结束后等待60秒再计算，确认时间仍记为该15m的结束时刻。13:33最多使用13:30 K。每分钟检查一次时钟，每个完成15m边界只刷新一次，休市日不批量取分钟。
- 普通A股标准原始tick为0.01、ETF为0.001，资产类型由TSP确认。历史调整tick逐日使用同一标的、同一资产、同日的公开 `close/raw_close`；历史日还校对15:00分钟与日K收盘。缺少因子证据或价格基准不一致时显示限制，停发正式BUY。宿主尚未公开逐标的历史tick元信息，特殊报价品种不在本版标准A股/ETF范围。
- 图表只展示插件已读取的完成15m、MA55、推动、支撑脚及选中确认。**官方个股详情**按钮调用 `ctx.ui.openStockPreview`，复用宿主图表和资产路由。

## 冻结规则与状态

唯一BUY链为“有效推动 → MA55附近支撑测试 → 第一根转强”。`engine.mjs` 等价移植指定生产提交的 `Rules / advance / minimal_features`，包括单K收回、多K测试、4根等待窗口、48根推动记忆、跨日清理和同波段锁及三种重新解锁。日K背景、MA233、W和ATR不作为BUY门槛。

`BUY_STRUCTURE` 是结构确认。确认收盘距离MA55超过3%时显示“离均线较远，不追价”。确认载荷首次写入后保持不变，后续防守失效、时效和数据待定记录在独立风险对象；价格基准变化后不将旧基准的防守点与新价格比较。

IndexedDB `lan-ma55-v02` 仅保存派生状态：每股单调水位、结构状态、最近55根必要15m样本、每个时槽最多5份量、最近120个确认和后续风险，最多80只；不保存1m/5m/日K原始库。缓存不存在或版本不兼容时从TSP历史重放。规则版本为 `MA55_15M_MINIMAL_V5`，缓存schema为1，旧V4状态不会读作本版。

确认同时保留 `setup_at / confirmed_at / data_asof / first_seen_at`，来源明确区分历史重放、实时确认和延迟确认。初次打开页面时发现的历史确认不会变成实时消息。**页面关闭后停止观察；没有后台扫描或推送。** SDK请求方法没有AbortSignal参数，卸载时停止计时器和后续请求，已发出的至多3个宿主请求可以完成，其返回结果会被丢弃。

## 源码与测试

| 文件 | 责任 |
| --- | --- |
| `src/extension.mjs` | 清单、页面生命周期、范围和有限并发 |
| `src/host_data.mjs` | TSP公开数据桥、资产与逐日价格口径 |
| `src/aggregate.mjs` | 北京时间、真实分钟完整性及5m/15m聚合 |
| `src/engine.mjs` | 冻结特征和逐根V0.2规则 |
| `src/state.mjs` | 水位、确认、风险和有界派生存储 |
| `src/presentation.mjs` | 列表、筛选、详情及简单SVG |

```powershell
pnpm build
pnpm test
```

无私有参考时，测试会明确跳过两个Python/私有数据对照组；合成与界面测试仍可独立执行。要执行实际Python一致性及冻结数据回放，使用已有 `nga` 解释器设置以下环境变量，再运行 `pnpm test`：

```powershell
# 填写本机已有的只读目录及nga解释器，不创建新环境
$env:MA55_PYTHON = '<nga解释器>'
$env:MA55_REFERENCE_REPO = '<DM-Ma55-Method的本地Git目录>'
$env:MA55_FROZEN_PACK = '<冻结包目录，含manifest.json>'
pnpm test
& $env:MA55_PYTHON -B -X utf8 -m ruff check tests/oracle.py
```

测试专用 `oracle.py` 从固定Git提交导出参考源码到临时目录，实际调用原Python函数；生产包不依赖MA55仓库、Python或CSV。私有行情仅只读进入子进程内存，不进入仓库、构建包或报告。

规则来源、实际验证与现有接口缺口见 [REVIEW_HANDOFF.md](REVIEW_HANDOFF.md)。
