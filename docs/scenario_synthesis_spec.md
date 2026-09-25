# Scenario 合成规范

## 数据规模

- 输入：`artifacts/profile_pool/profiles.jsonl` 中的 1,000 条 profile。
- domain：`travel`、`health`、`shopping`、`career_learning`。
- 输出：每个 profile 在每个 domain 下生成一条任务，共 4,000 条。
- 正式 case：`artifacts/scenario/cases.jsonl`。
- 生成轨迹：`artifacts/scenario/generation_traces.jsonl`。

## 生成原则

每个 `profile × domain` 交给一次 profile-conditioned planner。planner 读取完整
profile、domain 边界和候选工具 schema，然后判断当前候选集合能否支撑一个自然、连贯
的用户任务。不能支撑时允许拒绝并重新抽样；达到最大尝试次数仍失败时记录失败，不为
凑齐数据而硬造任务。

scenario 合成允许任务失败，也允许后续 Agent 无法完成任务。这里不设置追求高成功率的
额外规则，只保留保证文件可消费的结构检查。没有独立 reviewer/check 阶段。

候选工具与 planner 给出的 tool roles 仅用于让任务有现实能力依据，并写入生成轨迹供
审计。它们不是 Agent 的工具白名单。运行 trajectory 时，Agent 始终看到该 domain
配置的全部 MCP server 发现出来的完整工具目录。

## 正式 case

`cases.jsonl` 每条记录只保留交给 User Simulator 的运行契约：

```json
{
  "scenario_id": "S0001",
  "profile_id": "P0001",
  "domain": "travel",
  "user_task": {
    "goal": "...",
    "context": "...",
    "constraints": ["..."],
    "expected_result": "..."
  },
  "evaluation_criteria": ["..."]
}
```

正式 case 不包含 `selected_tools`、`tool_roles`、bindings 或任何 Agent 可见工具裁剪
信息。`evaluation_criteria` 由自然语言任务确定性生成。

## 生成轨迹

`generation_traces.jsonl` 以 `scenario_id` 对齐，保存：

- 每次抽到的 candidate tools 与 planner 决策；
- 最终 tool roles、required input bindings 和 planner 理由；
- model、seed、候选采样概率和最大尝试次数。

这些信息用于检查任务是如何被构造出来的，不进入 User Simulator 的自然语言任务，也
不约束 Agent 的运行时工具。

## 运行与恢复

当前 scenario 模型为 `deepseek-v4-flash`。API 请求使用两把 key 的 500/100 RPM
配额，最大在途并发不超过 100；调用模型 API 时不使用代理。

```bash
python -m privacy_trace.scenario_generator \
  --n 1000 \
  --input artifacts/profile_pool/profiles.jsonl \
  --output artifacts/scenario/cases.jsonl \
  --trace-output artifacts/scenario/generation_traces.jsonl \
  --tool-pool config/scenario_tool_pool.json \
  --candidate-tool-probability 0.25 \
  --max-task-construction-attempts 8 \
  --api-key-indexes 1,2 \
  --api-key-rpms 500,100 \
  --workers 100
```

长任务可使用 checkpoint 与 `--resume`。恢复时只复用与当前输入、模型、prompt、seed
和采样配置一致的 case/trace 对；损坏记录会隔离，未完成记录继续生成。每个阶段完成后
先人工 review 数据，再手动进入下一阶段，不由一键全链路脚本自动推进。
