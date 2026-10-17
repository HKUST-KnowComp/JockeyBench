# Active-v3 and Matched Frozen

本目录包含 RacingBench 的 Active-v3 实现及 matched Frozen 对照代码。

Active-v3 先通过历史 warmup 生成初始获取程序 \(P_0\)，再由获取程序决定每场比赛需要查询哪些信息。预测封存、当日结果释放后，updater 根据已释放的记录更新获取程序，供后续比赛使用。

Matched Frozen 使用对应 Adaptive 轨迹的同一个 \(P_0\)，在整个评估窗口保持不变，不重新 warmup，也不运行 updater。两种工作流沿用相同的预测、证据表示、研究费用核算和评分规则。

本实现依赖外部 Program 框架及固定的历史 warmup archive。它们的源码、比赛数据、模型凭据和实验运行产物不包含在本目录中。

## 目录结构

```text
ActiveResearch/
├── src/active_research/
│   ├── finegrained_v3/       Active-v3 与 matched Frozen 核心实现
│   ├── policy_amortized/     获取程序、证据表示及公共运行组件
│   ├── policy_warmup/        历史 warmup 与 archive 加载接口
│   ├── aligned/              预测交付、完整性检查及评分接口
│   ├── adaptive/             信息域和模型调用公共组件
│   ├── experience/           比赛日程、提示词和 Frame 公共组件
│   ├── llm/                  模型调用基础接口
│   ├── behavioral/           现有测试需要的兼容模块
│   └── ...                   其他公共模块与测试资源
├── tests/packaging/           包结构和文件完整性检查
├── tools/                    依赖扫描与源码核验工具
├── docs/                     依赖、文件用途和测试说明
├── metadata/                 文件清单、源码哈希与依赖映射
└── pyproject.toml
```

`finegrained_v3/` 是正式工作流的主要入口。其他目录保留了它实际使用的公共组件，以及现有测试需要的兼容依赖。独立的旧实验入口和试验性代码未纳入主包，具体范围见 [文件清单](docs/FILE-INVENTORY.md) 和 [历史代码说明](docs/HISTORY-AND-EXPERIMENTS.md)。

## 核心模块

以下路径均位于 `src/active_research/finegrained_v3/`。

| 模块 | 用途 |
|---|---|
| `lifecycle.py`、`supervisor.py` | 组织比赛执行、封存、结果释放、每日更新和运行监督 |
| `policy_contract.py`、`policy_executor.py`、`policy_cpu.py` | 校验和执行获取程序，并应用原有 CPU 限制 |
| `control.py`、`domains.py` | 提供 ControlView 和四个信息域 |
| `marginal_cost.py` | 管理已获取证据及边际研究费用 |
| `runtime.py`、`predictor.py`、`transport.py` | 逐场预测、修复和模型调用 |
| `warmup.py`、`warmup_context_fit.py` | 历史 warmup 及其上下文管理 |
| `policy_builder.py`、`updater_context.py` | 构建和更新获取程序 |
| `frozen_inputs.py` | 加载并核验匹配 Adaptive 的初始程序 \(P_0\) |
| `frame.py` | 从已释放记录生成每日 Frame |
| `gate.py`、`audit.py`、`audit_live.py` | 工程 gate 和运行完整性检查 |
| `report.py`、`frozen_report.py` | 汇总 Adaptive 和 Frozen 的保存记录 |

## 外部 Program 依赖

本包通过 Python 命名空间 `jrdb_lh` 引用外部 Program 框架，没有将其源码复制进来。

这一依赖不只包含数据，还包括：

- 运行配置、Run 和 checkpoint 接口；
- 原生数据与工具服务；
- ledger、计量和 tariff；
- 评分与预测质量计算；
- 审计及部分模型调用接口。

使用前，需要由 Program 提供方准备兼容的源码和运行依赖。可以使用环境中已安装的 `jrdb_lh`，也可以把包含 `jrdb_lh/` 的父目录加入 `PYTHONPATH`。

例如，外部目录为：

```text
/path/to/Program/
└── jrdb_lh/
    ├── __init__.py
    └── ...
```

则设置：

```sh
export PYTHONPATH="/path/to/Program${PYTHONPATH:+:$PYTHONPATH}"
```

这里填写的是 `jrdb_lh/` 的父目录。

可以用以下命令确认 Python 能找到该包：

```sh
python -B -c "import importlib.util; print(importlib.util.find_spec('jrdb_lh'))"
```

找到包只说明搜索路径正确，还需要确认其接口、数据、费率和运行配置与实验要求一致。详细依赖关系见 [DEPENDENCIES.md](docs/DEPENDENCIES.md)。

## 历史 warmup archive

历史 warmup 使用一份固定版本的 Program archive，与正常运行时引用的 Program 框架分别配置。

在 warmup profile 中，`archive_source` 应指向归档内的 **`jrdb_lh` 包目录本身**，即直接包含 `__init__.py` 的目录：

```text
/path/to/warmup-archive/
└── jrdb_lh/                  ← archive_source 指向这里
    ├── __init__.py
    └── ...
```

这与前面 `PYTHONPATH` 指向父目录的用法不同。

加载器会校验归档 Python 源码的聚合 SHA-256，以及固定的历史时间边界：

| 项目 | 固定值 |
|---|---|
| Archive SHA-256 | `d545ca9a936418de50bf6ad2d8ecf67c11226bc8744cfdffb9e82016b355c392` |
| `CLOCK` | `1704456000` |
| `ORIGIN` | `2024-01-01` |
| `HORIZON` | `2024-01-06` |

因此，不能直接用任意版本的 Program 源码代替这份归档。

Adaptive 使用该 archive 进行历史 warmup。Frozen 虽然不重新 warmup，但现有输入校验仍会加载并验证 archive，所以也需要提供对应的归档和 profile。

归档源码、历史数据和实际实验 profile 需要另行准备。

## 运行环境与安装

本包要求 Python 3.11 或更新版本。原实验运行环境为 macOS Mini，使用 Python 3.13.2。

正式执行路径依赖原有 macOS sandbox 和主机环境检查。Windows 可以用于代码阅读和包级检查；正式实验运行需要满足原入口的主机与执行环境要求。

安装前，请先准备兼容的 Program 环境及其依赖，然后在该 Python 环境中安装本包：

```sh
cd /path/to/JockeyBench/ActiveResearch

python -m pip install -e . --no-deps --no-build-isolation
```

上述安装方式要求环境中已准备好 `setuptools>=68` 等构建依赖；它不会自动安装外部 Program 框架及其运行依赖。

另外：

- 原科学测试使用 `pytest>=7`；
- 图表生成使用 `matplotlib>=3.7`；
- Program 所需的其他库及版本，应沿用对应框架的环境配置。

## 实验运行前需要准备的内容

除 Python 环境和外部依赖外，正式运行还需要：

- 比赛数据和确定的比赛日程；
- 模型身份、调用参数和费率配置；
- warmup profile 及固定 archive；
- 每条轨迹独立的运行目录；
- 与当前源码和配置对应的 scientific freeze；
- 通过正式渠道配置的模型凭据；
- Frozen 对照所需的匹配 \(P_0\) 及其来源记录。

正式监督入口为 `active_research.finegrained_v3.supervisor`。它读取已准备好的运行目录，执行原有 gate 和生命周期检查。该入口不自动重启或恢复已经停止的轨迹。

代码保留了原实验的路径推导、主机检查、模型身份校验、sandbox 和资源限制。部署到新目录或新机器时，需要先核对这些路径和环境条件，并为最终部署生成新的 scientific freeze；旧实验的 manifest 不能直接用于另一套源码目录。

当前代码包含 DeepSeek/Frozen 的正式入口和身份校验。GLM、MiMo、Astra 的全部模型专属 adapter 和运行配置未包含在此目录中。

## 检查与测试

包级检查不需要调用模型：

```sh
python -B tools/verify_package.py

python -B -m unittest discover -s tests/packaging -v
```

科学测试保留在对应模块中。部分测试还需要外部 Program 测试组件、固定 archive、profile 和 fixtures，不能仅凭本目录独立运行全部测试。

测试入口及外部资源要求见 [TESTING.md](docs/TESTING.md)。

## 补充文档

- [依赖与环境说明](docs/DEPENDENCIES.md)
- [文件清单与模块用途](docs/FILE-INVENTORY.md)
- [历史及未纳入代码](docs/HISTORY-AND-EXPERIMENTS.md)
- [测试说明](docs/TESTING.md)
