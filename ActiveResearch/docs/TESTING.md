# 测试选择、检查与缺失依赖

## 本轮执行的整理检查

从本整理目录运行：

```text
python -B tools/verify_package.py
python -B -m unittest discover -s tests/packaging -v
```

发布包默认不要求history源码。完整本地审阅副本可用
`python -B tools/verify_package.py --require-history` 强制核验全部155个原科学文件；
该可选模式不适用于不携历史源码的发布包。

这些检查解析AST、编译语法、核对已发布源码哈希、验证来源分区元数据和默认安装范围；
发布候选不包含history源码，检查不要求这些排除文件存在；在完整本地审阅副本中
可另行核验完整main/history分区。
不会import科学模块、执行policy/predictor、连接数据源、启动子进程或调用provider。
静态分析工具只导入本轮新增的检查脚本。

155个原科学Python文件均在本地保全：128个在主包，27个在本地history；内容保持原字节。
发布候选只携主包128个及历史排除项的哈希记录，不携27个历史源码。
包级测试不是完整科学回归，结果见 [release-validation.json](../metadata/release-validation.json)。

## 完整离线回归结果

Mini上两个独立副本的最终完整回归均为零失败：

| 范围 | PASS | FAIL | SKIP | 耗时 |
|---|---:|---:|---:|---:|
| 精简前完整回归 | 1284 | 0 | 1 | 175.916秒 |
| 精简后完整回归 | 1195 | 0 | 1 | 166.734秒 |
| 精简前正式保留的16个测试文件 | 239 | 0 | 0 | 见原记录 |
| 精简后正式保留的16个测试文件 | 239 | 0 | 0 | 见原记录 |

共同1196个nodeid的结果及执行次数完全相同，没有candidate-only nodeid。移出的
83个独立历史nodeid对应89次执行；其中一个Normalization missingfields nodeid
在原收集中重复执行7次，因此唯一nodeid数量与执行次数不同。这些耗时是本次
受控回归的观测，不用于性能优劣结论。

两边同一项跳过：`tests.test_resources::test_native_job_exceeds_old_memory_wall_and_cpu_caps`，
原因为`Explicit one-job Mac Mini resource validation`。这是原有opt-in重资源测试，
本轮未强制开启；它没有因缺少依赖而跳过。

完整回归、来源校验、guard与控制层适配证据见
[离线验收摘要](VALIDATION.md)。

## 合成请求、状态与生命周期对照

另行完成14个原测试路径合成场景：4个完整交付场景覆盖direct、repair、fallback及
checkpoint交付；10个纯获取场景覆盖progressive/history、training、condition_stats
和checkpoint重放。比较模型请求、完整证据/provenance/ControlView、ledger/owned
cache/reconcile、Frame和评分组件。10个纯获取场景的完整交付Frame与评分标为N/A，
没有以缺失产物冒充一致。

5个额外生命周期场景覆盖合法updater变更、keep、无效变更保留、fresh warmup保存、
Frozen无updater。两边这5个场景的77份合成产物全部一致。合计19个场景均PASS。

9份实际构造的`Request.data`请求体逐字节一致，未归一化；13份原fake caller的
packed输入也一致，这13份是mock调用载荷，不能当作HTTP wire证据。
checkpoint/state、Frame及其余结构按保留全部字段的canonical JSON比较一致；
这项结论针对合成结构内容，不声称原磁盘checkpoint封装字节一致。

这些调用使用原测试HTTP mock、fake caller和内存合成fixture，没有真实模型或
真实比赛预测。probe的测试时钟和CPU测量采用固定策略，字段仍保留；原native
组件的执行仍受审计guard约束，因此该对照不用于CPU或时延性能比较。

## 选定的16个科学测试文件

文件仍在 `src/active_research` 的原位置，原内容不变。两边这16个文件均通过239项测试。
完整模块名见 [package-layout.json](../metadata/package-layout.json) 的 `selected_test_roots`。

| 测试文件，相对active_research | 当前保留用途 |
|---|---|
| `finegrained_v3/tests/test_v3.py` | 当前信息状态、契约、预测/释放与V2兼容 |
| `finegrained_v3/tests/test_integration.py` | 获取、预测、释放、Frame整合 |
| `finegrained_v3/tests/test_marginal_cost.py` | 边际购买、cache、ledger和重放不重买 |
| `finegrained_v3/tests/test_timing.py` | 来源校验与原CPU限制 |
| `finegrained_v3/tests/test_updater_context.py` | updater上下文、合法keep、验证与保存 |
| `finegrained_v3/tests/test_warmup_v3.py` | warmup上下文拟合与初始化边界 |
| `finegrained_v3/tests/test_frozen_control.py` | exact-P0绑定及Frozen不更新 |
| `test_common_scoring.py` | 共同评分、退赛、并列、交付与费用加权 |
| `policy_amortized/compact_evidence/tests/test_codec.py` | 当前投影复用的字段/schema与可逆性契约 |
| `policy_amortized/readable_evidence/tests/test_codec.py` | 保留codec组件的值/类型/缺失处理 |
| `policy_amortized/readable_evidence/tests/test_report.py` | 当前报告链的统计及未知费用处理 |
| `policy_amortized/semantic_json/tests/test_view.py` | 当前SemanticJSON投影和无损内容 |
| `policy_amortized/semantic_json/tests/test_report.py` | 当前复用的provider/representation报告 |
| `policy_amortized/semantic_json/tests/test_gate.py` | 当前复用的gate、barrier和retry辅助 |
| `policy_amortized/semantic_json/tests/test_runtime.py` | v3兼容验证使用的V2预测参照 |
| `policy_amortized/semantic_json/tests/test_supervisor_lifecycle.py` | 当前审计复用的持久化不变量 |

主包26个测试相关Python文件不等于26个测试文件或测试用例：除上面16个文件外，
还有7个旧test helper和3个测试包初始化文件。

## 为什么部分旧测试和daily实现还留着

v3测试从旧测试模块导入fixtures、Response、SETTINGS和合成记录。为不改科学源码
或测试代码，下列helper及它们的依赖仍在主包：

- `adaptive/test_adaptive.py`
- `aligned/tests/test_aligned.py`、`test_graceful.py`
- `experience/test_frame.py`
- `policy_amortized/tests/test_policy.py`
- `policy_warmup/tests/test_warmup.py`
- `test_daily_session.py`

它们进一步需要旧aligned辅助实现、daily系列、reflection_normalization和旧warmup
lifecycle。这些文件标为测试支撑，不是额外发布的正式实验入口。本轮没有把fixtures
抽取到新模块，也没有删除旧文件中的测试函数来制造更小的数字。

进一步检查外部测试的反向依赖后，已原样恢复`mock_agent.py`，以及
`behavioral/__init__.py`、`behavioral/runner.py`。前者由原`test_active_research`
helper引用，后两者由外部behavioral回归引用。它们仍是用户自己的扩展文件；没有
复制外部测试或Modeling实现，也没有恢复behavioral旧启动入口。

旧helper在collection时读取的`daily_reflection_budget.json`已从原自有配置原样恢复：
83字节，SHA-256为
`1ee97de1d8637b580e7cd6720c0a42ef4f03264d812dc9cb36135fae89904d42`。
没有修改其值；该非Python资产不改变16个测试root或26个测试相关Python文件的数量。

## 完整离线回归的外部依赖

| 依赖 | 当前状态 |
|---|---|
| 外部 `jrdb_lh` | Mini既有框架已核验与原副本源码hash一致，仅只读引用，不复制 |
| 外部 `conftest`、`test_active_research` | 引用Mini既有原测试/helper；不纳入本包，不修改 |
| 同层 `daily_reflection_budget.json` | 已原样恢复83字节自有配置，供helper收集 |
| 固定warmup profile与archive | 在Mini已有；边界及archive hash匹配，仅只读引用，不纳入本包 |
| 原host、路径和macOS sandbox | 科学guard保留；测试路径适配单独审计，整理目录没有部署验收 |

默认pytest仍只收集 `tests/packaging`。上述完整离线回归在独立副本中只读引用外部
资源，所有测试写入限定在新审计目录，原实验产物和Modeling保持不变。网络、凭据
和写入guard由独立审计控制层强制执行，没有provider请求或真实trajectory。

为使旧测试验证真实原函数，审计控制层将精确选定的旧fixture配置路径指向新目录内
的合成SQLite和真实Python runtime副本；原check_config及科学参数保持不变。原有
jail测试的profile只追加网络禁用规则；两个prompt fake-launcher测试使用独立OS
沙箱；null-price负测试的导入实例ROOT指向新shadow目录。来源hash、限定测试与
控制层变更均另行记录，没有修改科学源、原测试或原配置文件。这些适配属于离线
验收环境，不是科学请求parity的一部分，也不表示整理包可直接启动正式实验。

## 历史测试

旧context、recovery、独立daily/protocol及替代表示runtime等测试随对应历史组件
移入 `history/`；它们不作为当前16个测试root。历史目录不在默认安装路径，不是
可以单独直接运行的历史复现包。
