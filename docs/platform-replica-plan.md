# 个人短租实例：官方镜像环境核对与安装策略

2026-10-14。当前没有实例，本次仅核对资料并确定操作策略，未执行远端连接或安装。

## 依据和边界

已阅读赛事仓库 README、docs/API_CONTRACT.md 第九/十节、FAQ、example/Dockerfile、
example/requirements.txt、serve/README.md，以及 fpgachina26-amd 的 RTL/HLS GUIDE。
示例 Dockerfile 的基础镜像名仍为占位符，未取得官方镜像 digest、包清单或 SBOM。
因此目前能复现公开要求和运行能力，不能声称完全复刻镜像的软件全集或补丁版本。

将三个清单分开：赛事明确提供的组件、个人实例实际发现的组件、本方案额外依赖。
用户要求的 ROCm 10.0 / PyTorch 2.12 / vLLM 0.27 系列属于复现目标；文档没有给出的
官方框架精确版本不能自行推定。具体 wheel、ABI 和 GPU 构建须按实例实测匹配。

## 必须核对的范围

| 层 | 核对内容 | 验证标准 |
| --- | --- | --- |
| 主机/权限 | OS、x86_64、glibc、CPU/主存及 cgroup 配额、sudo/容器权限、持久盘和本地盘 | 确定可安装位置、实际资源和读写权限 |
| GPU | 分配设备节点、rocminfo、实际 gfx 架构、驱动、ROCm、可见卡数 | 实际 GPU 运算与模型生成；不把宿主额外卡当作分配卡 |
| Vivado 2026.1 | vivado、xvlog、xelab、xsim；一并盘点 xvhdl 等安装附带入口 | 编译、展开、有限自检、lint 和 OOC 综合全部实跑 |
| 器件库 | xczu3eg-sbva484-1-e | 查询器件可用并实际综合，不能只安装可执行程序 |
| Vitis 2026.1 | 安装清单、vitis-run/v++/HLS 等实际可用入口，按发行版实际命令为准 | 对齐镜像工具范围；RTL 主链不增加 HLS 解题步骤 |
| 推理 | 匹配 GPU 的 llama.cpp 和运行库、指定 GGUF、上下文、采样参数 | 模型哈希、实际 GPU 加载/生成和 OpenAI-compatible HTTP |
| 框架 | 用户指定 PyTorch/vLLM 等版本和依赖 | Torch GPU 运算、vLLM 原生/CLI；不把它们等同于 GGUF 推理成功 |
| 系统/部署工具 | bash、Python、curl、解包/哈希工具；Docker/Podman 实际权限；必要构建工具 | 已有则复用，缺项按明确用途安装；不无目的安装全部开发包 |
| 镜像约定 | XILINX_ROOT、XILINX_VIVADO、XILINX_VITIS、EDA_TMP、PATH、动态库与授权配置 | source settings64.sh 后运行；避免 set -u 导致厂商脚本退出 |
| 工作目录辅助 | eda-workdir、/tmp/eda 本地盘、/workspace 持久盘、已有清理策略 | 私有逐次工作目录；缺少助手时提供明确标识的兼容实现 |
| 接入方式 | 127.0.0.1:7860/8000；若平台提供则核对 rc-tunnel | 非 Radeon Cloud 不强行安装平台私有入口，可通过 SSH loopback 转发验证 |
| Ross | 已冻结解析器、指南及许可证 | 现有离线文件随代码预置并校验；MCP/完整 RAG 不是已接入依赖 |

示例 requirements.txt 中 requests/FastAPI/uvicorn/pydantic 是参考实现依赖，不能据此
认定镜像保证预装；本 Agent 使用标准库。取得官方镜像清单后再核对额外包。
赛事未提供 iverilog、Verilator、Yosys 或形式验证器，不用它们替代判定链。

## 连接后的顺序

1. **只读盘点**：读取上述环境，查 PATH 与配置安装目录，不先改驱动、升级框架或下载权重。
   每项记录来源、路径、版本、状态和探测错误；不输出密钥、口令、令牌或许可证内容。
2. **差异与可行性**：确认 Linux 安装包、磁盘/解包空间、AMD 账号下载条件、授权和容器权限。
   驱动问题区分宿主与容器，不能在普通租用容器里假装完成宿主内核驱动安装。
3. **安装缺项**：优先补齐可实际运行的 Vivado RTL 工具及目标器件支持，再对齐 Vitis 范围。
   尽量复用统一安装器，检查组件清单，避免重复安装。使用 Linux 包，不能上传 Windows 安装目录代替。
4. **最小工具验收**：公开新题覆盖 xvlog/xelab/XSim/lint/synth；区分设计、测试台、授权、库和超时错误。
   先确认工具链有效，再投入模型下载和完整实验，减少短租资源浪费。
5. **推理与框架**：下载固定模型、核验哈希，匹配 gfx1100/gfx1201 等真实架构；隔离安装框架依赖，
   固定 ROCm Torch，避免解析器自动换成 CUDA 版本。通过实际 GPU 运算及推理。
6. **整机验收**：真实模型自主闭环、原样 baseline 配对、组件消融、资源/截止时间/连续请求。
7. **离线重启**：禁止外联，验证授权、模型、工具、检索与服务全可用；导出差异表、环境清单、配置和证据。

每项使用“发现/缺失/已安装/实际通过/失败/未验证”状态。完整版本号、来源、哈希和失败原因
写入实例专属报告。正式镜像后续发布后再作一次逐项差异核对。

## 租机前需要准备的关键条件

- 安装包与持久盘空间要先落实。AMD 2026.1 文档列出完整 Vitis 安装约需 200 GB，
  实际随组件/器件选择变化，此外还要留下载、解包、模型和测试空间；不能假设 100 GB 足够。
  [安装要求](https://docs.amd.com/r/en-US/ug1742-vitis-release-notes/Installation-Requirements)
- 安装完整设计工具，不能用较小的 Vivado Lab Edition 替代；官方说明 Lab Edition 用于
  器件编程和调试。[2026.1 下载页](https://www.amd.com/en/support/downloads/adaptive-socs-and-fpgas/development-tools/2026-1.html)
- Vivado 2026.1 已引入分级授权，免费 BASIC 仍有年度授权且仿真特性有限。赛事 FAQ 的
  “目标器件无需专业授权”不等于所有功能无需任何授权；以所需 XSim/综合操作实测和当前条款为准，
  不先推定必须购买，也不承诺断网签出。[授权说明](https://www.amd.com/en/products/software/adaptive-socs-and-fpgas/vivado/vivado-licensing-options.html)
- 镜像助手的定时清理属于运行约定，不能照搬成删除持久档案的任务。若需补齐，只处理本地临时
  目录，保留活跃任务和验证证据；节点盘上的大量 .Xil/xsim.dir 不直接写入 NFS 持久盘。
- 准备期优先国内可达镜像；AMD 账号限定包使用正常获授权渠道，必要时本机预取后上传。
  记录厂商校验值并建立 SHA-256 清单。运行期不保留联网下载或自动升级 fallback。
