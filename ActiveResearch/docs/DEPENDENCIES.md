# 依赖边界与模块关系

## 正式入口与必要的用户兼容代码

```text
finegrained_v3.supervisor
  -> 子进程入口 active_research.finegrained_v3
     -> lifecycle.execute
        -> Adaptive: warmup -> 初始获取程序
        -> Frozen: frozen_inputs -> 匹配的 exact P0
        -> policy_executor / policy_contract / policy_cpu
           -> control / domains / marginal_cost
           -> source_binding / native_replay
        -> runtime / predictor / transport
        -> 每场 seal -> 日间 release -> frame
        -> Adaptive: updater_context / policy_builder
        -> Frozen: 保持 P0，不执行 updater
        -> gate / audit / report / frozen_report
```

此图是原实现的阅读索引，不是经过重构的新调用路径。原科学源码保持字节不变；
具体保留文件及历史位置见 [源码清单](../metadata/source-manifest.json)、
[文件清单](FILE-INVENTORY.md) 与 [依赖映射](../metadata/dependency-map.json)。
`history/` 不在默认安装的 `src` 路径中，不应将它整体添加到 `PYTHONPATH` 以覆盖
正式包。历史测试的适用范围单独见 [测试说明](TESTING.md)。

v3 必要的旧层包括 `policy_amortized`、`policy_warmup`、`aligned`、`adaptive`、
`experience`、`llm` 和根目录公共模块。必须区分实际函数复用与导入耦合：

| 依赖链 | 保留原因 |
|---|---|
| v3 runtime -> 旧 policy predictor、aligned delivery | `FunctionType` 复用旧预测/repair 函数的代码和 globals，不能只搜索直接调用名 |
| v3 lifecycle -> 旧 lifecycle | 使用原 `coordinate` 和冻结校验；不执行旧实验主循环 |
| v3 lifecycle -> experience runner | 使用原日程验证函数；模块顶层还会导入旧 runner/model 基础层 |
| v3 audit_live -> SemanticJSON supervisor | 复用 invariant；其顶层 imports 又加载旧 lifecycle/runtime，零源码修改时仍需保留 |
| v3 report -> SemanticJSON report -> readable report -> compact offline | 真正复用统计函数；`offline.py` 的名称不代表整个文件可删除 |
| v3 warmup fitter -> 原 warmup builder | 存在顶层导入耦合；本轮不删除未使用 import 来改变源码 |
| SemanticJSON view -> compact project/schema | 保留原字段语义、canonical equality 与 evidence 审计 |

包的 `__init__.py` 同样是依赖的一部分。未进入正式入口闭包的旧入口、运维脚本和
独立历史模块另放 `history/`，不是在既有实验部署中删除文件。

## Modeling 仅作为外部 `jrdb_lh` 依赖

本扩展引用原 Modeling 基础框架的 Python 命名空间 `jrdb_lh`。本目录没有它的实现、
测试、fixtures、数据或打包文件，也不修改它。下表只说明用户代码所需的接口边界，
不对外部实现做未经读取的断言：

| 外部接口族 | 本扩展的用途 |
|---|---|
| `common`、`config` | 公共异常、digest/序列化、时钟、运行限制、Config/Tariff |
| `runner` | 原 Run/checkpoint/执行状态接口 |
| `data`、`economic_data` | 原生数据源接口、来源绑定与只读历史支持 |
| `economic_service` | 原生工具服务接口 |
| `ledger`、`metering` | 原账本、计量、数据/调用/计算 tariff 及核销接口 |
| `economic_scoring`、`accuracy` | 原评分与预测质量核算接口 |
| `audit` | 保存记录的完整性检查与离线重放接口 |
| `provider`、`agents` | 原模型调用和 agent 接口 |
| archive 中的 `schema` 等 | 固定归档通过模块加载器使用的运行时接口 |

静态引用的精确模块名及调用方保存在依赖映射的 `external_framework_modules`；
归档的动态加载另见下节。外部 `jrdb_lh` 本身的依赖应由提供方维护，本整理不复制
它的源码、不自动拉取其他仓库，也不改变其配置或评分语义。

### 在独立环境中提供依赖

1. 使用源码目录外的 Python 独立环境；本扩展要求 Python >=3.11，完整离线验收使用 Python 3.13.2。
2. 由框架提供方按原方式提供兼容的 `jrdb_lh`。可以使用环境中已安装的版本，
   或向 `PYTHONPATH` 添加已有源码中 `jrdb_lh/` 的父目录。
3. 在本整理目录安装用户扩展；不要把外部 framework 复制到 `src/` 或 `history/`。

```powershell
# X:\ABSOLUTE\EXTERNAL_FRAMEWORK_PARENT 是占位绝对路径，需替换。
# 该父目录包含原有的 jrdb_lh/，不应填写 jrdb_lh 目录本身。
$env:PYTHONPATH = 'X:\ABSOLUTE\EXTERNAL_FRAMEWORK_PARENT'

# 在已准备好的独立环境、此整理目录中执行。
python -m pip install -e . --no-deps

# 仅查找顶层包位置，不 import jrdb_lh，也不 import active_research 科学模块。
python -B -c "import importlib.util; spec=importlib.util.find_spec('jrdb_lh'); print('NOT FOUND' if spec is None else spec.origin)"
```

如果环境中已正确安装兼容 framework，不需要设置 `PYTHONPATH`。上面的赋值示例
仅用于一个新准备的会话；已有其他外部路径时应按环境管理方式保留它们。
文档不猜测 Modeling 的公开 PyPI 包名、版本号或安装地址。

`pip install -e . --no-deps` 只安装此扩展，不安装它的运行依赖。
PEP 517 默认构建隔离仍可能安装构建依赖；若构建依赖已在本地准备齐全，可使用：

```text
python -m pip install -e . --no-deps --no-build-isolation
```

上述命令不是科学运行验收。`find_spec('jrdb_lh')` 找到模块仅证明搜索路径可见，
不证明版本、接口、数据、tariff 或原始配置匹配。不要用 dotted 子模块探测替代本
检查，因为查找子模块可能先导入父包。此定位命令不执行科学import或provider请求。

## 固定 warmup archive

`policy_warmup/archive.py` 从合法提供的 `profile['archive_source']` 定位固定归档。
这个字段必须指向归档的 **`jrdb_lh` 包根目录**，直接包含 `__init__.py`，
不是给 `PYTHONPATH` 使用的父目录。加载器
核对全体 Python 源文件的聚合 SHA-256，然后加载到隔离命名空间
`_policy_warmup_v060`。现有固定值保持原样：

```text
archive_code_hash:
d545ca9a936418de50bf6ad2d8ecf67c11226bc8744cfdffb9e82016b355c392

CLOCK:   1704456000
ORIGIN:  2024-01-01
HORIZON: 2024-01-06
```

归档、真实 profile 和数据均未纳入本目录。不得替换成任意本地 Modeling checkout，
也不得移除 hash 或历史时间边界来使检查通过。Adaptive 使用该归档进行历史 warmup；
Frozen 不重新 warmup，但现有 `verify_frozen` 仍调用 archive `load(profile)` 校验，
因此仍依赖原归档输入。独立 Python 环境中的 `jrdb_lh` 与这个固定归档是两个
分别需要满足的依赖，不应混为一份可以随意替换的源码。

## 部署、freeze 与当前可运行范围

科学文件中的 `PROJECT`、历史路径、模型身份 guard、host 标记要求、sandbox、
CPU/context 限制、旧凭据加载器定位和科学参数均未修改。旧定位不包含凭据，
也不表示其他机器能够直接使用原登录环境；发布不提供任何凭据。采用 `src/` 布局后，部分基于 `__file__`
的路径推导会指向 `src/`；外部框架可见和扩展安装成功不会自动解决这些部署约束。

原 freeze 会把整个科学源码树纳入 protected hashes。因此，即使某文件不被正式入口
import，也不能从已有冻结实验的目录中移走。本次只整理新的审阅目录，没有改动旧
部署、重用旧 manifest 启动、创建 `.experiment-host`，也没有生成新的 scientific
freeze。未来若获准部署，需针对最终目录单独进行兼容验收与 fresh freeze。

本次来源为本地 DeepSeek/Frozen 科学副本，未包含 GLM、MiMo、Astra 在 Mini 上
各自全部最终 adapter、生命周期预算和 freeze 变体。本目录不能据此声称跨模型正式
复现通过；独立适配和运维组件在历史清单中列出，不静默合入公共 transport。

## Python 工具与测试资源

- 实现主要使用 Python 标准库。
- `setuptools>=68` 是本扩展的构建依赖，`wheel` 等构建支持需由环境按实际构建要求提供。
- `pytest` 用于原科学测试；`matplotlib` 用于 Frozen 图表。原框架所需数值库及版本
  由框架提供方维护，不应把本包的空直接依赖列表理解为无需它们。这些可选依赖的声明见
  `pyproject.toml`，并不代替外部 framework 与科学运行条件。
- 原正式原生执行路径依赖 macOS sandbox 和原 Mini host 条件；本整理不移除 guard 来适配 Windows。
- 用户测试对外层 `conftest`、`test_active_research`、固定 warmup profile、archive
  和原fixtures的源码引用保持原样。完整回归在Mini只读引用已有外部框架、测试和
  固定归档；Modeling源码、测试或归档没有复制到整理包。

本轮已完成包级检查、完整离线回归及19个合成产物对照。完整回归精简前为
1284 PASS/0 FAIL/1 SKIP，精简后为1195 PASS/0 FAIL/1 SKIP；共同1196个nodeid
的结果及执行次数相同。正式16个测试文件在两边均为239 PASS，无失败或跳过。
唯一共同skip是原有显式启用的Mini重资源验证测试，本轮没有强制开启。

这些结果依赖独立审计目录、只读外部资源、严格的网络/凭据/写入guard，以及限定旧
测试的fixture路径适配。适配使用合成SQLite、真实Python runtime副本和shadow配置
路径，保留原验证函数及科学参数；未修改Modeling、科学源或原实验产物。它们不等同
于当前src布局的正式部署、模型适配或launch验收。未来部署仍须完成兼容检查与
fresh scientific freeze。详见 [测试说明](TESTING.md)、[脱敏验收记录](../metadata/release-validation.json)
及[离线验收摘要](VALIDATION.md)。
