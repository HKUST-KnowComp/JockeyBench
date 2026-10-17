# 文件清单与精简结果

本轮仅整理自有扩展副本，科学源码内容未改。Modeling实现、原科学目录和实验产物未纳入。
准备上传的逐文件范围见 [UPLOAD-FILES.txt](../metadata/UPLOAD-FILES.txt)；本页的主包源码表
列出128个Python文件，另有1份自有JSON配置。27个history文件只以排除项名称记录，
不随发布包提供。包装工具、文档和元数据同样列在上传清单中。

| 分类 | Python文件数 |
|---|---:|
| 正式Active-v3/Frozen核心 | 28 |
| 正式入口兼容依赖 | 62 |
| 测试支撑的旧实现 | 9 |
| 外部完整回归支撑的自有文件 | 3 |
| 保留的原测试相关文件（含测试包初始化） | 26 |
| **主包合计** | **128** |
| 主包外历史归档 | 27 |
| **原始总数，全部保全** | **155** |

显式选择16个有效测试文件作为root，其余保留测试文件包含被引用的helper和初始化文件。
完整离线回归两边均零失败，正式16个测试文件两边均239 PASS；19个合成场景对照
通过。9份Request.data字节一致，checkpoint/state和Frame等结构保全字段后按
canonical JSON对比一致；具体适用范围见 [测试说明](TESTING.md)。
早先41个候选中有11个有效测试/初始化文件予以保留；外部回归的反向引用又要求恢复
3个自有文件，故最终保留主包128个、历史27个。

另有1个非Python资产：`src/active_research/daily_reflection_budget.json`，83字节，
原样恢复以供旧helper收集。SHA-256为
`1ee97de1d8637b580e7cd6720c0a42ef4f03264d812dc9cb36135fae89904d42`；
它不计入155个Python文件，安装范围由package-data及包级检查确认。

来源与SHA-256见 [source manifest](../metadata/source-manifest.json)；选择依据与测试root见 [package layout](../metadata/package-layout.json)。

## 主包模块

| 模块 | Python文件数 |
|---|---:|
| `(root)` | 10 |
| `adaptive` | 5 |
| `aligned` | 13 |
| `behavioral` | 2 |
| `experience` | 6 |
| `finegrained_v3` | 36 |
| `llm` | 3 |
| `policy_amortized` | 48 |
| `policy_warmup` | 5 |

## (root)

| 文件 | 分类 |
|---|---|
| [src/active_research/__init__.py](../src/active_research/__init__.py) | 正式入口兼容依赖 |
| [src/active_research/common_scoring.py](../src/active_research/common_scoring.py) | 正式入口兼容依赖 |
| [src/active_research/daily.py](../src/active_research/daily.py) | 测试依赖的旧实现 |
| [src/active_research/daily_report.py](../src/active_research/daily_report.py) | 测试依赖的旧实现 |
| [src/active_research/daily_session.py](../src/active_research/daily_session.py) | 测试依赖的旧实现 |
| [src/active_research/mock_agent.py](../src/active_research/mock_agent.py) | 外部完整回归支撑的自有文件 |
| [src/active_research/reflection_normalization.py](../src/active_research/reflection_normalization.py) | 测试依赖的旧实现 |
| [src/active_research/runner.py](../src/active_research/runner.py) | 正式入口兼容依赖 |
| [src/active_research/test_common_scoring.py](../src/active_research/test_common_scoring.py) | 有效公共测试 |
| [src/active_research/test_daily_session.py](../src/active_research/test_daily_session.py) | 原测试helper/测试包初始化 |

## adaptive

| 文件 | 分类 |
|---|---|
| [src/active_research/adaptive/__init__.py](../src/active_research/adaptive/__init__.py) | 正式入口兼容依赖 |
| [src/active_research/adaptive/domains.py](../src/active_research/adaptive/domains.py) | 正式入口兼容依赖 |
| [src/active_research/adaptive/model.py](../src/active_research/adaptive/model.py) | 正式入口兼容依赖 |
| [src/active_research/adaptive/runner.py](../src/active_research/adaptive/runner.py) | 正式入口兼容依赖 |
| [src/active_research/adaptive/test_adaptive.py](../src/active_research/adaptive/test_adaptive.py) | 原测试helper/测试包初始化 |

## aligned

| 文件 | 分类 |
|---|---|
| [src/active_research/aligned/__init__.py](../src/active_research/aligned/__init__.py) | 正式入口兼容依赖 |
| [src/active_research/aligned/audit.py](../src/active_research/aligned/audit.py) | 正式入口兼容依赖 |
| [src/active_research/aligned/delivery.py](../src/active_research/aligned/delivery.py) | 正式入口兼容依赖 |
| [src/active_research/aligned/frame.py](../src/active_research/aligned/frame.py) | 测试依赖的旧实现 |
| [src/active_research/aligned/integrity.py](../src/active_research/aligned/integrity.py) | 正式入口兼容依赖 |
| [src/active_research/aligned/model.py](../src/active_research/aligned/model.py) | 正式入口兼容依赖 |
| [src/active_research/aligned/prepare.py](../src/active_research/aligned/prepare.py) | 测试依赖的旧实现 |
| [src/active_research/aligned/report.py](../src/active_research/aligned/report.py) | 测试依赖的旧实现 |
| [src/active_research/aligned/runner.py](../src/active_research/aligned/runner.py) | 测试依赖的旧实现 |
| [src/active_research/aligned/scoring.py](../src/active_research/aligned/scoring.py) | 正式入口兼容依赖 |
| [src/active_research/aligned/specification.py](../src/active_research/aligned/specification.py) | 正式入口兼容依赖 |
| [src/active_research/aligned/tests/test_aligned.py](../src/active_research/aligned/tests/test_aligned.py) | 原测试helper/测试包初始化 |
| [src/active_research/aligned/tests/test_graceful.py](../src/active_research/aligned/tests/test_graceful.py) | 原测试helper/测试包初始化 |

## behavioral

| 文件 | 分类 |
|---|---|
| [src/active_research/behavioral/__init__.py](../src/active_research/behavioral/__init__.py) | 外部完整回归支撑的自有文件 |
| [src/active_research/behavioral/runner.py](../src/active_research/behavioral/runner.py) | 外部完整回归支撑的自有文件 |

它们由外部原测试反向引用，不作为正式v3入口；旧`behavioral/__main__.py`仍在历史目录。

## experience

| 文件 | 分类 |
|---|---|
| [src/active_research/experience/__init__.py](../src/active_research/experience/__init__.py) | 正式入口兼容依赖 |
| [src/active_research/experience/frame.py](../src/active_research/experience/frame.py) | 正式入口兼容依赖 |
| [src/active_research/experience/model.py](../src/active_research/experience/model.py) | 正式入口兼容依赖 |
| [src/active_research/experience/report.py](../src/active_research/experience/report.py) | 正式入口兼容依赖 |
| [src/active_research/experience/runner.py](../src/active_research/experience/runner.py) | 正式入口兼容依赖 |
| [src/active_research/experience/test_frame.py](../src/active_research/experience/test_frame.py) | 原测试helper/测试包初始化 |

## finegrained_v3

| 文件 | 分类 |
|---|---|
| [src/active_research/finegrained_v3/__init__.py](../src/active_research/finegrained_v3/__init__.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/__main__.py](../src/active_research/finegrained_v3/__main__.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/audit.py](../src/active_research/finegrained_v3/audit.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/audit_live.py](../src/active_research/finegrained_v3/audit_live.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/control.py](../src/active_research/finegrained_v3/control.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/domains.py](../src/active_research/finegrained_v3/domains.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/frame.py](../src/active_research/finegrained_v3/frame.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/frozen_inputs.py](../src/active_research/finegrained_v3/frozen_inputs.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/frozen_report.py](../src/active_research/finegrained_v3/frozen_report.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/gate.py](../src/active_research/finegrained_v3/gate.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/lifecycle.py](../src/active_research/finegrained_v3/lifecycle.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/marginal_cost.py](../src/active_research/finegrained_v3/marginal_cost.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/native_replay.py](../src/active_research/finegrained_v3/native_replay.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/policy_builder.py](../src/active_research/finegrained_v3/policy_builder.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/policy_contract.py](../src/active_research/finegrained_v3/policy_contract.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/policy_cpu.py](../src/active_research/finegrained_v3/policy_cpu.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/policy_executor.py](../src/active_research/finegrained_v3/policy_executor.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/predictor.py](../src/active_research/finegrained_v3/predictor.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/prepare.py](../src/active_research/finegrained_v3/prepare.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/report.py](../src/active_research/finegrained_v3/report.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/runtime.py](../src/active_research/finegrained_v3/runtime.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/source_binding.py](../src/active_research/finegrained_v3/source_binding.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/statistics.py](../src/active_research/finegrained_v3/statistics.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/supervisor.py](../src/active_research/finegrained_v3/supervisor.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/tests/__init__.py](../src/active_research/finegrained_v3/tests/__init__.py) | v3/Frozen原测试相关 |
| [src/active_research/finegrained_v3/tests/test_frozen_control.py](../src/active_research/finegrained_v3/tests/test_frozen_control.py) | v3/Frozen原测试相关 |
| [src/active_research/finegrained_v3/tests/test_integration.py](../src/active_research/finegrained_v3/tests/test_integration.py) | v3/Frozen原测试相关 |
| [src/active_research/finegrained_v3/tests/test_marginal_cost.py](../src/active_research/finegrained_v3/tests/test_marginal_cost.py) | v3/Frozen原测试相关 |
| [src/active_research/finegrained_v3/tests/test_timing.py](../src/active_research/finegrained_v3/tests/test_timing.py) | v3/Frozen原测试相关 |
| [src/active_research/finegrained_v3/tests/test_updater_context.py](../src/active_research/finegrained_v3/tests/test_updater_context.py) | v3/Frozen原测试相关 |
| [src/active_research/finegrained_v3/tests/test_v3.py](../src/active_research/finegrained_v3/tests/test_v3.py) | v3/Frozen原测试相关 |
| [src/active_research/finegrained_v3/tests/test_warmup_v3.py](../src/active_research/finegrained_v3/tests/test_warmup_v3.py) | v3/Frozen原测试相关 |
| [src/active_research/finegrained_v3/transport.py](../src/active_research/finegrained_v3/transport.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/updater_context.py](../src/active_research/finegrained_v3/updater_context.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/warmup.py](../src/active_research/finegrained_v3/warmup.py) | 正式v3/Frozen核心 |
| [src/active_research/finegrained_v3/warmup_context_fit.py](../src/active_research/finegrained_v3/warmup_context_fit.py) | 正式v3/Frozen核心 |

## llm

| 文件 | 分类 |
|---|---|
| [src/active_research/llm/__init__.py](../src/active_research/llm/__init__.py) | 正式入口兼容依赖 |
| [src/active_research/llm/agent.py](../src/active_research/llm/agent.py) | 正式入口兼容依赖 |
| [src/active_research/llm/runner.py](../src/active_research/llm/runner.py) | 正式入口兼容依赖 |

## policy_amortized

| 文件 | 分类 |
|---|---|
| [src/active_research/policy_amortized/__init__.py](../src/active_research/policy_amortized/__init__.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/audit.py](../src/active_research/policy_amortized/audit.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/compact_evidence/__init__.py](../src/active_research/policy_amortized/compact_evidence/__init__.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/compact_evidence/audit.py](../src/active_research/policy_amortized/compact_evidence/audit.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/compact_evidence/expand.py](../src/active_research/policy_amortized/compact_evidence/expand.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/compact_evidence/lifecycle.py](../src/active_research/policy_amortized/compact_evidence/lifecycle.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/compact_evidence/offline.py](../src/active_research/policy_amortized/compact_evidence/offline.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/compact_evidence/project.py](../src/active_research/policy_amortized/compact_evidence/project.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/compact_evidence/runtime.py](../src/active_research/policy_amortized/compact_evidence/runtime.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/compact_evidence/schema.py](../src/active_research/policy_amortized/compact_evidence/schema.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/compact_evidence/smoke_report.py](../src/active_research/policy_amortized/compact_evidence/smoke_report.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/compact_evidence/tests/test_codec.py](../src/active_research/policy_amortized/compact_evidence/tests/test_codec.py) | 有效公共测试 |
| [src/active_research/policy_amortized/frame.py](../src/active_research/policy_amortized/frame.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/lifecycle.py](../src/active_research/policy_amortized/lifecycle.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/model_usage.py](../src/active_research/policy_amortized/model_usage.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/policy_builder.py](../src/active_research/policy_amortized/policy_builder.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/policy_contract.py](../src/active_research/policy_amortized/policy_contract.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/policy_executor.py](../src/active_research/policy_amortized/policy_executor.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/predictor.py](../src/active_research/policy_amortized/predictor.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/prepare.py](../src/active_research/policy_amortized/prepare.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/readable_evidence/__init__.py](../src/active_research/policy_amortized/readable_evidence/__init__.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/readable_evidence/audit.py](../src/active_research/policy_amortized/readable_evidence/audit.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/readable_evidence/codec.py](../src/active_research/policy_amortized/readable_evidence/codec.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/readable_evidence/lifecycle.py](../src/active_research/policy_amortized/readable_evidence/lifecycle.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/readable_evidence/report.py](../src/active_research/policy_amortized/readable_evidence/report.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/readable_evidence/runtime.py](../src/active_research/policy_amortized/readable_evidence/runtime.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/readable_evidence/schema.py](../src/active_research/policy_amortized/readable_evidence/schema.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/readable_evidence/tests/__init__.py](../src/active_research/policy_amortized/readable_evidence/tests/__init__.py) | 原测试helper/测试包初始化 |
| [src/active_research/policy_amortized/readable_evidence/tests/test_codec.py](../src/active_research/policy_amortized/readable_evidence/tests/test_codec.py) | 有效公共测试 |
| [src/active_research/policy_amortized/readable_evidence/tests/test_report.py](../src/active_research/policy_amortized/readable_evidence/tests/test_report.py) | 有效公共测试 |
| [src/active_research/policy_amortized/report.py](../src/active_research/policy_amortized/report.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/semantic_json/__init__.py](../src/active_research/policy_amortized/semantic_json/__init__.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/semantic_json/gate.py](../src/active_research/policy_amortized/semantic_json/gate.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/semantic_json/lifecycle.py](../src/active_research/policy_amortized/semantic_json/lifecycle.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/semantic_json/report.py](../src/active_research/policy_amortized/semantic_json/report.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/semantic_json/runtime.py](../src/active_research/policy_amortized/semantic_json/runtime.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/semantic_json/supervisor.py](../src/active_research/policy_amortized/semantic_json/supervisor.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/semantic_json/tests/__init__.py](../src/active_research/policy_amortized/semantic_json/tests/__init__.py) | 原测试helper/测试包初始化 |
| [src/active_research/policy_amortized/semantic_json/tests/test_gate.py](../src/active_research/policy_amortized/semantic_json/tests/test_gate.py) | 有效公共测试 |
| [src/active_research/policy_amortized/semantic_json/tests/test_report.py](../src/active_research/policy_amortized/semantic_json/tests/test_report.py) | 有效公共测试 |
| [src/active_research/policy_amortized/semantic_json/tests/test_runtime.py](../src/active_research/policy_amortized/semantic_json/tests/test_runtime.py) | 有效公共测试 |
| [src/active_research/policy_amortized/semantic_json/tests/test_supervisor_lifecycle.py](../src/active_research/policy_amortized/semantic_json/tests/test_supervisor_lifecycle.py) | 有效公共测试 |
| [src/active_research/policy_amortized/semantic_json/tests/test_view.py](../src/active_research/policy_amortized/semantic_json/tests/test_view.py) | 有效公共测试 |
| [src/active_research/policy_amortized/semantic_json/view.py](../src/active_research/policy_amortized/semantic_json/view.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/service.py](../src/active_research/policy_amortized/service.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/specification.py](../src/active_research/policy_amortized/specification.py) | 正式入口兼容依赖 |
| [src/active_research/policy_amortized/tests/test_policy.py](../src/active_research/policy_amortized/tests/test_policy.py) | 原测试helper/测试包初始化 |
| [src/active_research/policy_amortized/transport.py](../src/active_research/policy_amortized/transport.py) | 正式入口兼容依赖 |

## policy_warmup

| 文件 | 分类 |
|---|---|
| [src/active_research/policy_warmup/__init__.py](../src/active_research/policy_warmup/__init__.py) | 正式入口兼容依赖 |
| [src/active_research/policy_warmup/archive.py](../src/active_research/policy_warmup/archive.py) | 正式入口兼容依赖 |
| [src/active_research/policy_warmup/builder.py](../src/active_research/policy_warmup/builder.py) | 正式入口兼容依赖 |
| [src/active_research/policy_warmup/lifecycle.py](../src/active_research/policy_warmup/lifecycle.py) | 测试依赖的旧实现 |
| [src/active_research/policy_warmup/tests/test_warmup.py](../src/active_research/policy_warmup/tests/test_warmup.py) | 原测试helper/测试包初始化 |

## 已移出主包的历史文件

这些文件不在src下，不进入安装包；归档不是独立可运行的历史复现包。

| 归档文件 |
|---|
| `history/active_research/__main__.py` |
| `history/active_research/adaptive/__main__.py` |
| `history/active_research/aligned/__main__.py` |
| `history/active_research/behavioral/__main__.py` |
| `history/active_research/context_v2/__init__.py` |
| `history/active_research/context_v2/test_context.py` |
| `history/active_research/experience/test_protocol.py` |
| `history/active_research/formal_report.py` |
| `history/active_research/formal_worker.py` |
| `history/active_research/llm/__main__.py` |
| `history/active_research/policy_amortized/__main__.py` |
| `history/active_research/policy_amortized/compact_evidence/__main__.py` |
| `history/active_research/policy_amortized/compact_evidence/tests/test_runtime.py` |
| `history/active_research/policy_amortized/readable_evidence/__main__.py` |
| `history/active_research/policy_amortized/readable_evidence/offline.py` |
| `history/active_research/policy_amortized/readable_evidence/operations.py` |
| `history/active_research/policy_amortized/readable_evidence/supervisor.py` |
| `history/active_research/policy_amortized/readable_evidence/tests/test_runtime.py` |
| `history/active_research/policy_amortized/readable_evidence/tests/test_supervisor.py` |
| `history/active_research/policy_amortized/semantic_json/__main__.py` |
| `history/active_research/policy_amortized/semantic_json/offline.py` |
| `history/active_research/policy_amortized/semantic_json/operations.py` |
| `history/active_research/policy_warmup/__main__.py` |
| `history/active_research/test_daily.py` |
| `history/active_research/test_reflection_normalization.py` |
| `history/active_research/test_transport_recovery.py` |
| `history/active_research/transport_recovery.py` |
