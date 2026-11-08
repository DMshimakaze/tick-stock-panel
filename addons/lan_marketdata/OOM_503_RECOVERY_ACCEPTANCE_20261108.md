# DuckDB OOM / 503 恢复验收｜2026-11-08

本机正式运行 **lan.marketdata 1.0.2、lan.kline 2.0.1、lan.ma55 0.3.2**。原DuckDB原位续跑，配置8GB、实际连接7.4 GiB、2线程；未删除、重建、重复导入或VACUUM。目标分支为`feature/tsp-shared-rolling-marketdata-ma55-r1-20261108`，个人Fork为`DMshimakaze/tick-stock-panel`，本轮起点为`245b59083977f92fa4821ce0494fe21c6e1bb0a9`。

## 原因和修复

开工现场/status为ERROR、writer_alive=false，异常为OutOfMemoryException（938.1/953.6 MiB）。配置确为1GB；机器仍有约61GiB可用RAM。故障后/inventory错误提示starting。300863.SZ原库已存160根15m，尚无其他周期。

- Store连接默认8GB，可私有配置整数1GB—32GB，16GB实测生效；非法值明确ERROR。/status从current_setting及duckdb_memory查询生效限制、线程、连接、缓冲使用量，保留成功水位、错误和重试状态。
- 网络仍最多100股、每股5000根、仅5m。单股事务和原schema/主键/因果聚合不变；指标先用完整真实输入计算，再按周期注册最多144/256根待保留尾部，避免先插入3072根5m再删除。临时注册finally释放，网络响应逐股释放。
- 每轮判空改为一条元信息读取。相同生产快照上，原全池解码RSS采样峰值约751MiB；新判空进程peak_wset约184MiB。此项只减少判空读取，正式全池分母保留。
- 单股OOM回滚后保持lease，关闭重开自身连接，等待5秒、15秒，最多2次本地重试，不重复HTTP。失败股的K/水位/ACK不提交；耗尽或初始化失败显示ERROR，/status仍可查，读取返回明确503。保留最后成功水位。未改insertion-order配置。
- 已知未预热股票也进入受限需求集合，重复合并；实时5m仍优先。历史调度优先查看股，并以同范围其他股票补足网络批量。
- 工作台已有K立即画；空503显示服务不可用和状态入口，保留有效图，恢复后清除错误。60m暂无K时明确保留已有15m图。股票名直达、原事件时点、价格基准和多页面隔离保留。

## 定向测试与资源测量

Python为现有nga，DuckDB1.5.5、QuantDash0.1.0；依赖未升级。以下测试/构建最终退出码均为0。

| 实际命令 | 结果 |
| --- | --- |
| `$PY -B -X utf8 -m pytest -q -p no:cacheprovider addons/lan_marketdata/tests_backend` | 27 PASS，0 FAIL/SKIP；含8项恢复回归、真实DuckDB OOM及两批100股×64交易日 |
| 调度调整后重跑test_resume.py、test_recovery.py的非大批用例 | 8 PASS；优先股与同范围股票共同批量，实时轮次优先 |
| `$PY -B -X utf8 -m unittest addons.kline_workbench.tests.test_backend` | 4 PASS |
| `$PY -B -X utf8 -m pytest -q -p no:cacheprovider addons/lan_ma55/tests_backend/test_extension.py addons/lan_ma55/tests_backend/test_shared.py` | 10 PASS，0 SKIP；只读冻结输入环境沿用，含拓维R1及滚动重启 |
| `pnpm --dir addons/kline_workbench test` | 23 PASS；含空503、恢复清错、部分K先画、空60m保留15m |
| 工作台pnpm build、两包build_backend.py | PASS；官方inspect/frontend/install及重启后runtime_version一致 |
| 相关ruff check、git diff --check | PASS |

原生产库快照的独立副本执行两批100股×3072根合成5m，包含重叠历史、增量、7周期写入、裁剪及MA233：旧实现78.2秒、RSS峰值397MiB；修复后73.3秒、RSS峰值391MiB，_write累计耗时11.0秒→6.9秒。该受控模拟本身在旧版也通过；生产OOM由现场状态证实。另以真实DuckDB内存不足异常验证未提交SQL回滚、旧水位/检查点不动、lease保留、重开后复用同一次网络结果；持续故障验证5/15秒后停止且不误ACK其他股。

## 正式原库运行

入口实际为127.0.0.1:3011（本机Vite代理到3018后端），按官方管理器先暂存公共1.0.2，再暂存工作台2.0.1并完整重启。上一版分别为1.0.1/2.0.0；MA55未重发，源码、R1内核与版本均未改。

- 约364秒、35次状态采样覆盖多轮**100股、2026-08-03—11-06的WARM历史批次**；30次采样处于100股WARM。全池FULL_MARKET和FULL_MARKET_BOUNDED_PAGES持续，writer/连接存活，无OOM、429或恢复重试。
- 修复前54次HTTP/48次5m，验收快照72次HTTP/66次5m，本轮新增18次均为5m。自有1m=0；不是把宿主合法1m归零。首次60秒交接等待按原预算执行，未改Provider配置。
- 监控期间整个后端RSS峰值4,818,898,944字节（约4.49GiB，含宿主、MA55及Python）；DuckDB缓冲采样最大349,753,344字节。缓冲每10秒采样，不是引擎瞬时峰值。8GB是预算上限，不是启动预分配。
- 300863.SZ /inventory及/bars均200；起始已有160根15m可读，按需补后实际为5m144、15/30/60m各256、120m128、daily64、2d32，均未超容量。60m MA233=38.52935622317597。
- MA55中点击“卡倍亿”名称，直接打开15m真实蜡烛、量、MA55/233、11/6 15:00原确认/支撑及32.25防守信息。000006.SZ实机15/30/60m均正常，60m MA233=6.920300429184549。深色桌面和600px窄屏截图已保存本机。
- 修复前22,449条signal_event的event_id、first_seen_at、payload、源版本逐条核对，变化/缺失0。验收快照总24,464条，新增2,015条均为R1/REPLAY。旧工作台5m14,400、1m4,800、日K1,200及5条请求审计均无新增/改写。
- 官方v0.3.32基线ca3a44252edb414e3f4c1df1b7c381bad9f55c8b对backend/app、frontend/src、dev.ps1差异为空；研究工作区及REVIEW_SUMMARY.md既有修改保持。

本轮为周日，**真实盘中未验**。全池深历史继续由现有有界队列补齐；缺K/缺因子仍按实际状态显示。

## 本机证据与回退

`%TEMP%/tsp-oom-recovery-20261108`保留停止writer后的一致rolling-before.duckdb、ma55-before.sqlite3、kline-before.sqlite3，及before-runtime.json、before-databases.json、JUnit、benchmark-old/fixed.json、metadata-profile-old/fixed.json、production-monitor.json、final-runtime.json、signal-protection-final.json和ui-*.jpg。数据库、行情、日志、截图、ZIP、凭据、SDK和用户任务文件均不上传。

回退时先停对应writer并另存当前数据，经官方管理器将公共包回退1.0.1、工作台回退2.0.0后重启，MA55仍0.3.2；不要覆盖或清空数据。1.0.1会恢复1GB限制，可能复现OOM，仅作为紧急隔离版本。原数据和本轮快照均保留。
