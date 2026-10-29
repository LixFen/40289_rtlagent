# 实例重启后的离线恢复（2026-10-28）

常规重启保留 `/workspace`，根文件系统（包括 `/opt` 的额外改动、`/root`、`/var/tmp`）
会回到平台镜像初始状态。GPU card 编号、进程、临时目录和隧道均不能沿用旧状态。
这里提供的是持久入口的一条命令恢复，没有假设平台会自动执行持久盘脚本。

## 重启后执行

```bash
bash /workspace/40289-restore.sh
```

入口转到源码内 `tools/restore_platform.sh`，依次：

1. 用基础系统 Python 校验并恢复离线软件包；只补缺失文件，同名不同版本会中止。
2. 保留已有 `/workspace/40289.env.sh`；文件缺失时从公共模板重新生成。
3. 加载 Vivado、已恢复的 native SDK 库路径，重新检测实际 GPU card。
4. 自动读取 `serve/fpga-token.local.txt`；检查软件、固定资源和模型 SHA-256，再真实预热。
5. 启动/复用模型和 API 守护，最后幂等恢复隧道。

不会要求重新输入比赛令牌，不执行应用层 pip 安装，不升级系统 ROCm/Torch。
当前 active 隧道只检查状态，不重新 expose；重启后旧隧道失效时，才申请新地址。
**平台发出的新地址仍须重新报送赛事方，脚本无法保证重启后保留原网址。**

仅恢复和检查软件、不启服务/隧道：

```bash
bash /workspace/40289-restore.sh --software-only
```

尚未注册云平台开机钩子。若平台提供自定义启动命令，可配置为上述持久入口；
在未实际验证该平台功能前，不声称“实例启动后完全自动恢复”。

## 哪些内容在哪里

| 内容 | 位置与恢复方式 |
| --- | --- |
| 源码 | `/workspace/40289_rtlagent`；另有本地 Git 提交 |
| 公共环境 | `/workspace/40289.env.sh`；源码模板可重建，无令牌正文 |
| 令牌 | 源码目录的私有 `serve/fpga-token.local.txt`，权限 600；不进入 Git/镜像/软件包 |
| GGUF | `/workspace/models/Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf`，完整 SHA-256 校验 |
| 软件恢复包 | `/workspace/40289-recovery/runtime-20261028.tar.gz` 与 `.tar.json` 清单 |
| API 运行状态 | `/var/tmp/40289-rtl-agent`，重启后重建，旧 PID 不作为持久状态 |
| 归档 | `/workspace/40289-deployment-20261028/runs`，避免落入小型 `/tmp` 内存盘 |

软件包共 1165 文件，压缩 754367094 字节，解压约 3.25GB。
包含经过验证的 llama.cpp 分发、实际加载的 native SDK 库、内核数据、slang 和隧道程序。
不包含模型权重、令牌、隧道租约/私有配置、SSH 私钥、官方答案或测试台。
有相同 SHA-256 的本地副本，位于项目外 `runtime-downloads/offline-context-20261028`。

## 恢复保证的边界

- 基础系统、基础 Python（3.11+）、Vivado 2026.1、GPU 驱动与挂载仍由平台镜像提供。
  包内记录并核对基础系统库哈希，不会覆盖 libc 或伪装成完整 Vivado/OS 镜像。
- 包针对当前 Ubuntu 24.04/x86_64 与固定推理栈；系统库/已有软件不同会明确失败，不能盲目覆盖新版本。
- 本轮没有真正重启云实例：用空目录模拟软件全部缺失，并实际冷启动恢复出的模型，避免丢失现有端点。
- 若持久卷本身被删除或丢失，盘内源码、权重和令牌也会丢失。恢复包另存本地，
  但约 11.8GB GGUF 不在包内，仍需从已声明 revision 和 SHA-256 的来源准备。

实际验证与 TCP 隔离限制见 [断网与上下文记录](offline-context-validation-20261028.md)。
