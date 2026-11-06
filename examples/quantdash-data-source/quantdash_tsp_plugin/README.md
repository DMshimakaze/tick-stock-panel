# QuantDash（个人订阅）× Tick Stock Panel

这是一个 **TSP v0.3.31 外部 Python 数据源插件**，与 MA55 无关，不修改 TSP 服务层或前端。遵循 TSP 的 `plugin_api_version: 1`，可以通过「设置 → 导入与备份」上传 ZIP。

`1.0.1` 修复指数日K和分时误用股票量价检查的问题：按 TSP 传入的
`asset_type="index"` 保留原始成交量、成交额，不将成分股成交均价与指数点位比较。
股票和 ETF 继续执行原有量价检查。已在 TSP v0.3.29 验证。

`1.1.0` 开放全量分钟：复用 TSP 正式的 `get_minute` 当日修复轮契约，
通过 QuantDash 已授权的批量 1m K 线读取原始价，由 TSP 的
`MinuteRefreshService` 负责全市场枚举和落盘。每批最多 100 个代码，
与其他请求共用 12 次/分钟预算。TSP v0.3.29 设置间隔上限为 120 秒，
整轮串行执行，全市场一轮约 5 分钟，无法提供每分钟全市场实时刷新。
本插件没有廉价全市场增量端点，因此状态显示 `repair_only=true`。

`1.1.1` 修复真实分钟记录约 `1e-14` 的 OHLC 浮点解码误差：
仅当相对误差不超过 `1e-12` 时，将高低价设为四价的最大/最小值，
保留开收价、成交量和成交额；实际越界的 OHLC 继续报错。

`1.1.2` 修复个人订阅五档访问模式：当前 Key 的单标的 `/v1/depth`
实际可用，`/v1/depth/batch` 则因不含 batch 权限返回 403。
通过逐股 `depth.get` 聚合满足 TSP 的 `get_depth_batch` 契约；
每股消耗一次 HTTP 请求，与分钟、实时等共享 12 次/分钟预算。

## 支持范围

- `daily`：QuantDash `klines.batch(period="1d", adjust="none")`，分批返回原始日线；显式量价契约，分批流式导入。
- `minute`：QuantDash `klines.batch(period="1m", adjust="none")`；最多 100 代码/批、12 天/请求窗口；北京墙钟 naive 时间戳。
- `realtime`：QuantDash `quotes.get(universes="CN_Stock")`；分数制涨跌幅、实时指数可用时可补拉。
- `adj_factor`：QuantDash `klines.ex_factors`，仅验证接口形态；**请先验证 ex_factor 是事件因子还是累积因子，再开启实际路由**。
- `depth5`：QuantDash `depth.get` 逐股读取后聚合；当前个人订阅的单标的模式已真实验证，数量按手保留。
- `full_minute`：TSP 通过现有 `get_minute` 当日窗口接管全市场分钟落盘，采用修复轮。

## 先决条件

- TSP 版本至少 0.3.24（验证源码为 0.3.31）。
- Windows TSP 开发版后端：通常 `E:\tick stock panel\backend\.venv\Scripts\python.exe`，**必须用这个 Python 安装 QuantDash SDK，不能只安装在 MA55 的 nga 环境**。
- `quantdash==0.1.0`；TSP 自己已有 `polars` 和其他依赖。
- 用户自己的 QuantDash API Key，建议通过 TSP 插件卡片填写；或使用现有环境变量 `QUANTDASH_TOKEN`。

PowerShell （在 Windows 中，先启动已有 TSP 虚拟环境；下方命令不会修改 MA55）：

```powershell
$root = 'E:\tick stock panel'
$py = Join-Path $root 'backend\.venv\Scripts\python.exe'
if (!(Test-Path $py)) { throw 'TSP backend .venv not found; run dev.ps1 once first.' }
uv pip install --python $py 'quantdash==0.1.0'
& $py -c 'import quantdash; print("QuantDash SDK import OK")'
```

## 安装与启用

1. 启动 TSP。打开 **设置 → 导入与备份 → 安装数据源插件**，选择 `TSP-QuantDash-Plugin-v1.1.2.zip`（ZIP 根目录必须直接是 `plugin.yaml`）。
2. 在新增 **QuantDash（个人订阅）** 卡片填写 Key。先探后存；不要在 Git 或聊天中提交 Key。
3. 在数据源详情中分别点击「试拉」：先日 K（`600519.SH`）、分钟 K（`600519.SH`）、实时行情。一个数据集通过再路由一个，不要首次就开启所有能力。
4. 在 TSP 设置中将 **实时轮询间隔设置为不少于 30 秒**，检查是否有 429、空页、单位告警。默认本插件请求预算 `TSP_QUANTDASH_RPM=12`；仅约束 TSP 进程内部请求，其他进程不共享。
5. 日 K 路由切到 `quantdash_user` 后先仅同步少数股票短历史，核对开高低收、成交量与金额，再扩大范围。分钟 K 同理。
6. 因子与五档暂保留默认原数据源，直到实际量纲及口径确认。完成有效测试后，可以分别切换，随时可回退。

## 验证

- 不需要 Key 的离线测试：运行 `PYTHONPATH=<解压后的源码根目录> python -m pytest -q tests`。测试使用假 Polars/SDK，只检验数据映射和适配器的 Python 协议，不等于实盘请求成功。
- 使用真正 Key、TSP 安装完插件以后，运行源包中的 `tsp_quantdash_live_smoke.py`，它通过 TSP 正式插件注册表取数，默认检查 daily/minute/realtime，不改路由、不写行情数据。

```powershell
$root = 'E:\tick stock panel'
$py = Join-Path $root 'backend\.venv\Scripts\python.exe'
& $py 'D:\Downloads\tsp_quantdash_live_smoke.py' --tsp-root $root
```

> 替换脚本路径为你保存的实际路径。确认返回 `SUMMARY: PASS` 后，再启用对应数据源路由。

## 数据契约注意

- TSP 成交量单位为 **手**，成交额单位为 **元**，涨跌幅为 **小数**。
- 本插件依据现有 MA55 QuantDash `5m` 原始采集代码，对 QuantDash A 股 `volume` 暂采用**手**口径，不做自动猜测。要求用实际 1m/1d/quotes 的成交额与价格核验：`amount / volume / 100` 应与真实成交均价一致。如果大范围不一致，插件拒绝异常批次；必须查明真实单位后修改映射并重测。
- QuantDash compact K 线中若缺 `amount`，插件保留 NULL，不伪造；相应 TSP 均价/金额指标可能不可用，需要实际补充可靠来源。
- 复权价格必须 `adjust=none`，复权因子单独路由；**不要把 QD 前复权 K 线再次送入 TSP 复权管道**。
- 日内分钟时间是 Asia/Shanghai 的 naive `datetime`，不能用 UTC 或服务器本地时区代替。
- 插件的 HTTP hook 计数按 QuantDash 0.1.0 SDK 的私有 transport 属性实施；升级 SDK 需重测。无 hook 时退化为高层调用计数，并在日志告警。
- `quantdash` SDK/行情服务受 QuantDash 单独许可协议约束，不将 SDK 或商业行情数据打包公开分发。

## 回退

在 TSP 的数据源能力路由把已切换项改回先前来源即可。插件不改动 MA55 代码/数据库；外部安装使用 TSP 的持久化插件目录，可单独停用或卸载。未经测试不要清理 TSP 正式 Parquet 数据。
