# Script index

## Anonymous review release

| Script | Purpose |
|---|---|
| `run_release_smoke.py` | Run the no-network five-minute integrity check |
| `download_release_data.py` | Download, SHA-256 verify, and extract GitHub Release assets |
| `validate_release.py` | Scan repository/data identity, secret, path, and record-count invariants |
| `package_release_data.py` | Build deterministic review archives from the private source workspace |
| `build_release_examples.py` | Materialize four compact synthetic offline examples |
| `build_paper_results.py` | Rebuild paper-facing tables and figures from an explicit input root |

在仓库根目录运行 `python scripts/<name>.py`。各阶段独立执行和 review，不使用一键全
链路编排器。

## Scenario 与 trajectory

| Script | Purpose |
|---|---|
| `discover_scenario_tools.py` | 从 MCP server 获取实时工具 metadata |
| `build_scenario_tool_pool.py` | 用 discovery snapshot 构建 scenario 生成工具池 |
| `run_agent_scenario.py` | 运行一条 profile-backed scenario |
| `run_agent_scenarios.py` | 用隔离 trajectory 进程和批次级共享 MCP 池批量生成 trajectory |
| `serve_shared_mcp.py` | 将一个 catalog MCP server 暴露为批次内复用的本地 HTTP endpoint |
| `review_trajectory_dataset.py` | 汇总 trajectory 完成状态和工具调用质量 |
| `package_trajectories.py` | 将逐条 trajectory 流式整理为 canonical JSONL 并审计 |
| `benchmark_mcp_concurrency.py` | 测试 MCP 工具的可用性与安全并发 |
| `serve_agent_api.py` | 通过 FastAPI 暴露相同的 Agent runtime |

## Profile

| Script | Purpose |
|---|---|
| `analyze_profile_distribution.py` | 汇总 profile pool 的真实分布拟合情况 |
| `benchmark_profile_provider.py` | 测试 profile 生成 provider 的速率与稳定性 |
| `rebuild_official_ipf_targets.py` | 从官方来源派生数据重建 IPF targets |
| `resample_profile_pool.py` | 从现有候选池重新抽取校准样本 |

## 隐私实验

| Script | Purpose |
|---|---|
| `run_privacy_attacks.py` | 对 trajectory 运行 one-shot 或 two-stage 攻击 |
| `run_channel_experiments.py` | 比较 schema、server-view 和 trajectory channel |
| `run_view_scope_experiments.py` | 聚合四个 domain 的 profile-level cross-domain 攻击视角 |
| `run_privacy_recovery_watchdog.py` | 无进展时重启可恢复 runner，并刷新直连 HTTP 连接池 |
| `backfill_two_stage_failure_checkpoints.py` | 为既有的 two-stage 失败结果补齐不采样的 Stage 1 审计 checkpoint |
| `run_semantic_judge.py` | 判断未能确定匹配的结果并运行统一 semantic judge |
| `build_privacy_eval_records.py` | 将所有攻击臂规范化为固定 17 属性分母的长表 |
| `materialize_failed_evaluations.py` | 将耗尽的生成失败计入固定分母负样本 |

## 分析

| Script | Purpose |
|---|---|
| `analyze_attack_comparison.py` | 比较配对 attack mode |
| `analyze_channel_experiments.py` | 汇总隐私 channel 实验 |
| `analyze_semantic_asr.py` | 聚合并汇总统一 Semantic ASR |
| `analyze_semantic_model_comparison.py` | 比较不同 attack model 的结果 |
| `analyze_privacy_experiments.py` | 生成 Privacy Attack V1 的 Semantic ASR、配对比较和 bootstrap CI |

所有生成产物都写入 `artifacts/`，不写入 `docs/`、`config/` 或 `data/`。
