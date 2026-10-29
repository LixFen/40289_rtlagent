# Agent 部署前检查 — 2026-10-17

结论：源码与本机工程回归通过，可以上传赛事实例做部署验证；尚无本次目标实例的
GPU/运行库/授权实测，不能标记为正式投题就绪。本次只评估 Agent 部署，基线保持赛事原样，
不以基线输出上限要求调整 Agent 上下文。

## 文档来源与镜像结论

已沿 `D:/AttendingCompete/README.md`、本项目 README 阅读：

- `../rtlagent2026/README.md`。
- `../rtlagent2026/docs/FAQ.md` Q6/Q7。
- `../rtlagent2026/docs/API_CONTRACT.md`，尤其第七至十节。
- `../rtlagent2026/docs/SUBMISSION.md`。
- `../rtlagent2026/example/Dockerfile`、`example/requirements.txt`、`example/serve/README.md`。
- `../fpgachina26-amd/tracks/rtl-hls-agent/GUIDE.md`。

本地参考仓库提交为 `f7a8bd465c2a820e006c7a00ecc7df468974635b`。
这些文档给出的赛事环境为：

| 项目 | 文档规定/说明 |
| --- | --- |
| 验证实例 | 单张 W7900 系列、48GB 显存；16核 CPU、55GB 内存 |
| 方案显存 | 仍按单卡32GB设计，48GB验证结果不能替代32GB验收 |
| 最终镜像架构 | 参考 Dockerfile 区分 gfx1100/W7900 与 gfx1201/R9700，二进制和 wheels 不能混用 |
| EDA | 完整 Vivado/Vitis 2026.1；目标器件 xczu3eg-sbva484-1-e |
| EDA 路径 | 从 XILINX_VIVADO/XILINX_VITIS 读取；文档示例 /tools/Xilinx/2026.1/Vivado |
| 持久盘 | 赛事 FAQ 描述 /workspace 为25GB持久盘，实例重启其他目录还原 |
| 临时盘 | /tmp 是4GB内存盘；EDA_TMP 和 AGENT_TMP 应设到 /var/tmp |
| 接入 | API 127.0.0.1:7860；模型 127.0.0.1:8000；仅暴露7860 |

参考 Dockerfile 的镜像名仍含 `REPLACE_WITH_ANNOUNCED_TAG`，本项目 `manifest.base_image`
也仍为 null：已知架构和能力要求，但没有可据此锁定的赛事镜像名称/digest。
应在赛事账号可见的镜像清单或公告中确认赛事专用镜像，记录实际 image ID/digest。

平台通用文档的 [Choose an image](https://amd-aim.github.io/radeon-cloud-docs/guides/images/)
描述默认 AMD OneClick Base 在2026年10月为 Ubuntu24.04、Python3.12、ROCm7.2构建的
PyTorch2.9.1，未承诺包含 Vivado。它不能据此认定为赛事 EDA 镜像，也不要把旧个人实例的
Python3.14/ROCm SDK10 配置当成新镜像默认值。
[存储文档](https://amd-aim.github.io/radeon-cloud-docs/guides/storage/)还说明临时 /workspace
与持久 /workspace 可选；创建时须确认选择了持久存储，不能仅凭路径名判断。

## 本次修复与检查结果

- `.dockerignore` 放行根目录 MODEL.md，以及更名后的 tools/serve_development.py。
  新回归在修复前分别复现两处 COPY 输入被排除的问题。
- EDA 环境白名单保留 FLEXLM_TIMEOUT，使赛事推荐授权连接超时设置能传到 Vivado；
  回归同时确认模型密钥和投题令牌不传给 EDA。
- 64K上下文/65535输出配置更新后本机完整回归115项通过；包含异步202、幂等、鉴权、任务期限、200次连续子进程请求、
  工具权限、测试冻结、候选保存、SSE恢复与进程清理。
- 官方 baseline 两文件与参考 checkout 字节匹配；9项固定 Ross 资产哈希通过。
- 最近一次真实 Windows Vivado 闭环为清理客户端后的验证：88.4秒、6轮、每轮2个调用，
  完成失败检测、补丁、原测试复验、lint、综合和提交。本轮未重复该测试；这不是本地模型准确率或Linux验收。
- 本机 Docker Linux 引擎未运行，实际镜像构建未执行。COPY白名单回归不是Docker构建成功证明。

部署前回归日志：`out/predeploy-20261017/regression.log`；64K配置回归日志：
`out/context-64k-20261017/regression.log`；65535输出配置回归：
`out/output-65535-20261017/regression.log`；预检结果：
`out/predeploy-20261017/preflight.json`；此前真实工具报告：
`out/http-only-20261017/vivado-smoke/report.json`。

## 平台上按顺序完成

1. 选择赛事专用镜像，先核对系统、权限、实际GPU/gfx、驱动/ROCm、磁盘与挂载、容器能力，
   再决定是否安装任何软件。不要重装/升级镜像内的 ROCm PyTorch。
2. 代码和已校验的GGUF放持久盘，模型为约11.8GB；避免同时保留多份下载临时文件挤满25GB。
   模型 revision、文件名和 SHA-256 以 manifest.json / model/MODEL.md 为准。
3. 直接从Windows上传源码时，先在项目根执行 `python3 -m serve.prepare_image`，
   它会规范化shell行尾并校验官方基线内容；不要求把参考答案仓库放进Agent目录。
4. 将非秘密配置和授权变量保存在 `/workspace/env.sh`。令牌由用户安全配置，文件权限设600；
   每次新实例/重启重新source。README中已有变量示例。
5. 沿用镜像的 XILINX_VIVADO；设置 XILINXD_LICENSE_FILE 和 FLEXLM_TIMEOUT。
   参考仓库给出的平台授权地址是 `2100@vivado-lic.fpgachina.svc.cluster.local`。
   检查 xvlog/xelab/XSim/lint/综合及ZU3EG真实任务，不能只查 --version。
6. 优先检查镜像自带 `/opt/llamacpp/llama-server` 的版本、动态库、GPU后端和实际生成。
   使用该镜像运行库时按 FAQ 设置 `ROCBLAS_TENSILE_LIBPATH=/opt/llamacpp/rocblas/library`。
   当前选定GGUF是否支持、原生tool_calls及启动参数是否兼容，必须实测。
7. 显式设置 LLAMA_SERVER、MODEL_PATH、MODEL_NAME、LLM_BASE_URL，采用 `serve/llamacpp.sh`。
   `serve/llamacpp-sdk10.sh` 保留的是旧W7900D的特定路径；不要在新镜像盲目运行。
8. 上下文和输出默认值只修改 manifest.json.context；输入和传输限制见 manifest.json.agent。
   用 `python -m agent.config` 查看有效配置，详见 [运行配置](configuration.md)。修改上下文后重新测显存，
   历史16K实测显存不能当作新配置结果。
   修改配置后重启模型与Agent服务，不能让启动器复用旧上下文的模型进程。
   完整流式归档设置 `AGENT_STREAM=1`，响应读取上限由统一配置提供。
   清除开发云桥的 LLM_BASE_URL 和 AGENT_REASONING_EFFORT=low 等实验残留，使用本机模型端点及声明配置。
9. 核实已分配卡对应的 GPU_DRM_CARD，再设置 GPU_ASSIGNED_COUNT=1；不能把宿主其他卡当作分配卡。
   用 `bash serve/entrypoint.sh` 启动。默认启动就绪超时300秒，日志在 `$AGENT_TMP/services/`。
10. 确认 health 的 ready=true、track=rtl、model正确、实测显存不超过32GiB。
    health不会实际执行Vivado；须完成第5步和真实模型自主解题才算整链可用。
11. 按 [隧道文档](https://amd-aim.github.io/radeon-cloud-docs/guides/tunnel/)及参考契约暴露7860，
    不暴露8000；本地和公网均核对鉴权与202/轮询。正式令牌应由赛事渠道提供。
12. 禁止外网后重新验证模型、工具、资料检索和平台授权；做连续任务、超时回收、僵尸进程、
    稳态/峰值显存与磁盘检查，再固定镜像、运行库、权重和源码哈希。

生产入口不依赖云端API。正式接题前必须清除开发用端点和配置；不得带官方测试台/参考答案进模型工作区。

## 仍待实际环境确认

赛事镜像名称/digest、目标GPU与ROCm/推理库适配、所选GGUF原生调用、Linux Vivado授权及
8GiB EDA地址空间限制适配、实际峰值显存、断外网许可、持续运行与重启恢复。
这些项目需要实例实测；文档阅读和Windows回归不能替代。
