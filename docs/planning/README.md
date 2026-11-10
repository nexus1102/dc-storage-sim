# 开发要求原文

开发路线图原文保留，不改写其中的历史文件路径。目录整理后按以下位置理解：

| 原文位置 | 当前位置 |
|---|---|
| results/degradation_calibration.csv | data/inputs/degradation_calibration.csv |
| results/load_business_24h.csv | data/inputs/load_business_24h.csv |
| results/results_v2.csv | baseline/v2_reference.csv |
| sim/pipeline_v2.py | sim/reference/pipeline_v2.py（历史参考）；当前核心为sim/dispatch.py |
| sim/build_business_load.py | tools/build_business_load.py |
| sim/battery_degradation.py | tools/battery_degradation.py |

实际迁移记录见../maintenance/reorganization_moves.csv。
