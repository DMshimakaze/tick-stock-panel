# 兰 MA55 R1｜lan.ma55 0.3.2

生产路径：公共lan.marketdata HTTP v1完成15m → pipeline.process_symbol → engine.minimal_features/advance → 原SQLite → 官方lan_ma55_summary/day/history。页面只展示结果，股票名称直接打开15m并携带真实确认信息。

唯一BUY规则MA55_15M_MINIMAL_V5_R1，研究源DMshimakaze/DM-Ma55-Method@5d1d0f6e8913f528110c93e054429f050b9851f6。状态revision4与物理schema1分开。30/60m用于观察，页面不计算BUY。原collector、预算和旧页面算法保留为历史源码，不进入生产ZIP。

信号库仍为context.data_dir/user_data/lan_ma55/state.sqlite3。跨版本重放保留event_id、源版本、首次时间和payload；旧工作状态先归档，缺足够真实历史的旧锁进入VERSION_HISTORY_INSUFFICIENT。有真实离开/失效证据后可重建。历史重放标REPLAY，同一确认时间跨版本不重复。

缺公共包、认证失败、未就绪或版本不符时，显示DATA_SOURCE_UNAVAILABLE并保留已保存结果。v0.3.32自定义路由不接受read:ext Token；有密码时，消费者私有settings提供合法api_session。api_token可继续用于官方Ext Data发布，不发到网页。

构建：pnpm --dir addons/lan_ma55 build，再用nga运行addons/lan_ma55/build_backend.py。先安装公共包，再安装本包与工作台，重启并刷新页面。首次安装须用Publisher.prepare()经官方管理API准备本包三张展示表；本机现有配置继续复用。

R1逐根/冻结验证在tests_backend/test_engine.py、test_shared.py；Python oracle固定上述研究提交，可用只读MA55_REFERENCE_REPO或MA55_REFERENCE_ARCHIVE，并挂载MA55_FROZEN_PACK/MA55_TUOWEI_INPUT。旧记录见QUANTDASH_PRO_ACCEPTANCE_20261108.md、RUNTIME_ROOT_CAUSE.md，均为历史版本。

完整验收和回退见[三包集成验收](../lan_marketdata/SHARED_ROLLING_ACCEPTANCE_20261108.md)。
