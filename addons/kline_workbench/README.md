# 兰 K线工作台｜lan.kline 3.2.1

TSP v0.3.36 独立 Page Pack。页面沿用宿主主题变量、系统字体、卡片和紧凑控件，价格图与独立成交量 pane 为主体。

- 价格 pane 恢复六条真实 LineSeries：MA5 / MA10 / MA20 / MA30 / MA55 / MA233，沿用固定颜色，与蜡烛共用右侧价格尺度。每项图例按“名称、数值、checkbox”排列，窄屏自动换行。
- 六条均线初次默认全选，开关仅改变对应 series 的 visible，不触发行情读取、BUY 计算或蜡烛重载。`lan.kline.preferences.v2` 仅保存 period / visible / maVisible；合法 v1 均线设置可迁移。
- 均线直接读取已保存的权威字段。默认读最新完成 K，十字光标同步读所指 K，缺口显示 `—`，离开后恢复最新值；股票两位、ETF 三位。真实缺格点或缺值处断线；合法午休、跨日、周末和假日保持连续。
- 15m 全部可见历史 BUY 仅用价格 pane 的淡红时间列表示。复用 BuyColumns，成交量不重复染色；深链可定位，窗口外用一条短提示。30/60m 不产生 BUY 高亮。
- 已移除 BUY 箭头/文字、支撑标记、防守价线、静态最新价线及额外价格标签。正常十字光标保留。顶部长事件信息与风险 JSON 已移除，真实异常汇总为一条轻量提示。

后端仍通过 lan.ma55 本机 HTTP v1 读取持久行情和分页历史事件，经官方 Ext Data/16 槽需求桥发布。页面 IO 只经 ctx.api；源错误保留有效图表并显示原因。后端转发服务端 `ma_history`、raw_tick 与版本证据；证券和指标的唯一数据源为 lan.ma55。

## 构建与运行

```powershell
pnpm --dir addons/kline_workbench test
pnpm --dir addons/kline_workbench build
& D:\Python_Envs\envs\nga\python.exe addons/kline_workbench/build_backend.py
& D:\Python_Envs\envs\nga\python.exe -m unittest addons.kline_workbench.tests.test_backend
```

将本机构建的 `dist/lan-kline-3.2.1.zip` 交给官方扩展管理器 inspect、候选前端加载校验、安装，正常重启后打开 `/lan-kline`。不迁移 MA55 数据库。

## 2026-11-12 完整均线验收

lan.ma55 在同一 SQLite 保留默认1,280根 raw 15m，从完整同源历史聚合30/60m、投影真实因子并计算六SMA，最后裁剪250/125/62根。冻结策略继续最多250根输入。工作台不重算指标，缺少基础数据时显示具体状态。

本轮 K线 Node 39项、三个页面共60项通过；后台全矩阵、滚动1,280根、重启、真实股票/沪深ETF、数学对照、压力和安装结果见 `docs/diagnostics/TSP_MA_Complete_ETF_Compact_20261112.md`。上一轮250根库的18/0/0个MA233是已修复基线。

当前发布组合为 lan.ma55 0.6.3、lan.kline 3.2.1、lan.theme 1.0.7，宿主为官方v0.3.36。原BUY和风险逐行核对保留；新增真实REPLAY事件另计。后台深历史由既有owner低优先级维护，页面读取不创建SDK或重复全量补拉。

任务前版本为 lan.ma55 0.5.2、lan.kline 3.1.1、lan.theme 1.0.5；按报告中的安装状态快照恢复，先正常停机并另存本轮新增状态。官方管理器的 previous 可作快速回退，完整步骤及本机一致性快照位置见报告。ZIP、商业输入、数据库、截图与日志只存本机。
