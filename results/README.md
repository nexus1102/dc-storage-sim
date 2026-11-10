# 可再生结果，Git不管理数值产物

| 子目录 | 内容 | 重建入口 |
|---|---|---|
| v3_stage0/ | 五架构、参数及穿越敏感面 | run_all.ps1 -Stage 0 |
| v3_stage1/ | 因子实验、业务分解和耦合消融 | run_all.ps1 -Stage 1 |
| v3_stage2/ | 容量配置、前沿和寿命现金流 | run_all.ps1 -Stage 2 |
| v3_stage3/ | 四范式同样本外评价 | run_all.ps1 -Stage 3 |
| foundations/ | 架构故障、服务合同、纯电池/算电协同功率—持续时间域 | sim/foundation_experiments.py --slots 24 |
| summary/ | 汇总表、测试XML、新交付清单 | acceptance.py --report；pytest；seal_delivery.py |
| mpc_v3/ | MPC独立实验 | mpc.py；audit_optional.py |
| v3_migration/ | 跨节点实验 | migration.py |
| input_build/ | 原始数据重建的说明图 | tools/build_business_load.py |
| reference_v2/ | 显式运行历史参考内核的结果 | sim/reference/pipeline_v2.py |

可以在检查后手动清理数值输出；新计算不会将旧输出当作输入。清理后，报告生成、发布副本比较等命令需要先重跑相应阶段。PUBLISHED.json指向runs/封存原件；删除发布副本与删除原件的后果不同。
