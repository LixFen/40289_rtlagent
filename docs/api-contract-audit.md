# API_CONTRACT.md 逐项核对

依据：`rtlagent2026/docs/API_CONTRACT.md`，参考提交
`f7a8bd465c2a820e006c7a00ecc7df468974635b`。核对时间：2026-10-17。
“实现完成”不代表最终 Linux/GPU/离线部署验收完成。

## HTTP 接口

所有端点要求 `Authorization: Bearer $FPGACHINA_TOKEN`，API 绑定127.0.0.1:7860。

| 方法与路径 | 行为 |
| --- | --- |
| GET /v1/health | 200；返回 ready、track=rtl、model、vram_gb |
| POST /v1/solve | 202；立即返回 job_id、原样 task_id、status=queued |
| GET /v1/jobs/{job_id} | 200；queued/running仅返回标识和状态；done再附solution、trace、elapsed_s |

未知job返回404；无效请求返回400；令牌错误返回401。求解失败是正常done，solution为空，
不把求解失败变成HTTP 500。相同nonce始终映射到同一任务，重复投递不再次求解。

POST请求字段：task_id、nonce、mode、prompt、interface、deadline_s。
mode支持agent/baseline；RTL评测interface可为空，正式单题deadline_s为900秒。
外部调用方取得 job_id 后通过 GET 查询结果；请求断开不影响后台任务执行。

接收的 JSON 请求体示例：

```json
{
  "task_id": "t1", "nonce": "unique-submission-nonce", "mode": "agent",
  "prompt": "Implement a module named TopModule ...",
  "interface": "", "deadline_s": 900
}
```

## 发现的全部软件缺项与处理

| # | 契约要求 | 核对前问题 | 当前处理 / 证据 |
| --- | --- | --- | --- |
| 1 | 投题立即202 | 同步挂起直至求解结束，返回200 | JobManager解耦HTTP与求解；真实投题39.8ms返回202 |
| 2 | job_id与轮询端点 | 没有GET /v1/jobs | 随机job_id与只读查询；未知任务404 |
| 3 | queued/running/done | 没有任务状态机 | 独立队列线程；只有done暴露结果 |
| 4 | nonce幂等 | nonce被忽略 | 锁内去重；12个并发重复提交只执行1次；done后也保留映射 |
| 5 | 排队时间计入期限 | 同步锁等待虽计时，但短任务可能无法及时被查询为完成 | 投递时记录单调时间；独立排队过期监控，不被前面的长任务阻塞 |
| 6 | 失败返回正常空代码结果 | 没有统一的异步终态 | 异常/过期/取消都进入done；空solution、JSONL trace |
| 7 | 断开客户端不影响任务 | 求解仍继续但结果不可重新取回 | 独立任务生命周期；可以用nonce找回job_id及轮询结果 |
| 8 | 所有端点鉴权 | 缺少jobs端点保护 | health/solve/jobs全部鉴权；Windows无授权POST读掉小请求体，避免RST覆盖401 |
| 9 | 几秒内返回与连续处理 | 长HTTP请求不能穿过平台入口 | 请求线程只提交/读取状态；200次连续真实子进程请求回归，服务不中途重启 |
| 10 | 异步投递与结果查询契约 | 曾依赖同步等待完整解答 | HTTP 回归验证202及轮询；请求辅助代码仅在tests中，外部评测方负责正式投题 |
| 11 | 健康模型名与声明一致 | 只核对/models中的环境别名 | 同时核对manifest声明；新增根MODEL.md，与model/MODEL.md保持同一模型 |
| 12 | Agent/基线共用思考设置 | Agent可单独传low，基线没有共享声明 | manifest.generation由两者读取；云桥接对两者应用共同参数；健康检查拒绝不一致设置 |
| 13 | 官方基线原样携带 | 项目副本落后于当前参考版本 | 从参考checkout逐字复制两份文件；preflight均匹配，没有改写其逻辑 |
| 14 | 空interface从题面解析 | “module of”被误读为模块名；辅助模块端口可能覆盖顶层端口 | 显式Module name/named/HDL声明优先；只核对目标模块的端口；回归覆盖真实加法器题面 |
| 15 | SIGTERM后强制回收整题 | 靠近期限才直接强杀，缺少Linux孤儿回收 | 提前10秒停止；SIGTERM、5秒宽限、强制结束；登记EDA进程组并清扫 |
| 16 | Jupyter PID1下不积累僵尸 | 没有subreaper | Linux服务及CLI启用PR_SET_CHILD_SUBREAPER，按所属进程组waitpid；无法启用则服务启动失败 |
| 17 | start.sh全部就绪后返回 | 前台阻塞在API，且不负责启动模型 | start.sh负责模型、遥测和清理器；就绪后返回0，失败非0；entrypoint.sh承接HTTP常驻 |
| 18 | start.sh可重复执行 | 重复启动服务 | 启动锁、模型探测和PID文件；启动器幂等单测通过 |
| 19 | 常驻进程脱离单题进程组 | 遥测是普通后台子进程 | Popen(start_new_session=True)，等价setsid；常驻服务不随单题超时结束 |
| 20 | 每次EDA有本地独立目录 | EDA直接写归档树，可能落NFS，保留大量临时文件 | EDA_TMP下独立TemporaryDirectory；小文件证据另存，xsim.dir/.Xil在运行后清理 |
| 21 | 两小时兜底清理 | 没有清理器 | 只清理本程序前缀目录；检查整树最新mtime及活动所有者；每10分钟扫描 |
| 22 | Linux可执行脚本 | Windows参考脚本含CRLF | 镜像构建按LF规范化，并严格匹配官方固定Git blob哈希；本机参考副本仍逐字一致 |
| 23 | 远程与容器单题入口 | HTTP直接调用Python，CLI与HTTP监督器嵌套 | 目标Linux的HTTP调用run.sh/run_baseline.sh；CLI只隔离公开文本并执行Agent |
| 24 | trace模型token及输出摘要 | 使用嵌套usage；输出摘要较长 | Agent llm事件增加tokens_in/tokens_out，工具excerpt按UTF-8限制为2KiB；完整原始日志保留 |

## 核对前已符合并继续保留

- 单服务用mode区分agent/baseline；基线单次模型调用，无Agent技能和工具调用。
- task_id原样返回，不将task_id/nonce解释为文件路径；每次独立输入与输出目录。
- 原始模型/工具调用写JSONL，超时返回已保存候选及中断记录。
- Vivado读取XILINX_VIVADO；xelab指定timescale；许可错误不按功能PASS处理。
- Shell使用set -eo pipefail，source AMD设置时不启用set -u。
- API及推理绑定loopback；推理端点不作为公网端点暴露。

## 验证证据

- 107项全量回归通过：`out/api-contract-20261017/acceptance-regression.log`，106.457秒。
- 脚本模型经异步API驱动真实Vivado：6轮、每轮2调用，76.162秒完成
  selftest_fail → 修复 → 同TB passed → lint/synth → submit。
  结果：`out/api-contract-20261017/vivado-smoke-v2/report.json`。
- 真实云模型加法器：POST 202耗时39.8ms，重复nonce返回同一job；
  最大轮询响应26.6ms，177.70秒后done，返回代码；数据在
  `out/api-contract-20261017/live-adder/`。
- 该代码独立131072组输入穷举及综合通过：
  `../trusted-evaluation/api-contract-adder-20261017/result.json`。
- 真实云模型自身TB修复没有收敛，因控制器额度停止并返回候选，不能算自主自测成功；
  这是Agent能力问题，不是异步接口或轮询失败。

## 尚缺的部署验收

| 待验项 | 当前限制 |
| --- | --- |
| 最终R9700/32GB本地模型推理 | 没有AMD实例；当前是Windows Vivado+云API开发环境 |
| /health.ready=true的正式验收 | 当前云服务别名与提交GGUF声明不同，且没有AMD GPU遥测；真实返回false，不伪造显存 |
| Linux启动脚本实际幂等/就绪/setsid | 启动器单测通过；本机WSL无/bin/bash，bash语法命令未能运行；尚未在目标Linux完整执行start.sh |
| Linux Jupyter下连续超时、孤儿/僵尸回收 | 有实现及信号顺序单测；没有目标平台压力实测 |
| rc-tunnel公网可达 | 没有可用AMD实例，未建立隧道或取得active证据；生产须配置赛事令牌 |
| 不可变基础镜像digest、干净容器重建 | BASE_IMAGE仍需提供；本轮没有构建并验收生产镜像 |
| 最终环境完全断网与许可证 | 需预置模型/运行库/工具/文档，并在目标平台断网跑真实最小任务 |
| 长时间、多题型、真实模型持续投题 | 200次合成模型服务回归通过；还需按真实trace体积配置本地归档容量（RUN_ARCHIVE_MB默认1GiB）并做长期负载测量 |

完成上述平台验收前，只能声明HTTP契约实现和本机验证通过，不能声明正式部署整体通过。
