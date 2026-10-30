# FPGA 创新设计大赛 AMD RTL/HLS 本地智能体设计赛道——设计报告

赛道：RTL  
版本：2026 年 10 月 28 日

## 1. 方案概述

### 1.1 设计目标

本方案构建一个运行于 AMD 单卡平台的本地 RTL 智能体。智能体接收题目描述和模块接口，调用本地语言模型生成 Verilog，并通过接口检查、编译、仿真和综合反馈迭代修改，最终输出 `solution.v`。

本方案主要关注三个目标：

- **功能正确性：** 允许智能体自行构造测试台和断言，发现仅靠编译、综合无法识别的功能错误。
- **相对基线的改善：** 使用与裸跑基线相同的本地模型，通过工具反馈改善一次生成的结果。
- **工程可复现性：** 固定模型、推理程序及知识资源版本，记录运行轨迹，支持依赖预置和服务恢复。

当前完成了三道公开示例的单次对比和平台工程验证，尚未完成完整题集评估，因此暂不设定或宣称总体通过率及归一化增益。

### 1.2. 总体架构

```text
题目描述、模块接口
        │
        ▼
任务与接口契约 ──────── 技能与本地知识
        │                    │
        └─────────┬──────────┘
                  ▼
             本地语言模型
                  │
             工具调用请求
                  ▼
        工具执行与工作区管理
                  │
       ┌──────────┼───────────┐
       ▼          ▼           ▼
    接口检查   编译与仿真   lint 与综合
       │          │           │
       └──────────┼───────────┘
                  ▼
          结构化证据与错误反馈
                  │
                  └────► 模型继续修改
                              │
                         提交候选结果
                              ▼
                  solution.v、运行轨迹
```

### 1.3 设计依据

方案提供轻量接口检查、功能仿真和综合工具，让智能体根据当前问题选择验证手段。

工具执行由程序控制。模型通过结构化工具调用读写工作区、运行检查及提交结果，宿主程序负责参数校验、资源限制和执行记录。

## 2. 模型与推理栈

### 2.1 最终选用的模型

| 项目 | 内容 |
|---|---|
| 模型 | `ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF` |
| 位置 | Hugging Face, ModelScope 同名仓库 |
| 来源与版本 | https://modelscope.cn/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF; commit:`fc62412a020e0c1ee3a8f9a12f10aea2102478f0` |
| 权重文件 | `Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf` |
| 量化方式 | GSQ-RCO IQ3_S |
| 文件大小 | 11,771,546,784 字节 |
| 上下文窗口 | 262,144 token |
| Agent 单次输出上限 | 16,384 token（与官方 baseline 一致） |
| 思考模式 | 开启（`xhigh`） |
| RoPE 外推 | 未配置 |
| 实测显存 | 28.245 GiB |

权重 SHA-256：

```text
64b53b64c7aa39f20a7e54bd80582fe595b1d745624ee8a72e92508c0326d810
```

上述显存峰值仅覆盖已执行的测试，尚不能证明所有输入、输出组合均满足显存预算。历史 65,535 token 配置并未完成长输出稳定性验证；当前 Agent 与官方 baseline 均设为 16,384 token。

### 2.2 评估过并放弃的候选

| 候选模型 | 放弃原因   |
| -------- | ---------- |
| MiniCPM5-2B   | 虽然能很好的体现Agent对模型的提升，但其无法实际体现完整的解题能力 |
| Qwen3.8-Flash-Next-GSQ-RCO   | 太大，能力强却由于显存限制无法容纳完整模型 |

### 2.3 是否微调

否。本方案使用公开量化权重，未进行额外训练或微调。

## 3 推理栈

| 项目 | 内容 |
|---|---|
| 推理后端 | llama.cpp，ROCm 构建 |
| 版本 | `0.4.0-dev-f114f91` |
| ROCm SDK | `10.0.0` |
| 服务入口 | `serve/llamacpp.sh` |
| 模型访问方式 | 本机 HTTP 接口 |
| 部署方式 | 单卡本地推理实例 |

选用该推理栈的直接依据是：当前 GGUF 权重仅对llama.cpp有较好适配。

## 4. 智能体设计

### 4.1 控制流

一次解题包含以下过程：

1. 读取公开题面和接口，建立独立工作区。
2. 组装任务上下文，加载 RTL 设计规则与工具说明。
3. 调用本地模型，由模型提出文件操作或验证请求。
4. 执行接口检查、编译、仿真、lint 或综合，返回结构化结果。
5. 模型依据反馈修改 RTL 或自行生成的测试台。
6. 模型提交候选结果，或在预算结束时由程序结束任务。

当前配置最多允许 24 次模型调用、64 次动作和 24 次工具运行；实际执行还受题目墙钟期限及工具预算限制。这些上限不意味着每道题都会执行到上限。

### 4.2 重试与回退策略

方案支持修改已有文件，无须每次重新生成全部代码。模型可以修补 DUT，也可以修复测试台，但两类修改均保留执行记录。

宿主程序维护候选结果和验证证据，在期限内尽可能返回已有产物。已验证通过的阶段只代表对应检查的结论，不能直接替代官方功能判定。

目前观察到的主要问题是：模型可能连续修补自行生成的测试台，消耗较多时间，却未同步改善 DUT。因此，重试次数和有效进展之间仍需进一步建立约束。

### 4.3 上下文管理

上下文以题面、接口契约、候选代码和工具证据为核心组织，避免将完整 EDA 日志反复加入模型输入。

仿真失败产生的反例会保留，直到相同测试台重新通过，减少模型在后续修改中遗漏已知错误的可能。

后端上下文窗口为 262,144 tokens，但 Agent 请求仍有 65,536 字符输入限制。

### 4.4 自我验证

智能体可以根据题面自行编写测试台、添加断言并运行仿真，也可使用编译、lint 和综合检查。

自行生成的测试台具有局限：模型可能误解题意，也可能同时在 DUT 和测试台中写入相同错误。

## 5. 技能包

### 5.1 技能清单

| 技能或资源 | 作用 | 使用条件 |
|---|---|---|
| RTL 设计规则 | 提醒接口、时序、复位和可综合性约束 | 设计与修改 RTL 时 |
| 自主工具使用规则 | 约束文件操作、验证与提交行为 | 解题全过程 |
| RTL 诊断资料 | 帮助解释常见工具错误 | 编译、仿真或综合失败时 |
| 固定版本 Ross 规则与解析器 | 提供部分诊断规则和解决线索 | 对应日志或规则适用时 |

### 5.2 提炼过程

技能内容围绕接口不符、工具报错、时序语义和测试台构造等问题组织。

### 5.3 有效性验证

当前已验证技能加载、工具调用和相关解析功能可运行

## 6. 实测结果

### 6.1 测试环境

| 项目 | 配置 |
|---|---|
| GPU | AMD W7900D，约 48GB |
| 系统 | Ubuntu 24.04.4 |
| Python | 3.14.7 |
| EDA | Vivado 2026.1 |
| 综合目标 | `xczu3eg-sbva484-1-e` |
| 题目范围 | 官方公开 selftest 示例, RTLLM 47题 |
| 采样次数 | 每题 Agent、Baseline 各一次 |

参考实现均先在可信评测中达到 L3。模型工作区未包含官方参考实现或官方测试台。

### 6.2 方案与基线对比

| 题目 | Agent | Baseline |
|---|---|---|
| `ex01_popcount8` | L3 | L3 |
| `ex02_detect_1101` | L3 | L1 |
| `ex03_lfsr8` | L3 | L0 |

Baseline 使用官方原样程序。第二题失败表现为 226 次采样中出现 4 次不匹配；第三题未通过编译或展开阶段。

### 6.3 pass@1 与 pass@5

未完成题集上的多次独立采样

### 6.4 墙钟

| 题目 | Agent | Baseline |
|---|---:|---:|
| `ex01_popcount8` | 66.18 秒 | 6.01 秒 |
| `ex02_detect_1101` | 511.10 秒 | 12.01 秒 |
| `ex03_lfsr8` | 444.83 秒 | 12.02 秒 |

### 6.5 结果分析

在本轮示例中，Agent 的最终结果优于或等于基线，但时间开销明显增加。后两题存在反复修复自行生成测试台的情况，说明当前工具循环能够得到可用结果，但验证效率仍有较大改善空间。

另外，后端完成了 262,080 token 合成输入、16 token 输出的窗口测试，设备显存峰值约 28.223 GiB.

## 7. 复现说明

### 7.1 构建与依赖准备

按现行提交规则，无需自行构建该平台内部镜像

```bash
python3 -B tools/build_image.py --source-only
docker build --target production -t rtl-agent:production .
```

模型为 `ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF`，固定 revision 为 `fc62412a020e0c1ee3a8f9a12f10aea2102478f0`，文件为 `Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf`

```bash
python3 tools/download_requested_model.py --output-dir /workspace/models
printf '%s  %s\n' \
  64b53b64c7aa39f20a7e54bd80582fe595b1d745624ee8a72e92508c0326d810 \
  /workspace/models/Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf | sha256sum -c -
```

下载脚本使用 ModelScope 的固定版本地址并完整校验哈希。容器中默认从 `/models` 只读挂载，平台示例配置使用 `/workspace/models`。来源和许可证见 [模型声明](model/MODEL.md)。

### 7.2 启动推理服务

```bash
source /workspace/40289.env.sh
bash serve/start.sh
```

`serve/start.sh` 就绪后返回，重复执行复用已有进程。常驻进程脱离单题进程组。官方容器重建路径在依赖和权重预置后，执行该入口，再逐题调用 `run.sh`。

远程投题路径还需提前将令牌写入私有 `serve/fpga-token.local.txt`，或用 `FPGACHINA_TOKEN_FILE` 指定预置文件。令牌不提交、不写入镜像，也不在解题期间交互输入。随后执行：

```bash
chmod 600 serve/fpga-token.local.txt
bash serve/deploy.sh /workspace/40289.env.sh
python3 tools/final_health.py --output /var/tmp/40289-health.json
```

部署入口执行软件与权重检查、真实模型预热，并启动 API 守护；健康检查应返回 `ready: true`。若使用外部令牌文件，上述 chmod 应改为该文件路径。公网接入使用 `python3 tools/platform_tunnel.py ensure`，并从实例外验证鉴权健康接口；隧道地址变化后需重新报送。

模型日志及 PID 位于 `$AGENT_TMP/services/`，API 守护日志位于 `$AGENT_TMP/run_api/serve_api.log`。修改配置不会自动替换存活进程；模型参数变化需重启模型，API 环境变化需重启 API。API 重启会丢失内存中的任务状态，更新前应确认没有在途题目。

### 7.3 单题运行与基线运行

输入目录必须包含 UTF-8 `prompt.txt`，模块名和端口在题面中；正式 RTL 题不提供 `interface.txt`，本方案允许该文件缺省。下例使用独立目录中的官方公开示例，只复制题面，不复制参考答案或测试台：

```bash
cd /workspace/40289_rtlagent
source /workspace/40289.env.sh
source serve/config.sh
export DEADLINE_S=900
mkdir -p /var/tmp/40289-repro/input
cp /workspace/rtlagent2026/tasks/ex01_popcount8/prompt.txt \
  /var/tmp/40289-repro/input/prompt.txt

./run.sh /var/tmp/40289-repro/input /var/tmp/40289-repro/agent
./run_baseline.sh /var/tmp/40289-repro/input /var/tmp/40289-repro/baseline
```

两个命令须串行运行，并使用同一个已启动的本地模型服务。每个输出目录应包含 `solution.v`、`trace.jsonl`；解题失败时可能为空，不能只凭退出码或文件存在认定通过。Agent 还保存候选及工具证据。`baseline.py` 和 `run_baseline.sh` 保持赛事方原样，基线单次生成、不调用工具或技能；双方从 manifest 读取同一套思考设置。

远程投题使用 `POST /v1/solve`，收到 job ID 后每两秒查询 `/v1/jobs/<job_id>`，直到 `done` 再投下一题。通信失败不代表原任务已结束，应继续核对原 job，避免后续任务排队消耗时限。官方以投题到查到 `done` 的墙钟计时，排队时间也计入；单题默认 900 秒。

### 7.4 自测复现

工程检查在源码目录执行：

```bash
python3 -B -m unittest discover -s tests -v
python3 -B tools/preflight.py
python3 -B tools/build_image.py --source-only
python3 -B tools/freeze.py --output /var/tmp/40289-source-hashes.json
python3 -B tools/freeze.py --output /var/tmp/40289-source-hashes.json --check
```

分级判定使用官方参考仓库 `fc8e26a5828b7222e9db439cee29128bcf4f8865`，放在 Agent 工作区之外的 `/workspace/rtlagent2026`。预先准备该仓库后，加载相同运行环境并执行：

```bash
source /workspace/40289.env.sh
export LLM_BACKEND=openai
export DEADLINE_S=900
cd /workspace/rtlagent2026/selftest
bash run_selftest.sh --agent /workspace/40289_rtlagent --check
bash run_selftest.sh --reference --timeout 900 \
  --out /var/tmp/40289-selftest-reference
bash run_selftest.sh --agent /workspace/40289_rtlagent --baseline \
  --samples 1 --timeout 900 --out /var/tmp/40289-selftest-paired
```


### 7.5 已知的环境依赖

- **推理运行库：** llama.cpp 二进制依赖配套 ROCm SDK 动态库、符号链接和内核数据；仅复制 `llama-server` 不足以复现。须保留匹配的 `PATH`、`LD_LIBRARY_PATH`，以及运行库需要的 `ROCBLAS_TENSILE_LIBPATH`。
- **EDA 与授权：** 需要 Vivado 2026.1、ZU3EG 器件支持，以及 xsim/综合可签出的许可。平台模板使用内部浮动许可地址；内网 DNS 与授权服务可达性须符合赛事网络约定，不能将其等同外部模型服务。
- **slang：** 固定 v12.0，安装清单在 `serve/slang-release.json`，通过 `SLANG_BIN` 或 `/opt/slang/12.0/bin/slang` 调用。若实例重建后缺失，需在准备阶段用固定归档及 `tools/install_slang.py` 恢复。
- **磁盘与缓存：** EDA 工程及运行临时文件放本地 `/var/tmp`，不放 NFS；持久归档位置由 `RUN_ARCHIVE_DIR` 配置。权重默认 `/models`，平台可覆盖到 `/workspace/models`；模型、运行库和知识资料都须预置。
- **离线与恢复：** `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1` 不能代替网络隔离。已验证缺失软件恢复和模型冷启动，尚未完成真实整机重启与完整 IP/UDP 隔离验收；恢复包不能替代基础系统、驱动、Python 或 Vivado。

## 8. 失败分析

### 8.1 已观察到的失败与效率问题

| 现象 | 结果或影响 | 原因判断 |
|---|---|---|
| Baseline 序列检测结果错误 | 停留在 L1 | 可信测试台发现逐拍行为不符，具体语义原因需结合代码继续分析 |
| Baseline LFSR 示例失败 | L0 | 未通过编译或展开 |
| Agent 反复修改测试台 | 两题耗时超过 400 秒 | 测试台语法、字符串及字符写法错误消耗修复轮次 |
| 近窗口输入处理缓慢 | 单次约 20 分 49 秒 | 可用上下文容量不等于期限内可用的处理能力 |

### 8.2 未达到预期的工程尝试

初次限制网络时，只允许模型和许可证端口，真实 XSim 仿真出现 PrivateChannel 通信失败。补充临时 TCP 端口范围后，实际仿真和综合通过。

### 8.3 方案边界

当前主要边界包括：

1. 自建测试台可能遗漏题意，无法提供完整功能证明。
2. 工具循环可能在测试台修复上消耗过多预算。
3. 大上下文能够容纳输入，但处理时间可能超过单题期限。
4. 当前验证不足以证明数百道连续请求的稳定性。
5. 完整网络隔离与独立镜像重建仍待验证。

## 9. 创新点总结

当前可供进一步验证的设计特点包括：

1. **任务、接口契约与工具证据共同驱动修复。** 模型能够修改代码并根据真实 EDA 反馈继续决策。
2. **保留仿真反例。** 已知失败证据在相同测试台重新通过前持续保留。
3. **将部署一致性纳入交付检查。** 模型、固定知识资源和源码归档均进行身份或哈希核对。

以上属于已实现的设计特点。是否形成稳定的解题增益，仍需完整题集和消融实验支撑，当前不将其表述为已证实的性能创新。

## 10. 结论与展望

### 10.1 达成度

方案已在 AMD 平台完成本地模型生成、工具调用、真实 EDA 检查及三道公开示例验收，也完成了近窗口容量和离线软件恢复验证。

完整题集能力、技能独立贡献、长输出稳定性及完整网络隔离尚未得到充分验证。

### 10.2 后续改进方向

优先开展完整题集对比，并依据运行轨迹统计时间消耗。重点分析测试台修复占比、无效重复动作和最终结果改善之间的关系，再决定是否调整重试与预算策略。

随后补充技能消融实验及平台侧完整网络隔离验证。

## 11. 参考文献

1. 赛事官方参考仓库 `rtlagent2026`，固定提交 `fc8e26a5828b7222e9db439cee29128bcf4f8865`。
2. 模型仓库 `ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF`，固定 revision 见第 3 节。
3. llama.cpp，构建提交 `f114f91`。
4. AMD Ross 技能资源，固定提交 `2cdc9eef1b6b5b17fa45f5e4d468e5483a1c92de`。
5. Vivado 2026.1 工具文档。

正式提交时应补齐各项来源链接、准确版本和引用位置。

## 12. 大模型协作记录

### 12.1 所用工具

开发过程中使用大模型辅助编写代码、分析日志、梳理规则和整理文档。

### 12.2 设计特点的来源

基于我学习到的大模型工作原理来进行开发。

### 12.3 采纳并验证的建议

固定模型 revision 与哈希、将实际 EDA 工作目录放入本地盘、对服务恢复和交付归档进行独立验证，均已通过相应工程检查。

### 12.4 未被测试支持的建议或判断

将有限 TCP 白名单测试视为完整断网验收的判断不成立；将近窗口容量测试视为长上下文解题能力验证的判断也缺少依据。最终报告分别限定了这些测试所能支持的结论。

### 12.5 验证与判别

个人验证时采用代码审查、自动回归、真实模型调用、Vivado 编译仿真综合、独立官方测试台判定和设备遥测交叉验证。
