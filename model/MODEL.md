# Model declaration — 2026-10-23

Latest 2026-10-28 boundary stress: 32768+16 and 262080+16 tokens both completed,
without truncation or prompt-cache reuse. Near-window wall time was 1249.161s
and device peak 28.222519GiB. This validates capacity, not a short task deadline
or stability under the older 65535-token output cap. Complete network isolation is still unverified;
the TCP-only acceptance limits are in docs/offline-context-validation-20261028.md.

2026-10-28 update: real-model official selftest Agent results were L3 on all three
public examples. Across 2639 device samples, peak VRAM was 28.244739532470703GiB
on the assigned 48GB W7900D, below the 32GB scheme budget for these requests.
The model slot reported context 262144; this was not full-context input stress.
See docs/platform-validation-20261028.md for timing and acceptance limitations.

Selected model: ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF.
Public source: https://www.modelscope.cn/models/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF.
Base model: Qwen/Qwen3.8-27B; declared license Apache-2.0; no team fine tuning.

File: Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf, 11,771,546,784 bytes.
Revision: fc62412a020e0c1ee3a8f9a12f10aea2102478f0.
SHA-256: 64b53b64c7aa39f20a7e54bd80582fe595b1d745624ee8a72e92508c0326d810.
Quantization: GSQ-RCO mixed precision IQ3_S, declared average 3.50 bpw.
No vision projector is needed for text-only RTL. Weights are not included in Git.
Prepare and hash-check them before offline use, then mount read-only at `/models`.
The platform copy is `/workspace/models/` followed by the filename above.

Current platform backend: `/opt/llamacpp/llama-server`, version 0.4.0-dev,
commit f114f91, ROCm SDK 10.0.0. Start with `serve/llamacpp.sh`.
Loaded SDK libraries were observed under the Python site-packages
`_rocm_sdk_devel/lib` directory. A binary alone is insufficient: preserve its
runtime libraries, symbol links and kernel data. The official base image digest
is now pinned. On 2026-10-28 its platform software passed checks in a clean
process environment without the previous private env.sh; slang 12.0 was restored
using the pinned installation recipe. Historical b11552/Ubuntu 22.04 paths are
not current dependencies. This does not claim an independently rebuilt container.

Agent and unchanged official baseline share the local model service. Context:
262144 tokens; current Agent output ceiling: 16384, matching the official baseline cap;
older boundary tests used the previous 65535-token configuration. Thinking enabled as of 2026-10-29.
Agent and baseline declare enable_thinking=true and reasoning_effort=xhigh in
manifest.json. serve/runtime.json preserves thinking history and the shared server
template keeps preserve_thinking=true. The server uses reasoning-budget=4096, reasoning-format=deepseek
and reasoning-preserve. The Agent replays reasoning_content within its retained
three tool turns; complete turns still expire under the existing input limit.
The upstream base-model card recommends temperature=1.0, top_p=0.95, top_k=20
for thinking. The Agent now uses these recommended values, min_p=0,
presence_penalty=0 and repeat_penalty=1. The unmodified official baseline still
sends its fixed temperature=0 and top_p=1; Agent and baseline sampling differs.
Thinking is capped at 4096 tokens per completion for both Agent and baseline.
The xhigh effort remains enabled; the cap may end thinking before it finishes naturally.
Thinking still shares the existing total output and wall-clock budgets.
The 2026-10-28 selftest and context measurements above used thinking disabled and
do not validate the newly enabled mode. The Agent request is defined in
agent/llm/client.py; shared thinking settings are declared in manifest.json.
There is no cumulative token ceiling; each request must fit the model context,
and time, model-call and tool budgets still apply. Token reservation uses the
local chat template/tokenizer, with an explicitly labelled estimate fallback.

On 2026-10-23 health reported approximately 28.12 GiB on the W7900 development
instance with context configured to 262144. This is a point sample, not peak
acceptance. A previous 70030-token probe observed 27.751 GiB peak; it does not
validate the whole context window. The newer near-window test above is now available;
hundreds of diverse real requests, clean container and complete network-isolation
acceptance remain pending. Latest rules use a 48GB W7900-series/gfx1100 instance
for both rounds while limiting the submitted scheme to 32GB VRAM (FAQ Q6).

Earlier 16384-token measurements and b11552 tests remain historical evidence in
`docs/w7900d-validation.md`; they do not substitute for current acceptance.

# MODEL.md

> 模板。所填内容须与 `manifest.json`、`REPORT.md` 第 2 节一致。
>
> **本文件未如实填写时，增益项 40 分不得分**（评分细则 2.4）。

---

## 1. 模型

| 项           | 内容                                    |
| ------------ | --------------------------------------- |
| 模型名称     | ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF  |
| 来源         | https://modelscope.cn/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF    |
| 版本号 / SHA | `fc62412a020e0c1ee3a8f9a12f10aea2102478f0`               |
| 权重校验和   | `sha256: 64b53b64c7aa39f20a7e54bd80582fe595b1d745624ee8a72e92508c0326d810`  |
| 参数量       | 27B                                      |
| 许可证       | Apache-2.0                              |

**必须是可本地部署的开源权重。** 评测时方案须断网运行，商用 API 在评分细则 2.1 与
2.3 两条上均不成立，全部运行类得分无法产生。

## 2. 量化与显存

| 项           | 内容                        |
| ------------ | --------------------------- |
| 量化方式     | GSQ-RCO |
| 量化工具与版本 | GSQ-RCO 内部工具链 |
| **实测显存占用** | `28 GB`  |
| 测量方式     | `rocm-smi --showmemuse`     |
| 测量硬件     | W7900                       |

**上限单卡 32 GB，不允许跨卡。** 云实例的 W7900 有 48 GB，但规则统一按 32 GB 计，超出的
运行不计入统计（评分细则 2.2）。

参照：32B 模型 FP16 约 64 GB（装不下），4-bit 量化后约 18 GB；7B 模型 FP16 约 14 GB。

## 3. 上下文配置

| 项                 | 值    |
| ------------------ | ----- |
| `max_model_len`    | 262144 |
| 单次输出上限       | 16384 |
| RoPE 外推方式      | 无  |
| 思考设置           | `xhigh`（`manifest.json` → `generation`：`enable_thinking: true`） |

**本项不受统一约束，但必须如实声明。** 思考设置须与 `manifest.json` 的 `generation` 段一致，
`baseline.py` 按那里的声明发请求，方案也须使用同一套设置。

上下文长度对结果的影响是一阶的，不是可以含糊带过的参数。已有评测记录到：同一推理
模型在 8 K 输出窗口下因截断表现很差，放宽到 16 K 后通过率大幅跳升，而截断样本仅占
5.31%；另一项工作中统一的 4096 输出上限静默截断了七个受评模型中的三个，放宽后有
模型从第五名升至第一。

**基线运行必须使用与方案完全相同的上下文配置**，否则方案与基线不可比。

## 4. 推理栈

| 项目 | 内容 |
|---|---|
| 后端 | llama.cpp，ROCm 构建 |
| 版本 | `0.4.0-dev-f114f91` |
| ROCm SDK | `10.0.0` |
| 关键启动参数 | `serve/llamacpp.sh` |
| 模型访问方式 | 本机 HTTP 接口 |

## 5. 微调

否

## 6. 辅助模型

无

## 7. 声明

- [x] 上述模型为可本地部署的开源权重，非商用 API
- [x] 实测显存占用（含辅助模型）不超过 32 GB，单卡运行
- [x] `run_baseline.sh` 使用与 `run.sh` 完全相同的模型、量化方式与上下文配置
- [x] 若经微调，权重已开源且链接可达
- [x] 本文件内容与 `manifest.json`、`REPORT.md` 一致
