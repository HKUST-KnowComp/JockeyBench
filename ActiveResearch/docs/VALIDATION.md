# 离线验收摘要

这份脱敏摘要随发布候选提供。只包含验证范围、计数、哈希和限制，不携原始请求正文、
checkpoint、比赛记录、模型响应、数据、凭据或机器路径。机器审计原件留在本地，
不属于上传候选。聚合机器可读结果见
[release-validation.json](../metadata/release-validation.json)。

## 源码与完整离线回归

本地精简前完整参考树有155个自有科学Python文件。发布主包保留128个；另外27个
独立历史文件仅在本地history归档，全部155个原文件哈希保持不变。未复制或修改
Modeling、外部测试、固定archive或原实验产物。原样恢复3个必要自有兼容文件和
83字节daily配置；没有修改科学逻辑。

| 范围 | PASS | FAIL | SKIP |
|---|---:|---:|---:|
| 精简前完整默认离线回归 | 1284 | 0 | 1 |
| 精简后完整默认离线回归 | 1195 | 0 | 1 |
| 精简前保留的16个正式测试文件 | 239 | 0 | 0 |
| 精简后保留的16个正式测试文件 | 239 | 0 | 0 |

共同1196个nodeid的结果及执行次数完全一致；精简后没有新增nodeid。83个只在旧树
中的nodeid对应89次执行，均属于排除的历史测试。数量差别包含一个原来重复执行7次
的Normalization nodeid，不是少跑当前保留的测试。

两边唯一共同skip是原有显式启用的单任务Mini重资源测试
`tests.test_resources::test_native_job_exceeds_old_memory_wall_and_cpu_caps`。
它不是依赖缺失造成的skip，本轮没有强制开启。完整离线验收使用Python 3.13.2和
原macOS Mini环境；运行耗时不构成性能等价证明。

## 请求、证据、账本、Frame与评分

19个合成场景由14个交付/获取场景和5个生命周期场景组成：

- 4个完整交付场景覆盖direct、repair、fallback及checkpoint交付。
- 10个获取场景覆盖history progressive扩展、重复获取、checkpoint重放、training
  投影和condition_stats复用；这些场景没有完整交付Frame/评分，明确记为N/A。
- 5个生命周期场景覆盖updater变更、keep、无效变更保留、fresh warmup保存及Frozen。

9份原构造`Request.data`请求体逐字节一致；13份原fake caller的packed输入一致。
后者是mock调用载荷，不等于已验证相应真实HTTP wire format。完整checkpoint/state、
证据/provenance/ControlView、ledger/owned cache/reconcile、Frame和评分组件保留
全部字段后按canonical JSON比较；5个生命周期场景的77份产物一致。没有删字段或
以正文摘要代替产物比较。固定测试时钟用于确定性比较；这不是运行时间或性能证明。

所有场景使用合成fixture和原mock，provider调用0、API支出0、真实trajectory启动0。

## 验证环境与适用限制

完整回归从两个独立审计副本执行，只读引用外部兼容框架、原测试/helper、固定warmup
profile/archive；未将这些外部资产复制进发布包。独立审计控制层限制网络、凭据、
子进程和写入；测试写入均在新的审计范围内。

为运行原测试，控制层对限定的旧fixture路径使用合成SQLite、真实Python runtime
副本和shadow配置；原验证函数和科学参数保持不变。部分jail测试追加网络禁用规则，
两个prompt fake-launcher使用独立OS沙箱。这些是离线验收环境适配，不是科学算法
或请求改写；具体测试范围还见 [TESTING.md](TESTING.md)。

该结论为**离线行为验收通过**，不等于正式部署、provider适配或launch验收。当前
`src/`布局的PROJECT路径、旧凭据定位、host标记、macOS sandbox及fresh scientific
freeze仍须在未来获准部署时单独验收。没有复用历史freeze启动，也没有创建host标记。
当前源树没有GLM、MiMo、Astra的全部最终专项adapter，不声称跨模型正式复现通过。

本轮包装只改变文件布局与文档，未执行模型调用、warmup、科学gate、324轨迹或上传。
