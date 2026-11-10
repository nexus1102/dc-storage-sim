# 文献优先级与取证边界

口径：**JCR Q1优先，同时记录中科院分区**。核对日期2026-11-10。引用量采用OpenAlex的cited_by_count，是数据库当日计数，不是WoS引用量，不等同于ESI高被引论文认定。

目前确认了以下期刊论文的DOI、刊名、作者及可检索引用量。**本次没有取得官方JCR年度/类别/四分位和中科院年度分区表，全部分区字段保持unverified；不能把下表称为“已核验Q1文献集”。** 网络检索服务返回transport closed，部分出版社/分区页面返回403，具体取证状态见search_audit.json。

| 作用 | 文献 | OpenAlex引用数 | 模型采用与边界 |
|---|---|---|---|
| 服务性能与经济联合约束 | Ghamkhari & Mohsenian-Rad, *Energy and Performance Management of Green Data Centers: A Profit Maximization Approach*, IEEE TSG, 2013, [DOI](https://doi.org/10.1109/TSG.2013.2237929) | 264 | 支持显式SLA而非只优化电费；本文M/M/1尾概率公式自行推导，不声称照搬该文 |
| 功率、能量与持续时间分开 | Hao et al., *Aggregate Flexibility of Thermostatically Controlled Loads*, IEEE TPWRS, [DOI](https://doi.org/10.1109/TPWRS.2014.2328865) | 744 | 仅作灵活性边界方法背景；不是UPS架构文献；在线年与卷期年分别记录 |
| 可行域几何与能力投影 | Zhao et al., *A Geometric Approach to Aggregate Flexibility Modeling of Thermostatically Controlled Loads*, IEEE TPWRS, [DOI](https://doi.org/10.1109/TPWRS.2017.2674699), [作者预印本](https://arxiv.org/abs/1608.04422) | 372 | 采用“可行功率轨迹集合”的思想；本文含开关整数变量，因此投影一般非凸，不能直接声称是多面体/Minkowski和 |
| 调度必须保留服务/资源约束 | *Proactive Demand Response for Data Centers: A Win-Win Solution*, IEEE TSG, [DOI](https://doi.org/10.1109/TSG.2015.2501808), [作者全文](https://arxiv.org/html/1511.00575) | 139 | 正文列有工作负荷、QoS延迟、服务器和能耗约束；该文“两阶段”为双层定价，不等同本站两阶段随机规划 |
| 可调度负荷与外部基准 | *Data center demand response: Avoiding the coincident peak via workload shifting and local generation*, Performance Evaluation, [DOI](https://doi.org/10.1016/j.peva.2013.08.014) | 225 | 相关背景候选，全文模型未取得；不从标题推断本文参数 |
| 数据中心动态灵活性 | *Data centers as a source of dynamic flexibility in smart girds*, Applied Energy, [DOI](https://doi.org/10.1016/j.apenergy.2018.07.056) | 67 | 强相关补充；保留出版元数据标题中的girds拼写；不称为同组最高引用 |
| UPS节点与柔性服务 | *UPS Node-Based Workload Management for Data Centers Considering Flexible Service Requirements*, IEEE TIA, [DOI](https://doi.org/10.1109/TIA.2019.2933791) | 37 | 主题相关性高，引用量较低；仅作候选，不冒充高引用核心文献 |

原始元数据、查询URL、作者、分区状态及年份见literature_metadata.json和literature_matrix.csv。摘要不作为附件批量再分发；正文与元数据的证据等级分开记录。没有把检索失败的工程白皮书或被重定向到无关站点的页面当成依据。

用户补充的肇嘉毅等（2026）实体—虚拟储能耦合论文已完成全文及关键公式版面核读，见[专项评审](../paper_reviews/zhao_2026_physical_virtual_storage.md)。它作为强相关补充文献单列，分区和引用量仍未核验；评审同时记录对当前调频SOC损失、退化吞吐单位与公平耦合对照的待修订事项，不代表这些修订已经实现。

## 尚需机构数据库核验

对每篇候选补齐：JCR版本年份、学科类别、该类别四分位、证据页/导出文件；中科院版本年份、大类/小类分区及证据。若同刊多个类别，分别列出，不只挑最有利类别。期刊当前分区与论文发表当年分区应区分。补齐前，分区筛选门槛保持未通过。

## 对原综述的修正

- 不沿用“2N+1在两条腿上各加备用”的混淆表述；本项目明确约定2N+1为一个共享可切换备用，2(N+1)为每路各一个备用。该约定须以目标站点单线图最终确认。
- 不将任意N-1模块判定等同Uptime Tier等级认证；路径故障、模块故障和维护退出是不同状态。
- 不将ACM/IEEE会议文章称作JCR Q1期刊论文；会议成果只能作为额外证据。
- 不声称“尚无其他工作”或“必然可发表”；需要系统检索及原文对照才可判断创新性。
