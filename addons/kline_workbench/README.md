# 分钟 K 工作台

独立 Page Pack **lan.kline 1.0.1**，侧栏「分钟 K 工作台」，路由 `/lan-kline`。适用于官方 TSP v0.3.32 / Page Pack SDK v1；无需重建宿主或安装后端任务。实际验收、源代码提交与范围见 [REVIEW_HANDOFF.md](REVIEW_HANDOFF.md)。

支持股票/ETF搜索、日K及1/5/15/30/60m蜡烛、自定义简单均线、同轴成交量、十字光标、滚轮/触摸缩放、拖动与MA55信号定位。默认15m，预设MA5/10/20/30/55/233，默认打开MA55和MA233。MA周期按当前级别K线根数计算；15m MA233与60m MA55只供近似观察，不作强制等价。

## 构建与安装

在工程根目录，用现有Node/pnpm工具执行：

```powershell
pnpm --dir addons/kline_workbench install --frozen-lockfile --ignore-scripts
pnpm --dir addons/kline_workbench build
pnpm --dir addons/kline_workbench test
```

产物 `addons/kline_workbench/dist/extension.js` 自包含 Lightweight Charts 5.2.1，约270kB，低于官方5MB限制。在 **设置 → 导入与备份** 选择此JS，页面标识填写 `lan_kline`，确认来源并安装，刷新页面。导出清单ID为 `lan.kline`；管理用页面标识为 `lan_kline`。更新时确认替换该标识原页面。支持独立启停/卸载，刷新生效，不需要重启后端。

本机已安装并启用1.0.1。可打开 `http://127.0.0.1:3011/lan-kline`；从侧栏搜索名称或带 `.SH/.SZ/.BJ` 的完整代码，仅接纳官方确认的股票/ETF类型。

“初始显示”有40/80/120/240/全部，也可输入10–10000后点「应用视窗」。该数值只控制视窗，不扩大查询历史。勾选MA、修改各周期、添加或删除均线即可调整图表；周期允许1–2000，最多12条。浏览器只保存均线和视窗偏好，不持久化行情。

## 数据口径

- `ctx.api.instrumentSearch` 确认名称和资产类型；不按代码猜类型或市场。
- `ctx.api.klineMinuteRange(symbol, 20)` 只读最多20个已落库交易日。来源明确标为「TSP 本地分钟行情（官方投影）」，不补拉、不二次复权。
- `ctx.api.klineDaily(symbol, 400)` 读取官方日K，并提供已有交易日序列。宿主参数是自然日窗口，400不保证400根，实际数量单独显示。
- 「更新盘中尾部」仅在已确认交易日的连续竞价时段、由用户显式点击后，经 `ctx.api.klineMinute` 更新当前单股。当本地数据已够或非交易日，不自动请求Provider。不调用minute-batch规避历史限制。
- 09:30竞价不参与连续竞价分钟聚合；上午09:31–11:30、下午13:01–15:00分别对齐。首根15m结束09:45，下午首根13:15。按区间结束戳标注，延后60秒作为完成余量。缺分钟、冲突、空开盘价等区间留空；尚未完成的区间不画蜡烛。
- MA只使用同口径完整收盘序列，满n根才产生值。缺口中断计算和画线；日K日历不可用时在跨日处断开。界面同时显示总有效根数与末段连续根数。
- 窗口最多约320根15m或80根60m，因此60m MA233历史不足。实际本机股票样本仅1个交易日、16根15m，MA55/233按实标记不足，没有为了验收补市场历史。

内存缓存最多3个标的，按当前标的串行调度、跳过被后续选择取代的队列；官方封装不支持AbortSignal时，已发请求可结束，其过时响应不会覆盖新图。离页后销毁图表和缓存，丢弃未完成结果；没有轮询、行情数据库或新后台采集器。

## MA55 深链

示例：`/lan-kline?symbol=000006.SZ&period=15m&event=2026-11-06T15:00:00`。

事件只在对应完成K结束戳上标记，可点「定位信号」。超出实际本地历史时明确提示；不是当前级别结束点或存在缺口时不挪到附近K。切换标的会清除别股事件。

配套 MA55 **0.2.9** 只增加选中区域的「15 分钟 K」链接，并保留官方个股详情按钮。链接通过公开 `ctx.api.extPagesList` 确认工作台路径已启用且无错误后出现；未启用时显示说明。该修订的全部MA55后端源码与0.2.8相同，Worker状态里的0.2.8仍是后台代码版本。

需要构建MA55链接修订时单独执行：

```powershell
pnpm --dir addons/lan_ma55 build
& 'D:\Python_Envs\envs\nga\python.exe' -B -X utf8 addons/lan_ma55/build_backend.py
```

经官方入口导入 `addons/lan_ma55/dist/lan-ma55-0.2.9.zip`，重启后端并刷新；本机已完成。独立工作台本身不依赖该包，不读取MA55 SQLite。BUY来自独立QuantDash5m后台，图表来自TSP公开投影，两者输入及复权覆盖可能有差异，未声称逐根相同。

## 边界与恢复

**官方原生弹窗原位增加分钟K tab 尚未实现。** 已核对当前宿主和上游 `b8303417f18124aa541e39949affee41968ad468` 的运行时Page Pack契约，支持route/navigation；源码扩展的`stock-preview.footer`未向运行时包开放。后续需官方正式暴露可替换视图/插槽契约。更长分钟历史也需正式的严格本地读取契约，本轮未改宿主。

Page Pack v1没有内置历史版本列表。更新前保留旧JS和页面标识；需要恢复时在官方入口按相同标识导入备份并确认替换，刷新。本轮实际验证停用、卸载和备份恢复，MA55后台始终存活；最后再安装1.0.1。MA55旧包代码另有备份并由官方包管理器保留0.2.8回退版本。包、日志、截图和备份只在本机忽略的dist目录。

## 源码与依赖

`extension.mjs`只注册页面；`data.mjs`负责官方请求与会话缓存；`aggregate.mjs`负责时间与OHLCV聚合；`indicators.mjs`负责SMA和准确事件匹配；`chart.mjs`封装独立图表；`page.mjs`负责交互。依赖单向，不导入宿主内部组件或其它插件实现。

图表采用 [Lightweight Charts 5.2](https://tradingview.github.io/lightweight-charts/docs/api/functions/createChart)，标记使用其[公开接口](https://tradingview.github.io/lightweight-charts/docs/api/functions/createSeriesMarkers)。该依赖为Apache-2.0开源库，Copyright (c) 2026 TradingView, Inc.，来源为 [TradingView仓库](https://github.com/tradingview/lightweight-charts)。页面保留TradingView归属标志与链接，构建产物保留其版权声明。
