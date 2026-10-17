# JockeyBench

[English](README.md) | 简体中文

**在有限的研究预算下，评测 LLM 智能体编写、测试和修订预测程序的能力：基于十年的日本赛马数据。**

智能体会拿到三样东西：一个沙箱化的 Python 环境，一组按时间门控、逐次计费的赛马档案数据工具，以及
一项任务：写出 `predict(sdk, context)`。每晚 21:00（日本时间），由框架而不是模型，用智能体当前的程序
预测第二天的每一场比赛，并封存一个完整排名和各马的获胜概率。结果在第二天晚上公布；在自适应臂中，
智能体随后可以修订自己的程序。智能体代码的每次数据读取、每次数据工具调用和每个 CPU 秒都以
*score-yen* 计费（控制类工具免费）。整条轨迹的得分是平滑对数得分减去这些费用。

本目录是 JockeyBench 仓库中的 `ProgramSetting/`，即论文 **Program** 设定（主实验组）的运行时：

* 协议 `economic-v1`，投影 `objective-v5`，计费规则 `score-yen-v2`（×0.5）；
* **A** 臂（每天修订程序）和 **S** 臂（冻结初始程序）；
* 参照程序 **R0–R3**。

它是一个自包含的包，下文所有命令都在本目录下运行。它的 CI 工作流是仓库根目录下的
`.github/workflows/program-setting.yml`。

> **本仓库不分发任何数据。** 档案数据是 JRDB 的付费会员数据。每位使用者需要自行订阅获取，并用这里附带
> 的导入器构建自己的私有数据库。见 [docs/DATA.md](docs/DATA.md)。

详细文档（`docs/` 下各文件、CHANGELOG、CONTRIBUTING）只有英文版；本文件是 [README.md](README.md) 的
中文对照。两者不一致时以英文版为准。

## 一条轨迹如何运行

```text
 warmup (model session)            day d                                                   day d+1
 ─────────────────────── ┬──────────────────────────────────────────────────────────────┬──────────
  explore support races   │ 21:00 (d-1) cutoff     race day d       20:30 release   reflect  │ 21:00 (d)
  write strategy.py       │ framework runs predict ─► seal ─► races ─► results + score ─► (arm A) │ cutoff ...
  validate (3 races ×2)   │ (no model call)                           booked in ledger  revise  │
```

1. **预热（warmup）**：一次模型会话。智能体探索窗口开始前的支撑比赛（support races），写出
   `strategy.py`，再在 3 场比赛上各运行两次做验证。
2. **截止（cutoff，前一天 21:00）**：框架运行 `predict` 并封存预测。这一步不调用模型。
3. **比赛与公布（当天 20:30）**：公布结果，计分并记入账本。
4. **反思（仅 A 臂）**：智能体可以修订程序，修订后的程序进入下一个截止。

要点：

* **信息边界。** 只投影白名单内的客观事实：出马表、调教、过往成绩、血统统计、骑师与练马师的近期表现。
  赔率、人气、派彩、JRDB 指数与评级、各类预测和自由文本一律不暴露。标识符都是带密钥的假名。
* **时钟。** 出马表从截止时刻起可见，名次标签从当天收盘（日本时间 20:30）起可见，复盘字段从其发布时间
  起可见。2026 赛季是封存的保留集，运行时永远不会读取。
* **程序接口。** `predict(sdk, context)` 返回完整排名和概率。程序只能通过 `sdk.call(tool, **arguments)`
  看到比赛数据，用的是与智能体相同的计费工具。
* **只验证，不择优。** 新程序必须在最多三场支撑比赛上通过接口和资源检查，每场运行两次。程序从不按准确率
  挑选。验证失败时保留上一个有效程序；若没有，就使用确定性的均匀预测。
* **得分。** 每场比赛的得分为 `S·ln(n·p̄) + I(predictor)·B·(hit − 1/n)`，其中 S = B = 100，平滑系数
  λ = 0.02。均匀预测的得分恰好为 0。再减去数据费、调用费和 CPU 费。全部规则见
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。
* **完整性。** 每个检查点都绑定数据源、代码、配置、智能体和沙箱环境，每一步都能在崩溃后幂等恢复。
  `replay-audit` 重新提供每个工具响应并重新计算每场比赛的得分；`reexecute-audit` 重新运行每个已封存的
  程序。

## 安装

需要 **Python 3.13**。运行时支持使用 `sandbox-exec` 的 **macOS**，以及使用 `bubblewrap` 和
`libseccomp` 的 **Linux**。Linux 适配目标为 x86_64 或 aarch64 架构的 Ubuntu 22.04/24.04。
在 Linux 上先安装系统依赖，再创建 Python 环境：

```bash
sudo apt-get update
sudo apt-get install bubblewrap libseccomp2 libarchive-tools lsof build-essential
```

`lhafile` 在 Linux 上可能需要编译 C 扩展，尤其是 ARM 架构，因此需要 C 编译器。Python 安装还必须提供
与解释器匹配的 Python 3.13 开发头文件。若系统 Python 不包含这些文件，请从与解释器相同的软件源安装
对应的开发包（通常为 `python3.13-dev`）；托管的 Python 发行版可能已包含这些文件。

Linux 主机必须允许 bubblewrap 创建用户命名空间。容器策略以及部分
[Ubuntu 24.04 的 AppArmor 配置](https://documentation.ubuntu.com/security/security-features/privilege-restriction/apparmor/)
可能禁止此操作，即使依赖已经安装。请使用策略允许 bubblewrap 的主机或 CI runner；隔离不可用时，
运行时会拒绝执行，绝不回退为无沙箱进程。不要为了启动运行而全局关闭主机的安全限制。

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements-lock.txt
.venv/bin/pip install --no-build-isolation --no-deps -e .
```

锁文件固定了运行时、导入器和测试的全部依赖。每条轨迹都会记录解释器里能导入哪些库，所以实验环境里
不要装额外的科学计算包。贡献者还需安装 `requirements-dev.txt`，它额外包含 `ruff`。

## 无需任何数据即可测试

```bash
.venv/bin/pytest
```

所有测试都使用伪造的数据，放在内存里或写入临时目录。两种支持的平台都可以用伪造数据测试真实沙箱：

```bash
JRDB_NATIVE=1 .venv/bin/pytest -m native
```

这些测试不需要实验主机标记、JRDB 数据或模型供应商凭据。CI 在 Ubuntu 22.04、Ubuntu 24.04 和 macOS
上运行单元测试，并在 Ubuntu 22.04 和 macOS 上运行 native 测试。

## 运行

涉及真实数据或模型的命令只在指定的实验主机上运行，并且必须从已预先配置 `.experiment-host` 标记的
checkout 根目录执行。不要在开发机器上复制或创建该标记。在实验主机上按
[docs/DATA.md](docs/DATA.md) 准备 `data/private/expanded.sqlite`，然后运行一个参照程序；参照程序
不调用任何模型。

```bash
export JRDB_SOURCE="$PWD/data/private/expanded.sqlite" JRDB_RUNS="$PWD/runs"
.venv/bin/jockeybench --config configs/smoke.json doctor
.venv/bin/jockeybench --config configs/smoke.json run --run-id smoke-r2 --reference R2
.venv/bin/jockeybench --config configs/smoke.json replay-audit --run-id smoke-r2
.venv/bin/jockeybench --config configs/smoke.json reexecute-audit --run-id smoke-r2
```

`configs/paper.json` 是论文的主配置：2024 年上半年的十个比赛日，包含全部符合条件的比赛。要评测一个
模型，先写一个 provider 文件（见 [docs/MODELS.md](docs/MODELS.md)），然后运行一次共享预热，再分别
运行两个臂：

```bash
.venv/bin/jockeybench --config configs/paper.json run --run-id m-warmup --warmup-only --provider-config provider.json
.venv/bin/jockeybench --config configs/paper.json --arm S run --run-id m-S --initial-from m-warmup --provider-config provider.json
.venv/bin/jockeybench --config configs/paper.json --arm A run --run-id m-A --initial-from m-warmup --provider-config provider.json
```

审计每次运行时，要用它运行时相同的 `--config` 和 `--arm`，例如 `--arm S replay-audit --run-id m-S`。
`report --run-id m-A` 打印已保存的报告；
`compare runs/m-A/report.json.gz runs/m-S/report.json.gz` 逐场配对比较两份报告（左减右，这里即 A − S）。

一个运行根目录（`JRDB_RUNS`）同一时间只能有一条活动轨迹，它的 5 GiB 存储上限覆盖其中的所有运行。
`--initial-from` 从同一个根目录读取，所以一个模型的预热和两个臂共用一个根目录，并且依次运行；不同模型
请各用一个根目录。

每条命令都输出 JSON。退出码 2 表示协议拒绝，3 表示 provider 请求被中断。遇到退出码 3 时，在原有代码和
环境下重新执行同一条命令即可续跑。切换到 Linux 时必须新建轨迹，不得在 Linux 上续跑或拼接 macOS 轨迹。

## 仓库结构

| 路径 | 内容 |
| --- | --- |
| `src/jockeybench/data/` | 只读、按时间门控的档案访问，字段白名单和投影 |
| `src/jockeybench/protocol/` | 工具代理（计量、价格、幂等），工具说明，智能体任务说明 |
| `src/jockeybench/sandbox/` | macOS `sandbox-exec` 和 Linux bubblewrap/seccomp 执行器、CPU 计费、策略版本；`client/` 是沙箱内程序唯一能看到的基准代码 |
| `src/jockeybench/lifecycle.py`、`runner.py` | 预热、封存、结算、反思、验证与回退；带检查点的运行 |
| `src/jockeybench/scoring.py`、`ledger.py`、`reporting.py` | 计分规则、整数 score-yen 账本、报告与配对比较 |
| `src/jockeybench/agents/` | 兼容 OpenAI 接口的工具调用智能体、带成本监控的传输层、参照程序 R0–R3 |
| `src/jockeybench/audit/` | 重放审计与重新执行审计 |
| `src/jockeybench/importer/` | JRDB 档案 → 私有数据库 → 带密钥的假名 |
| `configs/` | `paper.json`（主协议）和 `smoke.json`（两天） |
| `tests/` | 基于伪造数据的单元测试和 native 测试 |
| `docs/` | 架构、数据准备、模型评测、复现说明 |

## 可复现性

规则、计分、记账和审计都与产出论文主实验组的 v0.6.0 运行时相同。面向智能体的契约（系统提示、工具说明、
每个工具响应和错误信息）沿用 v0.6.0，只改正了与实际执行规则相矛盾的表述，作为协议 **0.6.1** 发布。
例如：概率是否必填，命中奖金如何对待全部相等的概率，以及真实的响应大小上限。
[CHANGELOG.md](CHANGELOG.md) 和 `protocol/revisions.py` 列出了每一处修改。

测试证明，把这些修正还原后，系统提示、工具说明和会话任务文本与 v0.6.0 逐字节一致。工具响应和错误信息由
一个指纹比对工具检查：它在原运行时上跑完整的合成轨迹（包括一段脚本化的模型会话），再与本运行时比对。
该工具驱动的是内部的 v0.6.0 运行时，因此不随仓库分发。比对结果显示，除了修正本身带来的差异，以及三个
无实际作用、已不再计入绑定配置的上限之外，没有任何差别。

[docs/REPRODUCING.md](docs/REPRODUCING.md) 列出了验证过的内容和删去的内容，也说明了哪些部分无法精确复现：
provider 的非确定性、你自己的私有假名密钥，以及你的解释器环境。

Linux 支持保留协议规则，但不意味着不同操作系统或硬件下的预测、CPU 费用或分数相同。运行时 1.1.0 会为
每条新轨迹记录机器架构、平台和沙箱环境。本次平台支持仅适用于 `ProgramSetting/`；`ActiveResearch/` 仍依赖外部的
`jrdb_lh` 框架，并不是可以独立运行的 Linux 包。

## 引用

配套论文 *JockeyBench: Evaluating LLM Agents on Horse-Racing Decision-Making* 正在评审中，引用信息将在
评审结束后补充。

## 许可证

代码以 [MIT 许可证](LICENSE) 发布。该许可证只覆盖本仓库，不授予任何使用 JRDB 数据或规格文档的权利；
这些仍受你与 JRDB 之间协议的约束，详见 [NOTICE.md](NOTICE.md)。
