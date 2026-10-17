# JockeyBench

[English](README.md) | 简体中文

JockeyBench 评测 LLM 智能体在研究预算约束下开发预测程序的能力。
**Program 与 Active Research 是同一实验框架的两种设定**：在仓库根目录安装一次，通过同一个
`jockeybench` 命令使用。

| 设定 | 智能体开发的内容 | 实验臂 |
| --- | --- | --- |
| **Program** | 输出完整排名和获胜概率的 Python 预测程序 | A：每日修订；S：冻结初始程序 |
| **Active Research** | 决定查询哪些信息的获取策略，由预测器根据证据预测 | Adaptive：每日更新策略；Frozen：使用相同初始策略且不更新 |

两种设定共用按时间门控的数据服务、计费、预测校验和评分引擎。公开的 Research 整合版使用独立的
`jockeybench-research-v1` profile；这是新的部署配置，不能视作历史 DeepSeek/Frozen 实验的逐字节复现。
详见[架构](docs/ARCHITECTURE.md)和 [Research 说明](docs/RESEARCH.md)。

**不分发任何数据。** 使用者需自行订阅 JRDB，再用仓库内的导入器构建私有数据库。2026 年数据仍保持封存。
详见[数据说明](docs/DATA.md)。

## 统一安装

使用 **Python 3.13**。支持 macOS 的 `sandbox-exec`，以及 Linux 的 bubblewrap 与 libseccomp。
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
.venv/bin/jockeybench program --help
.venv/bin/jockeybench research --help
```

现在只有一个 Python 包 `jockeybench`，Research 位于 `jockeybench.research`。公开 Research 入口无需
另装 `active_research`、`jrdb_lh` 或提供外部 warmup 源码归档。
旧版源码整理记录保留在[来源说明](provenance/active-research/README.md)中。

## 使用合成数据检查

```bash
.venv/bin/pytest
JRDB_NATIVE=1 .venv/bin/pytest -m native
.venv/bin/python -B scripts/verify_research_provenance.py
```

默认测试使用合成输入，不调用模型。源码内嵌的历史 Research 测试不被默认收集；它们不能证明新 profile
复现了历史轨迹。CI 覆盖 Ubuntu 和 macOS。贡献者另安装 `requirements-dev.txt`。

## 运行两种设定

真实数据读取和模型调用只在已配置 `.experiment-host` 标记的实验主机上进行。数据库、运行目录和凭据
均在该主机本地配置。不要把标记复制到开发机器，也不要在开发机器上创建它。

```bash
export JRDB_SOURCE="$PWD/data/private/expanded.sqlite" JRDB_RUNS="$PWD/runs"

# Program：不调用模型的离线参考程序。
.venv/bin/jockeybench program --config configs/smoke.json doctor
.venv/bin/jockeybench program --config configs/smoke.json run --run-id program-r2 --reference R2

# Research：调用模型的 Adaptive，以及匹配同一初始策略的 Frozen。
.venv/bin/jockeybench research --config configs/research.json run \
  --run-id research-adaptive --provider-config provider.local.json --arm adaptive
.venv/bin/jockeybench research --config configs/research.json run \
  --run-id research-frozen --provider-config provider.local.json --arm frozen \
  --initial-from research-adaptive
```

Research 会调用配置的模型并可能产生费用。provider 文件和运行目录是用户自行准备的私有输入，
这些命令不是已经完成实验的记录。具体格式见 [Research 配置](docs/RESEARCH.md)。原有
`jockeybench --config ...` 命令仍默认选择 Program。另见[模型配置](docs/MODELS.md)与
[Program 复现说明](docs/REPRODUCING.md)。

## 目录

```text
src/jockeybench/             共享引擎、Program 设定及命令行入口
src/jockeybench/research/    Active Research 设定及兼容组件
configs/                    公开配置模板
tests/                      当前支持的合成测试与包装检查
provenance/active-research/ 原源码整理记录与迁移哈希
```

Program 代码保留原 MIT 许可。原 Research 源码整理包没有单独的许可声明，本次合并目录不扩大原 MIT
授权范围。详见[许可范围](docs/LICENSE-SCOPE.md)。
