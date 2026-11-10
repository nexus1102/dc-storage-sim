# 数据边界

| 位置 | 用途 | V3完整运行是否必需 |
|---|---|---|
| inputs/load_business_24h.csv | 已冻结96点四类业务负荷 | 是；不要当生成垃圾删除 |
| inputs/degradation_calibration.csv | 已冻结三档电池退化标定 | 是；主流程不要求重新安装PyBaMM |
| raw/openb_pod_list_default.csv | GPU pod原始裁剪数据 | 是；阶段1统计及输入重建 |
| raw/openb_pod_list_cpu050.csv | CPU pod原始裁剪数据 | 是；阶段1统计及输入重建 |
| raw/AzureLLMInferenceTrace_*.csv | 历史v1使用的LLM数据 | 当前V3不读取；首次GitHub快照不包含，原电脑保留 |

输入文件受Git管理，与results/可再生输出分开。重建负荷使用tools/build_business_load.py；重新标定使用tools/battery_degradation.py并额外安装requirements-battery.txt。重建输入会影响科学结果，应同步检查溯源及基线。
