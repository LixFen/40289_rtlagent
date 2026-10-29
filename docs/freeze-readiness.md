# 40289 队冻结准备（2026-10-28）

已连接提供的平台并部署候选版本；尚未完成赛事仓库封存与推送。
源码允许填写令牌后保持哈希不变：令牌属于私有部署配置，不属于交付源码。

## 预先填写比赛令牌

打开仓库 `serve/fpga-token.local.txt`，只填一行赛事令牌正文，不加引号、
不写 FPGACHINA_TOKEN= 或 Bearer。空文件不能启动 API，不会退化为无鉴权。
内容按纯文本读取，不执行 shell。

Git 忽略、Docker 上下文和 freeze 清单均排除此文件。Git 克隆不会传输它，
须单独复制到平台仓库的同一路径，或持久盘文件并设置 FPGACHINA_TOKEN_FILE。
Linux 文件权限设为 600。已填写文件优先于旧环境令牌；未填写时仍支持既有环境变量。
无需运行时手工输入。更换令牌须 stop 后重新部署，不热切换在途服务。

## 本地候选检查

```bash
python -B -m unittest discover -s tests -v
python -B tools/preflight.py
python -B tools/build_image.py
python -B tools/freeze.py --output out/source-hashes.json
python -B tools/freeze.py --output out/source-hashes.json --check
```

Windows 跳过的 Linux flock、信号与进程回收测试已在提供的平台补跑，完整 212 项通过。
独立网络命名空间被平台权限拒绝，仍不能声明完整断网验收通过。out 内日志和清单不提交到源码仓库。
冻结前审查全部未提交改动，排除权重、运行档案、密钥及参考答案/测试台。
源码工作区哈希受检出行尾影响，最终以 Linux 部署目录生成并核验的清单为准。

## 平台与正式提交

1. 连接先自检系统、权限、GPU/ROCm、磁盘挂载、Vivado/器件/授权与软件版本。
   核对官方基底和实际安装差异，补齐可复现步骤后再冻结。
2. 使用候选版本与校验后的权重，部署前放好令牌文件及环境配置，
   执行 `bash serve/deploy.sh`。确认真实模型生成与 ready=true。
3. 重跑相关 Linux 测试；验证真实 RTL 编译/仿真/综合、官方 selftest、
   Agent/Baseline、超时后接题、长输入显存及无外网下载依赖。
   API 守护启动/异常恢复/停止也须实测；测试时不连接正式评测隧道。
4. 全部改动经审查后提交最终 commit，推送到唯一的私有仓库 40289_rtlagent 默认分支。
   邀请 GitHub vickyiii 或 Gitee Vickyiii，确认可读。现有远端名不等于已满足赛事命名。
5. 平台运行同一 commit，记录 git rev-parse HEAD、源码清单、模型/运行库版本与哈希。
   检查令牌不在 git ls-files 中。填令牌不应改变源码 freeze --check 结果。
6. 先启本地服务，确认未鉴权 health 为 401、鉴权 health 为 ready=true，
   再安装 rc-tunnel/expose、去除代理测试公网鉴权访问并按赛事通知报送网址。

截止：北京时间 10 月 31 日 10:00；此前应完成部署、默认分支和访问确认。
评测期间不修改封存代码、技能或权重。报告、视频及仓库链接在 11 月 4 日 18:00 前提交。
