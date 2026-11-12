# 兰 K线工作台｜lan.kline 3.1.1

TSP v0.3.36 独立 Page Pack。页面沿用宿主主题变量、系统字体、卡片和紧凑控件，价格图与独立成交量 pane 为主体。

- 价格 pane 恢复六条真实 LineSeries：MA5 / MA10 / MA20 / MA30 / MA55 / MA233，沿用固定颜色，与蜡烛共用右侧价格尺度。每项图例按“名称、数值、checkbox”排列，窄屏自动换行。
- 六条均线初次默认全选，开关仅改变对应 series 的 visible，不触发行情读取、BUY 计算或蜡烛重载。`lan.kline.preferences.v2` 仅保存 period / visible / maVisible；合法 v1 均线设置可迁移。
- 均线直接读取已保存的权威字段。默认读最新完成 K，十字光标同步读所指 K，缺口显示 `—`，离开后恢复最新值；股票两位、ETF 三位。缺值、午休及跨交易日处断线，不补造时间点或均线值。
- 15m 全部可见历史 BUY 仅用价格 pane 的淡红时间列表示。复用 BuyColumns，成交量不重复染色；深链可定位，窗口外用一条短提示。30/60m 不产生 BUY 高亮。
- 已移除 BUY 箭头/文字、支撑标记、防守价线、静态最新价线及额外价格标签。正常十字光标保留。顶部长事件信息与风险 JSON 已移除，真实异常汇总为一条轻量提示。

后端仍通过 lan.ma55 本机 HTTP v1 读取持久行情和分页历史事件，经官方 Ext Data/16 槽需求桥发布。页面 IO 只经 ctx.api；源错误保留有效图表并显示原因。本轮后端仅同步版本字符串，没有修改生产或存储逻辑。

## 构建与运行

```powershell
pnpm --dir addons/kline_workbench test
pnpm --dir addons/kline_workbench build
& D:\Python_Envs\envs\nga\python.exe addons/kline_workbench/build_backend.py
& D:\Python_Envs\envs\nga\python.exe -m unittest addons.kline_workbench.tests.test_backend
```

将本机构建的 `dist/lan-kline-3.1.1.zip` 交给官方扩展管理器 inspect、候选前端加载校验、安装，正常重启后打开 `/lan-kline`。不迁移 MA55 数据库。

## 2026-11-12 实际验收

- 基线：个人分支 `feature/tsp-ma55-unified-db-stress-20261112`，HEAD `5bf7d949c43831dee7e7f17820a44b66bbcd0b2e`。本轮新分支 `feature/tsp-kline-ui-unified-20261112`。
- Node **37 通过 / 0 失败 / 0 跳过**；nga Python unittest **4 通过 / 0 失败 / 0 跳过**。前端构建、后端打包、改动 Python 文件 Ruff、`git diff --check` 均通过；ZIP 的 8 个文件与源文件/构建产物及官方安装目录逐字节一致。
- 官方管理器实际完成兼容检查、候选前端加载、安装和正常重启。安装/runtime 均为 **3.1.1**，pending 为空，previous 为 **3.1.0**，服务 READY，分钟 SDK 与 1m 请求计数均为 0。其它插件版本未变。
- 真实浏览器对照现有 lan.theme 页面及宿主图表实现，检查 000338.SZ、000001.SZ 的 15/30/60m，1366/900/430px、深浅主题、开关后刷新/换股、十字光标/离开、深链/窗口外、跨周末日期范围、缩放和平移。没有横向溢出、图例覆盖蜡烛或 checkbox 遮挡。浏览器无错误日志。
- 实际 15/30/60m 分别为 250/125/62 根；两只股票 MA233 有效点均为 18/0/0。实际悬停的六个显示值与对应权威 K 字段逐项一致。真实设备 DPR 约为 1；1.5/2 的几何行为由单测覆盖。
- 核心源码及全部其它插件相对本轮基线零差异；官方宿主基准为 v0.3.36（`a7868b783fc6057913bbc0a8d2b69817ad9b7d9b`）。MA55 算法、250 根容量、8/2 采集预算及官方 1s/3s 设置未变。两只验收股票的 source/updated_at/attempted 全部未变，未因打开图表重新采集。

**数据库静态验收未通过。** 19:54:45 的只读对比中，原有 45,073 条 signal_event 全字段保持且无删除；但既有 MA55 后台持续冷预热，新增 347 条 REPLAY，两表总数均为 45,420，另有 6 条旧 event_risk 被后台更新。风险更新时间与对应标的行情提交时间逐一相同，符合现有 writer 的风险更新路径。本轮未写库，也未为满足静态断言停掉生产或覆盖风险记录；不能报告“两表计数与全字段完全不变”。

以上为盘后验收。历史不足时 MA233 显示 `—`；旧源 BUY 与当前复权基准未核实的深链保留短提示。私人证据位于本机 `data/private/kline-ui-unified-20261112/`，包括截图、布局、读数、安装及只读数据库对比，均不入 Git。

回退：在官方扩展管理器对 lan.kline 选择回退到 previous **3.1.0**，正常重启并刷新页面；不回滚 MA55 数据库。构建产物、ZIP、商业行情、数据库、截图与日志不上传 GitHub。
