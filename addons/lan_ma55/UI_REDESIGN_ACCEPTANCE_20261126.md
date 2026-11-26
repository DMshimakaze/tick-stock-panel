# MA55 工作台改版验收

2026-11-26，TSP v0.4.1，`lan.ma55 0.13.2` 候选版

## 实现

- `/lan-ma55` 首屏显示四项监控指标、真实运行状态和六周期股票矩阵。关注管理独立打开，沿用东财行业树、成分与反查、关注草稿和迁移确认。冻结历史按股票、代码、时间和模式展示，并保留原事件定位。
- 原 worker 通过官方 Ext Data 发布独立运行投影。在 A/B/C/F/D/W 取数执行前发布阶段，计算时切换状态，结束后清除批次。覆盖使用当前关注修订、分类代和完成周期，行情、分析和结果发布各有自己的水位。浏览器只在结果变化或手动刷新时重读矩阵。
- 与 TSP v0.4.1 的宿主核心差异为零。TickFlow 原生 15m 和日线、请求预算、六周期规则及冻结归档沿用原实现。

## 验证

| 验证项 | 结果 |
| --- | --- |
| `pnpm --dir addons/lan_ma55 test` | 16 通过，0 失败，0 跳过 |
| `nga` 定向 pytest | 84 通过，0 失败，0 跳过 |
| `pnpm --dir addons/lan_ma55 build` 和 `build_backend.py` | Vite 构建及 ZIP 出包成功 |
| 官方扩展管理器 | 预检、前端预览和安装均返回 HTTP 200，隔离宿主重启后运行 0.13.2 |
| Edge，100% 缩放 | 1440×900 明暗各 10 行，1366×768 8 行，640×900 的横向滚动在股票表内 |

定向 pytest 使用下列文件，路径相对于仓库根目录。

```powershell
& 'D:\Python_Envs\envs\nga\python.exe' -B -X utf8 -m pytest -q -p no:cacheprovider `
    addons/lan_ma55/tests_backend/test_dc_taxonomy.py `
    addons/lan_ma55/tests_backend/test_focus_pool.py `
    addons/lan_ma55/tests_backend/test_workbench_owner.py `
    addons/lan_ma55/tests_backend/test_sector_observation.py `
    addons/lan_ma55/tests_backend/test_extension.py `
    addons/lan_ma55/tests_market/test_live_status.py
```

隔离浏览器使用 56 只关注股的快照，核对三级行业、题材长原因及分页、股票反查、关注组、别名预览及撤销、六周期图表和冻结确认定位。运行阶段通过本地受控 SDK 请求与带标识的浏览器场景验证。截图和安装回执留在本机。生产运行版本为 0.12.0，非空池盘中持续取数仍待实盘验收。
