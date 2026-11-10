# 验收参考内核，必须保留

这是当前V3的历史成本锚点依赖，不是待删除的开发旧版本。

- pipeline_v2.py：固定V2口径，供sizing.legacy_anchors比较五架构成本。
- business_v2.py：保留V2队列实现，包括当时漏乘步长的口径，专用于重现历史数值。
- topology.py、checks.py：参考模型拓扑及检查。

当前V3优化由上一级dispatch.py完成，业务参数由上一级business.py提供。参考模型输出写入results/reference_v2/，不会覆盖V3结果。独立运行：`python sim/reference/pipeline_v2.py`。
