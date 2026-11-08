# 兰 K 线工作台｜lan.kline 2.0.1

/lan-kline只读展示15/30/60m。后端从lan.marketdata正式HTTP v1取得已保存K/MA，经lan_kline_demand → 轻量视图 → lan_kline_view → ctx.api.extDataRows显示。16个会话槽、client、请求序号和90秒租约隔离需求。

生产ZIP不含QuantDash collector或行情SQLite store。原workbench.sqlite3及1m/日K文件保留供离线导入和回退，正常运行不写。浏览器仅整理时间轴空白，不从5m重新计算权威OHLC/MA。

完整合法symbol深链直接读取，不依赖搜索；名称从公共元信息补齐。手工搜索和官方个股详情继续可用。BUY链接携带原event/setup/defense/factor，箭头落在原15m结束点；基准未证实一致时不叠加防守线，超出256根窗口明确提示。

Lightweight Charts保留蜡烛、量、十字光标、缩放、日期范围、TSP主题，使用公共MA5/10/20/30/55/233。预热、缺口、因子待补、限频/429及不可用状态可见，服务失败保留有效当前视图。localStorage仅保存界面偏好。

已有部分真实K立即绘制，不等待256根；60m暂无K时保留并明确标注已有15m图。503显示公共服务不可用及状态入口，已有图表保留，恢复后清除错误。回退至2.0.0使用官方管理器并重启；MA55保持0.3.2，公共库文件和旧工作台数据保留。

构建：pnpm --dir addons/kline_workbench build，再用nga运行addons/kline_workbench/build_backend.py。首次安装用Publisher.prepare()经官方管理API创建本包两张固定槽展示表，本机原表继续使用。公共包须先安装；会话认证设置见[公共行情说明](../lan_marketdata/README.md)。

验证：pnpm --dir addons/kline_workbench test；nga的-m unittest addons.kline_workbench.tests.test_backend。旧聚合/采集源码和REVIEW_HANDOFF.md保留为历史记录。实机数据、安装和回退见[三包集成验收](../lan_marketdata/SHARED_ROLLING_ACCEPTANCE_20261108.md)。
