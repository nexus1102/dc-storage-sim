# 当前验收基准

- v3_reference.csv：V3主结果的数值回归基准，verify.py regress默认读取。
- v2_reference.csv：当前五架构历史成本锚点，sizing.legacy_anchors及测试读取，必须保留。

首次GitHub上传不包含旧运行的v3_regression_evidence.json或旧runs原件。新电脑完成全部阶段后，可用sim/acceptance.py --regress-three --report重新生成三次回归证据；该命令会重新冻结V3基线，只有确认模型口径后才可执行。

修订物理公式或成本口径后先检查与说明漂移，不得直接覆盖基线掩盖变化。历史基准是当前验收输入，不是待清理的旧结果目录。
