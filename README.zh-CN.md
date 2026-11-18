# JockeyBench

[English](README.md) | 简体中文

JockeyBench 评测 LLM 智能体在研究预算约束下开发预测程序的能力。
论文比较同一框架下的两种工作流。在仓库根目录安装一次即可使用两者。

| 工作流 | 命令 | 智能体开发的内容 | 实验臂 |
| --- | --- | --- | --- |
| **code-as-policy** | `jockeybench code-as-policy` | 输出完整排名和获胜概率的 Python 预测程序 | A：每日修订；S：冻结初始程序 |
| **LLM-as-policy** | `jockeybench llm-as-policy` | 可复用的证据采集计划，LLM 根据计划收集的证据逐场预测 | Adaptive：每日更新策略；Frozen：使用相同初始策略且不更新 |

已有脚本仍可用 `jockeybench program` 调用 code-as-policy，用 `jockeybench research` 调用 LLM-as-policy。

两种设定共用按时间门控的数据服务、计费、预测校验和评分引擎。公开的 LLM-as-policy 整合版使用独立的
`jockeybench-research-v1` profile；这是新的部署配置，不能视作历史 DeepSeek/Frozen 实验的逐字节复现。
详见[架构](docs/ARCHITECTURE.md)和 [LLM-as-policy 说明](docs/RESEARCH.md)。

**不分发任何数据。** 使用者需自行订阅 JRDB，再用仓库内的导入器构建私有数据库。2026 年数据仍保持封存。
详见[数据说明](docs/DATA.md)。

<a id="当前-program-实验结果"></a>

## 当前 code-as-policy 实验结果

[2026-10-18 结果快照](results/README.md)纳入 **26 个模型，每个模型的 A、S 两组均取第 1–5 轮**：
共 130 条 A 轨迹和 130 条 S 轨迹，使用相同的 324 场比赛、10 个比赛日。
额外完成的轮次单独用于稳健性比较；不足五个完整轮次的两个模型列入排除说明。
这些结果属于 code-as-policy，表格同时提供[机器可读汇总](results/program-results.json)。

## LLM-as-policy 论文净分与配套材料

论文跨工作流比较的净分取 **`score.common_net`**。报告中的两个净分字段都是单条轨迹总分。
复算正文的每场净分时，每条完整轨迹先除以 324，再对轨迹取平均。对每场可计分的预测，均计入居中的首选马命中项
`100 × (命中指示值 − 1/n)`，其中 `n` 是实际参赛马数。均匀提交和执行回退也按此计算。
报告同时输出 **`score.aligned_net`**，只有预测器直接交付或修复后交付才计入这一项。
两者使用相同的对数得分，扣除相同的加权研究费，归属该轨迹的热身费只扣一次。供应商费用另以美元统计。
旧字段 `score.net_after_warmup` 等于 `score.aligned_net`。

- [论文样本清单](artifacts/workflow-cohort.csv)可按 `workflow` 列的 `LLM-as-policy` 或 `code-as-policy` 筛选。
  清单含 9 个模型，每个模型在两种工作流中各有 3 条轨迹，每条封存 324 场。配对 Frozen 对照另计。
  [旧标签清单](artifacts/workflow-cohort-legacy-labels.csv)仅在 `workflow` 列使用旧标签 `Adaptive Acquisition` 和 `Program`，其他各列相同。
  `updater_lifecycle` 列反映 2026-11-18 完成的检查结果。依据已保存的赛后 updater 检查点，原标记为 UNKNOWN 的 11 条轨迹均确认为 PASS。
- [投稿版接口附件](artifacts/minimal-interface-supplement.zip)中的 `jracebench-llm-policy/` 收录历史
  DeepSeek-V4-Flash R1 及其配对 Frozen 对照的参考提示词、工具定义、配置和源码出处。

## 统一安装

使用 **Python 3.13**。支持 macOS、Linux，以及 **Windows 上的 WSL2 + Ubuntu 22.04**。
Windows 用户请按 [Windows 安装指南](docs/WINDOWS.md)操作，其中提供 PowerShell 启动器和全新环境安装脚本。
不支持直接使用 Windows 原生 Python 或 WSL1。macOS 使用 `sandbox-exec`；Linux 和 WSL2 使用 bubblewrap 与 libseccomp。
Ubuntu 22.04/24.04 先安装系统依赖：

```bash
sudo apt-get update
sudo apt-get install bubblewrap libseccomp2 libarchive-tools lsof build-essential
```

Python 需包含匹配的开发头文件，以构建 `lhafile` 扩展。Linux 主机需允许 bubblewrap 用户命名空间；
部分容器或 AppArmor 策略会限制它。隔离失败时运行停止，请使用配置合适的主机，不要全局关闭安全限制。

在仓库根目录运行：

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements-lock.txt
.venv/bin/pip install --no-build-isolation --no-deps -e .
.venv/bin/jockeybench code-as-policy --help
.venv/bin/jockeybench llm-as-policy --help
```

现在只有一个 Python 包 `jockeybench`，LLM-as-policy 位于 `jockeybench.research`。其公开入口无需
另装 `active_research`、`jrdb_lh` 或提供外部 warmup 源码归档。
旧版源码整理记录保留在[来源说明](provenance/active-research/README.md)中。

## 使用合成数据检查

```bash
.venv/bin/pytest
JRDB_NATIVE=1 .venv/bin/pytest -m native
.venv/bin/python -B scripts/verify_research_provenance.py
```

默认测试使用合成输入，不调用模型。源码内嵌的历史 LLM-as-policy 测试不被默认收集；它们不能证明新 profile
复现了历史轨迹。CI 针对 Ubuntu、macOS，以及实际启动 WSL2 Ubuntu 22.04 的 Windows Server 2025；
Windows 10/11 用户电脑属于部署目标，并不表示已测试每种硬件。贡献者另安装 `requirements-dev.txt`。

## 运行两种设定

真实数据读取和模型调用只在已配置 `.experiment-host` 标记的实验主机上进行。数据库、运行目录和凭据
均在该主机本地配置。不要把标记复制到开发机器，也不要在开发机器上创建它。

```bash
export JRDB_SOURCE="$PWD/data/private/expanded.sqlite" JRDB_RUNS="$PWD/runs"

# code-as-policy 的离线参考程序，不调用模型。
.venv/bin/jockeybench code-as-policy --config configs/smoke.json doctor
.venv/bin/jockeybench code-as-policy --config configs/smoke.json run --run-id program-r2 --reference R2

# LLM-as-policy 的 Adaptive 臂及其配对的 Frozen 对照。
.venv/bin/jockeybench llm-as-policy --config configs/research.json run \
  --run-id research-adaptive --provider-config provider.local.json --arm adaptive
.venv/bin/jockeybench llm-as-policy --config configs/research.json run \
  --run-id research-frozen --provider-config provider.local.json --arm frozen \
  --initial-from research-adaptive
```

LLM-as-policy 会调用配置的模型并可能产生费用。provider 文件和运行目录是用户自行准备的私有输入，
这些命令不是已经完成实验的记录。具体格式见 [LLM-as-policy 配置](docs/RESEARCH.md)。原有
`jockeybench --config ...` 命令仍默认选择 code-as-policy。另见[模型配置](docs/MODELS.md)与
[code-as-policy 复现说明](docs/REPRODUCING.md)。

## 目录

```text
src/jockeybench/             共享引擎、code-as-policy 工作流及命令行入口
src/jockeybench/research/    LLM-as-policy 工作流及兼容组件
configs/                    公开配置模板
tests/                      当前支持的合成测试与包装检查
provenance/active-research/ 原源码整理记录与迁移哈希
results/                    code-as-policy 主实验结果（仅汇总数据）
```

JockeyBench 作者为两种工作流统一采用
[MIT 许可证](LICENSE)。论文接口附件中由项目作者编写的参考提示词和代码也适用该许可。详见[许可范围](docs/LICENSE-SCOPE.md)。
