# 算电协同储能仿真平台

私有博士课题研究项目，GitHub仓库：**nexus1102/dc-storage-sim**。当前共享核心已修正退化计费、调频能量与电表闭合，包含显式电气架构、服务合同、闲置能力及公平耦合实验。开发收尾范围见[有限范围交付约定](docs/planning/有限范围交付约定.md)；标准符合性与实站校准仍有外部证据缺口。

先读：[下一步开发计划](docs/planning/下一步开发计划.md) · [跨电脑接力指南](docs/collaboration/跨电脑接力指南.md) · [研究模型说明](docs/research/foundations/model_specification.md) · [最新论文评审](docs/research/paper_reviews/zhao_2026_physical_virtual_storage.md)

功能开发已冻结。2026-11-11后续工作仅为[外部实测校准](docs/research/calibration/实测校准报告.md)、[规范取证](docs/standards/20261111规范取证补充.md)及[论文分析](docs/research/analysis/论文分析与结论边界.md)。已取得LFP电芯、Azure完整七天推理及MLPerf功耗数据，衍生观测独立放在`data/empirical/`，没有自动替换默认输入；目标站点实证仍待取数。

开发和正式验收还须执行[验收规范与证据矩阵](docs/standards/验收规范与证据矩阵.md)，覆盖用户指定的11项规范。当前标准符合性为BLOCKED：规范条款、模型验证和外部工程证据尚未齐备，现有软件测试不构成符合全部规范或Tier认证的证明。

## 快速安装与运行

Windows，Python 3.12：

```powershell
git clone https://github.com/nexus1102/dc-storage-sim.git
Set-Location -LiteralPath .\dc-storage-sim
git config user.name "yuandingshan"
git config user.email "yuandingshan1102@stu.xjtu.edu.cn"
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pytest -q
.\run_all.ps1 -Stage all -Scenarios 2
.\.venv\Scripts\python.exe sim/verify.py regress
.\.venv\Scripts\python.exe sim/foundation_experiments.py --slots 8
```

2场景与8时隙用于快速检查，不形成统计或实站结论。完整V3使用`.\run_all.ps1 -Stage all -Scenarios 40 -Test`；研究模块使用`python sim/foundation_experiments.py --slots 24`。首次运行不依赖以前的生成结果，运行后会自动建立runs和results。更完整的验收、报告和接力命令见[接力指南](docs/collaboration/跨电脑接力指南.md)。

Linux/macOS：`python3.12 -m venv .venv`，安装相同依赖后执行`bash run_all.sh --stage all --scenarios 40`。本次移交实测平台为Windows，不声称已经跨系统验收。

## 目录与职责

| 位置 | 职责 | 是否随Git管理 |
|---|---|---|
| sim/ | V3共享调度、四阶段实验、随机/DRO、MPC、迁移及研究模型 | 是 |
| sim/reference/ | 当前五架构历史成本锚点的必需参考内核 | 是，不能删除 |
| data/inputs/ | 冻结业务负荷与退化标定 | 是，不能当作生成垃圾 |
| data/empirical/ | 外部实测精简衍生表、归属与许可，仅离线校准 | 是；与默认运行输入分开 |
| data/raw/openb*.csv | 阶段1统计与输入重建必需的两份原始裁剪 | 是 |
| baseline/ | V2锚点与V3数字回归基准 | 是；运行路径证据重新生成 |
| tests/、tools/、requirements*、run_all.* | 测试、重建维护工具、依赖和运行入口 | 是 |
| docs/ | 当前模型、参数/公式溯源、原要求、文献评审与下一步计划 | 是；生成报告和旧机盘点除外 |
| evidence/ | 本次精简上传验证摘要 | 是；不含旧运行证据 |
| results/、runs/ | 新生成输出、逐次冻结输入/源码及SEAL | 否，仅保留results/README.md |
| external/ | 公开取数原件、规范副本、临时核读；非平台必需文件 | 否；只提交来源、SHA及必要衍生表 |
| archive/、replay/、.venv/、缓存 | 本机历史、临时核验和环境 | 否，仅保留archive/README.md说明 |

首次GitHub提交以当前快照建立新历史，不携带原电脑旧开发提交、旧标签、历史Azure轨迹或旧仿真输出；原电脑内容没有被删除。参考内核属于当前运行与测试依赖，虽名称带v2仍须保留。生成结果不是后续仿真的输入。

## 研究入口与边界

当前博士课题入口：`python sim/thesis_experiments.py --slots 24`；20个代表日参考情景、80个能力点、16格经济对照及独立故障回放。`--slots 8`仅作小规模软件检查。结果放`results/thesis/`，封存原件放`runs/thesis_*`；`python sim/independent_verify.py <run>`复核导出轨迹与能力历史，`python sim/acceptance.py --standards`独立审查规范证据（缺证据返回2）。

架构排名需排除参考调度的影响：`python tools/matched_capability.py`另行验证共同IT/冷却及工作进度下的16个匹配能力点，结果在`results/matched/`。开发与验收汇总见[交付验收报告](docs/research/delivery/验收报告.md)；标准BLOCKED及实站校准缺口继续保留。

默认`Config()`采用修正的`accounting_version=4`，完整吞吐成本、AGC效率损失和电表计量一致；`run_all`/`experiments.py`及V3回归入口显式使用3，以保留历史可复核性。[核算推导](docs/research/accounting_derivation.md)与[失电策略](docs/research/outage_policy.md)说明单位、假设及适用边界。不得将历史V3结果冒称修正模型结果，亦不以旧基线约束新结论。

- V3阶段0：五架构与备用穿越敏感性；阶段1：业务柔性×调频实验及消融；阶段2：容量、可靠性与寿命经济性；阶段3：共享事前投标的随机/DRO样本外比较。
- 显式研究模块：`physical_topology.py`、`service_contracts.py`、`capability.py`；四种架构为N、N+1、2N+1、2(N+1)。通过参数启用，默认V3聚合口径仍保留。
- 工作负载代理不等于真实训练/LLM服务认证，故障图不等于现场Tier认证；价格采用有来源的历史研究情景。模型、参数和单位改变后先检查漂移，再决定是否冻结新基线。
- 本轮修正已覆盖退化吞吐口径、调频SOC/购电闭合及保留冷却服务的四组对照；停止增加新功能，后续重点是论文结果解释、实测校准和规范证据补齐。原P2扩展不列为本轮软件交付门槛。

原开发要求在[路线图](docs/planning/开发路线图_博士课题仿真平台.md)，公式与参数溯源分别在`docs/formula_traceability.csv`和`docs/parameter_provenance.csv`。JCR Q1优先、同时记录中科院分区；目前分区取证仍有未完成项，见[文献取证说明](docs/research/foundations/README.md)。

## Git与保密

主分支main；仓库仅供获授权账户接力，未添加开源许可证。Git提交身份为`yuandingshan <yuandingshan1102@stu.xjtu.edu.cn>`。不要推送原电脑旧分支/标签、凭据、论文原PDF或生成运行目录。开发约定见[AGENTS.md](AGENTS.md)，依赖与数据许可见[NOTICE.md](NOTICE.md)。
