# 兰 K线工作台｜lan.kline 3.1.0

TSP v0.3.36 独立 Page Pack。后端通过 lan.ma55 本机 HTTP v1 读取持久行情和全部可见历史事件，再经官方 Ext Data/16槽需求桥发布。无个人行情 SDK、SQLite 或采集器。

- 主图只画蜡烛和联动量柱。MA55/233 仅作为图外数值；已移除均线管理控件、localStorage 均线回灌及全部 MA LineSeries。
- 15m 的每个历史 BUY 时间列使用 #ff7383、0.08 alpha 背景及箭头；支撑测试为次要标记。公开 series primitive 每次绘制读取当前时轴，随缩放、平移、DPR、主题及尺寸同步。
- 十字光标保留原确认、首次记录、支撑、防守、规则版本、event_id 和风险。深链定位一条确认，同时保留其他历史 BUY。窗口外数量明确提示；30/60m 只展示聚合行情。
- 防守价线要求原事件与当前图表的因子、来源和基准修订核实一致；更正后保留历史文字。
- 15m≤250、30m≤125、60m≤62；30/60m MA233 无值，缺口留白。页面不重新计算 BUY。

`pnpm --dir addons/kline_workbench build` 后由现有 nga Python 执行 build_backend.py，官方管理器安装重启后刷新 /lan-kline。自定义 HTTP 使用宿主面板会话认证；页面 IO 仍只经 ctx.api。源错误保留有效图表并显示原因。

Node tests、Python tests/test_backend.py 以及本机视觉验收覆盖事件分页、深链、背景时轴、窄屏和主题。商业截图只保存在私人目录。旧 workbench 文件留在回退归档，回退使用 previous_version 与统一库一致性快照。
