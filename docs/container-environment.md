# 镜像与平台环境配置

`serve/env.sh` 是不含密钥的公共环境入口，由 `serve/config.sh` 自动加载。
模型启动器、`serve/start.sh`、HTTP 入口以及 `run.sh` 都使用该入口；`serve/deploy.sh`
仍先加载仓库外的私有环境文件（默认 `/workspace/env.sh`），再补公共默认值。
所有显式环境变量优先，不覆盖基础镜像已有的 PATH、XILINX_*、动态库或部署覆盖值。
启动时先沿用当前授权，真实综合预置极小设计；失败后才尝试官方浮动地址
`2100@vivado-lic.fpgachina.svc.cluster.local`，缺省 FLEXLM_TIMEOUT 为 300000000。
两次尝试均失败则启动失败并保留诊断，不把模型就绪当成 EDA 就绪。
每次综合硬超时最多 30 秒，可用 LICENSE_PROBE_TIMEOUT_S 缩短。相同实例、工具和配置下
成功结果缓存复用，单题入口只读取选择结果，不重复综合；修改许可或工具后重新探测。
该地址不是授权文件或密钥；正式隔离网络是否保留许可通路仍须实际验证。

## 公共默认值

| 变量 | 未配置时的默认值 |
| --- | --- |
| MODEL_PATH | `/models/` 加 manifest.model.file；权重须事先准备并只读挂载 |
| LLAMA_SERVER | 优先使用 PATH 中的 llama-server，否则 `/opt/llamacpp/llama-server` |
| INFERENCE_START | 当前仓库目录下的 `serve/llamacpp.sh`，不依赖旧仓库绝对路径 |
| SLANG_BIN | 优先使用 PATH 中的 slang，否则 `/opt/slang/12.0/bin/slang` |
| EDA_TMP / AGENT_TMP | `/var/tmp/eda` / `/var/tmp/rtl-agent` |
| RUN_ARCHIVE_DIR | `$AGENT_TMP/rtl-runs` |
| RUN_ARCHIVE_MB | 8192 MiB；保留证据，不自动删除。按题量和可用盘空间调整 |
| GPU_TELEMETRY_FILE | `$EDA_TMP/rtl-gpu.json` |
| GPU_DRM_CARD | 从实际可见的 `/dev/dri/cardN` 中选择唯一 AMD 卡，不枚举宿主未分配卡 |
| GPU_ASSIGNED_COUNT | 实际可见 AMD card 节点数；无卡或多卡时不伪装为单卡就绪 |
| PORT | 7860，绑定地址仍为 127.0.0.1 |
| HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE | 1；这不是网络隔离验收的替代品 |

若未提供 LD_LIBRARY_PATH 且当前 Python 的 site-packages 中存在
`_rocm_sdk_devel/lib`，使用该 SDK 库目录；已有值原样保留。
ROCBLAS_TENSILE_LIBPATH 未配置且 `/opt/llamacpp/rocblas/library` 存在时才使用该目录。
这些默认值不会下载、安装软件，也不保证任意基础镜像具备相应运行库。

只有 render 节点、多个可见 card 节点或特殊设备映射时，须显式设置正确的
GPU_DRM_CARD 和 GPU_ASSIGNED_COUNT。没有 GPU 时可以检查配置，但就绪检查不会报通过。

## 容器使用

Dockerfile 的 `slang-build` 阶段复制随源码提供的 `vendor/slang-linux-v12.0.tar.gz`，
由 `tools/install_slang.py` 根据 `serve/slang-release.json` 校验固定 SHA-256 后安装，无需联网下载。
构建过程执行版本检查和真实最小 RTL lint；失败则构建失败。运行镜像只复制安装目录，
不复制下载归档、安装脚本或 smoke RTL，并设置 SLANG_BIN=/opt/slang/12.0/bin/slang。

```bash
docker build --build-arg BASE_IMAGE='<官方镜像>@sha256:<digest>' --target production -t rtl-agent .
```

离线包约 3.69 MB，进入构建上下文与源码哈希清单；校验失败即停止构建。运行时不安装、不下载。
官方基础镜像已固定为 Dockerfile 和 serve/runtime.json 中的 digest，镜像只在平台内部可访问。
按 2026-10-26 规则，队伍无需自行 docker build，由赛事方在退路模式重建；
Dockerfile 中的安装步骤仍须与实例实际执行步骤一致。这里的 Docker 命令仅用于可选开发验证。

复制 `serve/container.env.example` 到仓库外的私有文件，填写部署覆盖值。
这是 Docker `--env-file` 的 `KEY=VALUE` 格式，不是 `source` 使用的 shell 脚本。
比赛令牌预先填入一行文本文件，使用 `FPGACHINA_TOKEN_FILE` 指定只读挂载位置；
默认读取仓库 `serve/fpga-token.local.txt`。文件不进入 Git、镜像和源码哈希清单。
令牌、私钥、真实授权文件及整个 `/workspace/env.sh` 都不要复制到镜像。

```bash
# IMAGE 为基于官方基础镜像构建的方案镜像；由赛事方在退路模式重建。
docker run --init --rm \
  --network host \
  --device /dev/kfd --device /dev/dri \
  --env-file /private/rtl-agent.env \
  --env FPGACHINA_TOKEN_FILE=/private/fpga-token.txt \
  --mount type=bind,src=/private/fpga-token.txt,dst=/private/fpga-token.txt,readonly \
  --mount type=bind,src=/prepared/models,dst=/models,readonly \
  --mount type=bind,src=/prepared/rtl-runs,dst=/var/tmp/rtl-agent/rtl-runs \
  IMAGE
```

预先创建 `/prepared/rtl-runs`，保留容器退出后的证据；该目录应有足够空间且可写。
此示例面向 Linux 引擎，使用 host 网络让宿主上的 rc-tunnel 能访问绑定 127.0.0.1 的服务。
若使用隔离网络，需在容器内部运行隧道客户端或采用平台指定接入方式；仅加 `-p` 无法
转发到只绑定容器 loopback 的监听。host 网络本身不限制外网，离线验收仍需独立配置网络策略。

该命令使用 Dockerfile 默认 HTTP 入口。正式平台的设备映射、GPU 访问组与授权条件仍按
官方镜像配置核对，不能据此声称已经通过容器或断网验收；公网入口另按 rc-tunnel 文档配置。

容器重建退路里，运行 `bash serve/start.sh` 后再执行 `bash run.sh TASK OUT`。
`run.sh` 会重新加载公共环境，因此不依赖前一个 shell 进程的 export 能否传回调用者。
官方 `run_baseline.sh` 和 `baseline.py` 保持逐字节不变；模型端点和生成参数仍由 manifest
和容器环境共同确定，与 Agent 共用同一推理进程。

## 平台使用

原命令 `bash serve/deploy.sh /workspace/env.sh` 保持可用，既有 MODEL_PATH、LLAMA_SERVER、
SLANG_BIN 和 GPU 卡号不被覆盖。准备干净镜像的 env-file 时不要沿用
`INFERENCE_START=/workspace/FPGAAgent/...`，留空让程序按新仓库位置选择。

检查公共默认值：`python3 -m serve.environment`。它只打印白名单配置，不打印比赛令牌、
云 API key 或授权信息。启动后仍需真实模型生成、Vivado 最小任务、显存和离线验收。
