# Sector-focused MA55 integration seed

This commit is a non-installed implementation seed on TSP v0.3.36, not a
runtime release or an acceptance result. Keep the original lan.ma55 0.9.8
manifest and runtime unchanged until the remaining integration is verified.

- Existing focus_pool.py and focus_controls.mjs are retained.
- ths_focus.py implements the documented THS catalog/member GET contract using
  an injected authenticated and budgeted request. A successful explicit edit
  fetches newly selected boards only; explicit/daily refresh also fetches
  retained boards. No-op saves and removals do not fetch constituents. Failed
  new boards commit nothing; failed retained boards keep their last good data.
  Real provider access, completeness and budget are not proven by mocks.
- multiframe_core/multiframe.py and structure.py are direct copies from
  DMshimakaze/DM-Ma55-Method commit 4440e1985151d7e03688e2b0a0ed33701789d40c.
  Original blob SHAs: multiframe.py b1f75615d0acdb3bd27e1efeda534addc2b622f3; structure.py f5922c5d7dfdaebb6d0f6a8c1dcf1b1071c7c41b.
  features.py contains only the verbatim minimal_features function from
  original features.py blob e682a304c493b1ff8c78a67980f51096056451ab, with its stdlib imports. No
  pandas/config/desktop dependencies and no runtime import from the research
  repo. Do not change thresholds or state transitions. Research version
  MA55_MULTIFRAME_R1_RESEARCH_V1 is not frozen production.
- ma55_levels.py is display-only: true per-period MA55, same-basis price,
  completed target proof, unavailable/stale distinct from zero, sorted upper
  levels; no other MAs, no SELL or cross-period BUY veto.
- The build allowlist includes the new files; the existing package is not
  rebuilt or installed by this seed. The synthetic research tests are copied
  with import adjustment only; optional private frozen-pack tests remain in
  research. Run them and the targeted application tests in Windows nga.

Remaining: wire verified THS transport and budget into a single plugin worker;
scope all queues/status and publications; build causal 15/30/60/120m/day/week
input with calendar, factor and gap evidence; persist versioned observations
and dedup events; connect focus controls, reuse the chart component under
lan.ma55, verify compatibility/deep links before disabling lan.kline.
No host source changes, no main changes, no raw data or credentials.
