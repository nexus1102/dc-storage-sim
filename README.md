# 算电协同储能仿真平台

私有博士课题研究项目，GitHub仓库：**nexus1102/dc-storage-sim**。当前快照包含V3完整运行源码，以及显式电气架构、服务合同、闲置能力域的研究补强模块。尚待修订的调频能量闭合与退化计费问题见下一步计划。

先读：[下一步开发计划](docs/planning/下一步开发计划.md) · [跨电脑接力指南](docs/collaboration/跨电脑接力指南.md) · [研究模型说明](docs/research/foundations/model_specification.md) · [最新论文评审](docs/research/paper_reviews/zhao_2026_physical_virtual_storage.md)

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
| data/raw/openb*.csv | 阶段1统计与输入重建必需的两份原始裁剪 | 是 |
| baseline/ | V2锚点与V3数字回归基准 | 是；运行路径证据重新生成 |
| tests/、tools/、requirements*、run_all.* | 测试、重建维护工具、依赖和运行入口 | 是 |
| docs/ | 当前模型、参数/公式溯源、原要求、文献评审与下一步计划 | 是；生成报告和旧机盘点除外 |
| evidence/ | 本次精简上传验证摘要 | 是；不含旧运行证据 |
| results/、runs/ | 新生成输出、逐次冻结输入/源码及SEAL | 否，仅保留results/README.md |
| archive/、replay/、.venv/、缓存 | 本机历史、临时核验和环境 | 否，仅保留archive/README.md说明 |

首次GitHub提交以当前快照建立新历史，不携带原电脑旧开发提交、旧标签、历史Azure轨迹或旧仿真输出；原电脑内容没有被删除。参考内核属于当前运行与测试依赖，虽名称带v2仍须保留。生成结果不是后续仿真的输入。

## 研究入口与边界

- V3阶段0：五架构与备用穿越敏感性；阶段1：业务柔性×调频实验及消融；阶段2：容量、可靠性与寿命经济性；阶段3：共享事前投标的随机/DRO样本外比较。
- 显式研究模块：`physical_topology.py`、`service_contracts.py`、`capability.py`；四种架构为N、N+1、2N+1、2(N+1)。通过参数启用，默认V3聚合口径仍保留。
- 工作负载代理不等于真实训练/LLM服务认证，故障图不等于现场Tier认证；价格采用有来源的历史研究情景。模型、参数和单位改变后先检查漂移，再决定是否冻结新基线。
- 已识别的优先问题：退化标定/计费吞吐单位、调频效率损失的SOC与购电闭合、保留冷却服务的公平耦合对照。详见[下一步计划](docs/planning/下一步开发计划.md)。

原开发要求在[路线图](docs/planning/开发路线图_博士课题仿真平台.md)，公式与参数溯源分别在`docs/formula_traceability.csv`和`docs/parameter_provenance.csv`。JCR Q1优先、同时记录中科院分区；目前分区取证仍有未完成项，见[文献取证说明](docs/research/foundations/README.md)。

## Git与保密

主分支main；仓库仅供获授权账户接力，未添加开源许可证。Git提交身份为`yuandingshan <yuandingshan1102@stu.xjtu.edu.cn>`。不要推送原电脑旧分支/标签、凭据、论文原PDF或生成运行目录。开发约定见[AGENTS.md](AGENTS.md)，依赖与数据许可见[NOTICE.md](NOTICE.md)。
