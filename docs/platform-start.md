# 平台一键启动

重启后优先使用已实测的 `bash /workspace/40289-restore.sh`，会先离线恢复软件、
补配置、验证模型并保留或重建隧道，无需重新输入令牌。
步骤与保证边界见 [持久恢复](restart-recovery.md)。下文的 deploy 入口适用于软件已齐备的情况。

上传仓库并把指定 GGUF 下载、校验到持久盘后，在 Linux 执行：

```bash
cd /workspace/40289_rtlagent
bash serve/deploy.sh /workspace/40289.env.sh
# 或指定其他环境文件：bash serve/deploy.sh /path/to/env.sh
```

部署前把赛事令牌正文填入 `serve/fpga-token.local.txt`，一行，不加引号或变量名。
这是私有本地文件，不进入 Git、镜像或源码哈希；Git 上传后须单独复制到平台相同位置。
也可以预先配置 `FPGACHINA_TOKEN_FILE` 指向持久盘上的令牌文件。
已填写的文件优先于旧环境中的 FPGACHINA_TOKEN，避免沿用开发令牌；无文件时仍兼容环境变量。
无需在运行时输入，令牌修改后用 `bash serve/run_api.sh stop` 再部署。

脚本在存在时加载 `/workspace/env.sh`，规范化 shell 行尾并核验官方 baseline，检查模型
SHA-256、模型名称、推理程序和 Vivado 入口，然后复用 `serve/start.sh` 启动模型、
GPU 遥测和 EDA 清理服务。真实生成预热通过后，后台启动鉴权 HTTP 服务，确认
`ready=true` 才成功返回。所有后台进程脱离 SSH 会话，重复运行复用已启动服务。

2026-10-28 的独立公共环境模板为 `serve/platform.env.example.sh`，部署副本位于
`/workspace/40289.env.sh`，不再 source 旧 `/workspace/env.sh`，其中不含令牌正文。
实例重启会丢失 `/opt/slang`，持久盘已保留固定安装包；恢复时先运行：

```bash
source /workspace/40289.env.sh
python3 tools/install_slang.py /workspace/40289-deployment-20261028/slang-linux-v12.0.tar.gz --prefix /opt/slang/12.0
bash serve/check_image.sh
bash serve/deploy.sh /workspace/40289.env.sh
```

安装器先验证固定 SHA-256；无需重新输入令牌。重新安装 rc-tunnel、建立隧道及报送网址仍须另做。

SSH 会话可能缺少 Notebook 注入的 FRP_BROKER_URL 和 TMPDIR，直接执行官方安装器会失败。
可信部署辅助工具只继承所需的两项 Notebook FRP 配置，不输出配置值，并去除代理：

```bash
python3 tools/platform_tunnel.py install
python3 tools/platform_tunnel.py expose
python3 tools/platform_tunnel.py status
```

先确认鉴权健康检查就绪再 expose；该工具仅用于平台接入准备，不属于 Agent 求解工具。

默认首次读取整个模型并校验 SHA-256，将成功记录保存到
`$AGENT_TMP/services/model-sha256.json`。后续启动仅核对模型真实路径、设备/inode、
字节数、纳秒修改时间/状态变更时间及 manifest 预期哈希；完全一致时复用记录，
不再读取 11.8GB 权重。文件变化、记录损坏/丢失或实例重建后会自动完整重验。
这是可信本地文件的变化检测，不能保证发现存储静默损坏或有人同时篡改文件与记录。
下载后首次部署、正式冻结或怀疑文件损坏时，可强制完整校验：

```bash
AGENT_VERIFY_MODEL=full bash serve/deploy.sh
```

2026-10-18 在当前平台的 11.8GB 模型上测得：独立 `sha256sum` 为 12.89 秒，
启动程序完整校验为 7.26 秒（缓存状态不同），复用记录为 0.000104 秒。
这只是模型文件验证阶段耗时，不包含模型加载、预热和 HTTP 启动。
快速校验相关 7 项回归在平台 Linux 上通过；本次未改变服务运行状态。

环境文件应在源码仓库外，权限为 600，至少配置：

```bash
export XILINX_VIVADO=/tools/Xilinx/2026.1/Vivado
source "$XILINX_VIVADO/.settings64-Vivado.sh"
export XILINXD_LICENSE_FILE=2100@vivado-lic.fpgachina.svc.cluster.local
export FLEXLM_TIMEOUT=300000000
export EDA_TMP=/var/tmp/eda
export AGENT_TMP=/var/tmp/rtl-agent
export LLAMA_SERVER=/opt/llamacpp/llama-server
export MODEL_PATH=/workspace/models/Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf
export LLM_BASE_URL=http://127.0.0.1:8000/v1
export GPU_DRM_CARD=card2  # 必须按实际分配核对
export GPU_ASSIGNED_COUNT=1
export AGENT_STREAM=1
# 令牌预先填入 serve/fpga-token.local.txt，无需在这里 export。
```

保留镜像要求的 PATH、动态库与 ROCBLAS_TENSILE_LIBPATH 设置。模型名称和上下文
默认读取 manifest.json。更改模型上下文或进程环境后须先停旧服务再启动，脚本不会
自动替换存活的进程。令牌更改后也须重启 HTTP。

模型等日志和 PID 位于 `$AGENT_TMP/services/`：`model.log`、`telemetry.log`、
`eda-cleanup.log` 及同名 `.pid`。启动失败先查看这些日志；已启动的模型可能保留，
便于诊断和重试。脚本不安装软件、不下载模型、不自动开放公网。

```bash
source /workspace/env.sh
python3 tools/final_health.py
```

模型预热和 health 通过不能替代 Vivado 真实任务、工具调用兼容性、长上下文峰值
显存、连续投题及目标平台断网验收。公网接入在本地验收后手动执行 `rc-tunnel expose --port 7860`。

HTTP 通过 `serve/run_api.sh start|stop|status` 管理，守护 PID 和日志位于
`$AGENT_TMP/run_api/run_api.pid`、`$AGENT_TMP/run_api/serve_api.log`。
API 退出后等待 2 秒重启，关闭启动终端不影响服务；模型不会随 API 重启。
API 中断约一分钟，平台会永久收回原隧道；须重新 expose、重新报送网址。
实例重启后还须重新安装 rc-tunnel。守护只能恢复进程，不能恢复丢失的在途题目；评测失败不重投。

## 2026-10-18 平台实测

在 `/workspace/FPGAAgent`、W7900D/gfx1100、镜像自带 llama.cpp f114f91 上通过：

- 指定 GGUF 的 SHA-256 校验、真实生成预热及 HTTP `ready=true`。
- 未携带令牌的 health 请求返回 401。
- 第二次执行后 model/http/telemetry/eda-cleanup 的 PID 均保持不变。
- Linux 上 `test_startup.py` 9 项和 `test_deploy.py` 3 项全部通过，shell 语法检查通过。
- 64K 上下文预热后的两次显存读数为 15.31/15.54 GiB；不是峰值或长输入压力测试。

该镜像的总 `settings64.sh` 引用了未安装的 PDM，启动路径因此改为加载实际存在的
`.settings64-Vivado.sh`；`serve/start.sh` 在该文件不存在时仍兼容总入口。
本地 Windows 的既有启动测试遇到沙箱临时目录权限错误，上述 9 项已在远端 Linux
重新运行通过。未执行完整测试套件、真实自主投题或本轮 Vivado 功能验收。

平台环境文件已备份为 `/workspace/env.sh.before-agent-deploy-20261018-045354`。
当前令牌为远端生成的本地测试令牌，仅保存在权限 600 的 `/workspace/env.sh`；
正式投题前替换为赛事提供的令牌并重启 HTTP 进程。

## 启动耗时实测

2026-10-18，同一 W7900D 实例，使用 Python 单调时钟测量 `bash serve/deploy.sh`
从命令执行到脚本完成（包含真实模型预热、鉴权 HTTP health ready=true），不含 SSH 建连：

| 起始状态 | 耗时 |
| --- | ---: |
| 模型和 HTTP 均停止，本次触发完整 SHA-256 | 18.649 s |
| 模型和 HTTP 均停止，命中校验记录 | 6.006 s |
| 已有模型和 HTTP 存活，命中校验记录 | 0.420 s |

这里的冷启动指进程停止后重启，未清空 Linux 文件缓存，不代表实例重建后的首次启动。
另两次复用服务时因模型 ctime 与旧记录不同触发完整校验，分别为 7.759/7.727 s；
记录稳定后的快速路径如上。当前最终服务保持运行，最后 health 显存为 15.34 GiB。

计时前发现容器 PID 1 未回收已停止服务，旧的 kill(pid, 0) 会把僵尸进程判断为存活。
已修正 Linux 存活检测以排除 Z 状态，并在平台通过 `test_startup.py` 全部 10 项回归。
