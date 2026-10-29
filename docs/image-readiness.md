# 镜像与冻结就绪清单（2026-10-28）

规则依据：rtlagent2026 `fc8e26a5828b7222e9db439cee29128bcf4f8865`（1026-commit）。
官方目标为 W7900/gfx1100、32GB 显存、Vivado 2026.1，器件 xczu3eg-sbva484-1-e、时钟 5ns。
旧环境记录不代表最终验收。

## 已准备的源码

- Dockerfile 与 serve/runtime.json 固定官方基础镜像
  `fpgachina-rtlhls@sha256:a7cf627e11708e8a6dcd9de689ff934f7b450b79369ebc7bb0217e8b54d81e56`。
- 官方镜像仅平台内部可访问，队伍无需自行 docker build；赛事方仅在退路模式重建。
  本地无 Docker 引擎不再列为提交阻断。
- 固定 slang 12.0、归档 SHA-256，构建时执行真实最小 RTL lint。
- `serve/check_image.sh` 检查软件、动态库和 Workspace API，不代替 GPU/授权验收。
- 公共环境由 `serve/env.sh` 提供；模型默认挂载 /models，EDA 使用 /var/tmp。
- `serve/run_api.sh start|stop|status` 管理独立 API 守护；API 退出后等待 2 秒重启。
  entrypoint.sh 前台运行同一守护；模型等常驻进程由 start.sh 启动。
- 令牌预先填写到 `serve/fpga-token.local.txt`，启动自动读取，无交互输入。
  文件不进入 Git、镜像或源码哈希；也可通过 FPGACHINA_TOKEN_FILE 选择持久文件。
- 源码自检核对官方镜像、参考提交、Baseline、模型身份、工具和 Ross 资源。

## 本地检查

```bash
python -B -m unittest discover -s tests -v
python -B tools/preflight.py
python -B tools/build_image.py
python -B tools/freeze.py --output out/source-hashes.json
python -B tools/freeze.py --output out/source-hashes.json --check
```

build_image.py 默认仅检查提交输入和官方 digest，不要求 Docker。
--build 才检查 Linux Docker 引擎并执行可选构建。
权重单独准备，固定 ModelScope revision 并验证 SHA-256，不在运行阶段下载。
详见 [容器配置](container-environment.md) 与 [冻结步骤](freeze-readiness.md)。

## 平台待验收

后续已完成近窗口 262080+16 token 压力（28.223GiB）及离线软件包恢复、
空目录冷载模型和一键恢复。TCP 端口限制下实际运行通过，但 IP/UDP 和完整 netns 隔离仍未验收。
详见 [新记录](offline-context-validation-20261028.md) 与 [恢复入口](restart-recovery.md)。

2026-10-28 已提供平台并完成 212 项 Linux 回归、三道官方 selftest Agent L3、
真实工具链、超时恢复与生产 API 崩溃恢复；本轮显存峰值 28.245GiB。
下表中的历史证据以 [最新验证记录](platform-validation-20261028.md) 为准；
完整 IP/UDP 隔离、长输出及数百道多样真实请求仍待验收。

| 项目 | 当前证据边界与待办 |
| --- | --- |
| 推理运行库 | 当前 f114f91 + ROCm SDK 10.0.0 在干净进程环境检查及真实生成中通过；保留已解析和实际加载库哈希 |
| 冷启动/CLI 退路 | 重启后的实例从候选源码恢复模型及 API，start.sh 幂等；独立重建容器仍未验证 |
| EDA | 最终候选注册工具编译、展开、三项断言仿真、lint、ZU3EG 综合通过；三道官方参考及 Agent 均 L3 |
| 离线授权 | 无外网下载依赖，内部浮动许可按赛事网络约定签出；历史实例无法执行隔离网络验收 |
| 显存 | 原自测峰值 28.245GiB；后续 262080+16 token 近窗口压力完成，峰值 28.223GiB；长输出/多样持续请求仍待验证 |
| 稳定性 | 已验证六次 Agent/Baseline、超时后接题和 API 异常恢复；数百道多样真实请求仍未验证，守护不恢复丢失任务 |
| 正式接入 | 40289_rtlagent 私有仓库、默认分支、赛事方协作者、正式令牌、隧道及端点报送尚待确认 |

不能把源码检查、开发机测试或旧实例记录写成上述平台验收通过。
API 中断约一分钟会导致隧道永久失效，需重新 expose 并报送；实例重启后还需重新安装 rc-tunnel。
