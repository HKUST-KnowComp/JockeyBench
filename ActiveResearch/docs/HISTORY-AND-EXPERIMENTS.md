# 历史归档与未纳入组件

## 本轮实际移出的范围

只在本整理副本内将27个Python文件从 `src/active_research/` 移到
`history/active_research/`，没有删除文件、改写科学源码或改变原实验目录。
主包保留128个文件，包括当前方法、兼容依赖、有效测试及外部回归所需的自有支撑。

此前的41个静态候选不是最终移出名单：其中11个有效公共测试及测试包初始化文件
继续保留；其后按外部测试的反向引用又恢复3个自有文件，所以最终移出27个。
评分、退赛/并列、SemanticJSON投影、统计和当前gate测试没有因“不被入口
import”而丢弃。选择的16个测试root与依赖闭包见
[package-layout.json](../metadata/package-layout.json)。

`history/` 在默认安装路径之外，也加入 `.gitignore`，不属于当前上传候选。
归档只用于阅读开发历史，不是独立运行包；部分文件依赖仍在主包的公共模块。
旧入口不再作为本整理包支持的启动方式。正式入口是当前v3命名空间。

## 离线验收后恢复的文件

| 主包文件 | 恢复原因 |
|---|---|
| [mock_agent.py](../src/active_research/mock_agent.py) | 外部原test_active_research helper反向引用 |
| [behavioral/__init__.py](../src/active_research/behavioral/__init__.py) | 外部完整behavioral回归需要包导入 |
| [behavioral/runner.py](../src/active_research/behavioral/runner.py) | 外部原behavioral测试反向引用 |

3个Python文件均来自用户原扩展，内容原样恢复；`behavioral/__main__.py`仍在历史目录。
旧helper需要的83字节`daily_reflection_budget.json`也已按原自有配置恢复，未改值，
不计入Python数量。没有复制Modeling、外部tests或实验数据来消除依赖。

完整离线回归两边均零失败，正式16个测试文件两边均239 PASS，19个合成对照场景
通过。9份Request.data字节一致，checkpoint/state和Frame等结构保全字段后按
canonical JSON对比一致；完整范围见 [测试说明](TESTING.md)。这些恢复文件仍属于
兼容与测试支撑，不作为额外正式实验入口。

## 逐文件归档清单

| 文件 | 移出原因 |
|---|---|

| `history/active_research/__main__.py` | 旧版本启动入口，当前正式入口不调用 |
| `history/active_research/adaptive/__main__.py` | 旧版本启动入口，当前正式入口不调用 |
| `history/active_research/aligned/__main__.py` | 旧版本启动入口，当前正式入口不调用 |
| `history/active_research/behavioral/__main__.py` | 独立早期behavioral工作流 |
| `history/active_research/context_v2/__init__.py` | 独立旧context/recovery工作流 |
| `history/active_research/context_v2/test_context.py` | 独立旧context/recovery工作流 |
| `history/active_research/experience/test_protocol.py` | 旧experience完整工作流测试 |
| `history/active_research/formal_report.py` | 旧formal工作流入口/报告 |
| `history/active_research/formal_worker.py` | 旧formal工作流入口/报告 |
| `history/active_research/llm/__main__.py` | 旧版本启动入口，当前正式入口不调用 |
| `history/active_research/policy_amortized/__main__.py` | 旧版本启动入口，当前正式入口不调用 |
| `history/active_research/policy_amortized/compact_evidence/__main__.py` | 旧版本启动入口，当前正式入口不调用 |
| `history/active_research/policy_amortized/compact_evidence/tests/test_runtime.py` | 旧compact/readable替代表示预测loop测试 |
| `history/active_research/policy_amortized/readable_evidence/__main__.py` | 旧版本启动入口，当前正式入口不调用 |
| `history/active_research/policy_amortized/readable_evidence/offline.py` | 旧独立离线回放工具；当前报告所需compact offline仍在主包 |
| `history/active_research/policy_amortized/readable_evidence/operations.py` | 旧实验部署/启动工具 |
| `history/active_research/policy_amortized/readable_evidence/supervisor.py` | 独立旧readable监督入口 |
| `history/active_research/policy_amortized/readable_evidence/tests/test_runtime.py` | 旧compact/readable替代表示预测loop测试 |
| `history/active_research/policy_amortized/readable_evidence/tests/test_supervisor.py` | 旧readable supervisor测试 |
| `history/active_research/policy_amortized/semantic_json/__main__.py` | 旧版本启动入口，当前正式入口不调用 |
| `history/active_research/policy_amortized/semantic_json/offline.py` | 旧独立离线回放工具；当前报告所需compact offline仍在主包 |
| `history/active_research/policy_amortized/semantic_json/operations.py` | 旧实验部署/启动工具 |
| `history/active_research/policy_warmup/__main__.py` | 旧版本启动入口，当前正式入口不调用 |
| `history/active_research/test_daily.py` | 独立旧daily工作流测试；所需helper仍按依赖保留 |
| `history/active_research/test_reflection_normalization.py` | 独立旧daily工作流测试；所需helper仍按依赖保留 |
| `history/active_research/test_transport_recovery.py` | 独立旧恢复路线及其测试 |
| `history/active_research/transport_recovery.py` | 独立旧恢复路线及其测试 |

## 保留的旧层不是额外实验入口

主包仍有62个正式兼容文件、9个旧测试支撑实现和3个外部回归支撑文件。SemanticJSON的原投影/schema、旧
predictor/repair、账本与审计公共接口仍被当前实现复用；daily与部分aligned旧实现
则被原test helpers引用。它们未通过重写import来拆分，内容保持原字节。

## 原工作副本

| 原本地来源 | 原Active Research Python文件数 | 作用 |
|---|---:|---|
| `A2-aligned-work/active_research` | 119 | 早期Active Research基础层 |
| `Active-v3-work/active_research` | 150 | 初始v3副本 |
| `Active-v3-updater-work/active_research` | 152 | updater上下文修复副本 |
| `Active-v3-frozen-control-work/active_research` | 155 | 本整理来源，含Frozen |

这些原目录未被本轮移动、覆盖或重建freeze。原manifest保护整个源码树，不能用本
精简目录替换旧部署后继续沿用历史freeze。

## 本轮没有复制的独立组件

| 原工作区组件 | 状态 |
|---|---|
| `Frozen-v3-Control-20261011` | 单次实验准备、部署和读取脚本，带机器路径 |
| `Active-v3-Queue-20261011`、`Active-v3-Repeat-Cohort-20261008` | 队列、预算、repeat调度及一次性launch |
| GLM 429诊断适配及对应测试 | 模型专项组件，未合入公共transport |
| MiMo生命周期预算组件及测试 | 带模型和授权预算绑定，未合入公共核心 |
| GPT Responses/OAuth bridge及诊断 | 独立transport路线，未并入当前DeepSeek副本 |
| GPT6 synthetic probes | 能力探测，不是正式科学入口 |
| DeepSeek R3 Frozen recovery候选 | 未通过正式续跑验收，不接入 |
| 分析、派彩回放、附录图表脚本与结果 | 与科学执行核心分开，未纳入 |

## 外部内容

Modeling/`jrdb_lh` 源码及其测试、固定哈希归档、第三方源码、数据、checkpoint、
保存policy、模型响应、日志和凭据均未复制。当前来源仍是本地DeepSeek/Frozen树，
不代表GLM、MiMo、Astra各模型最终adapter全套。外部引用方式见
[依赖说明](DEPENDENCIES.md)。
