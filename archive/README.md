# 历史归档：检查后由用户手动决定删除

当前V3源码、输入、测试及重建工具不依赖这里的任何文件。本次整理只移动文件，没有执行删除。

| 目录 | 内容 | 判断 |
|---|---|---|
| development_logs/ | 成功、失败、重试等开发控制台日志 | 已提取小型验收证据，通常无需保留全部日志 |
| legacy/ | v0/v1脚本、旧uncertainty/规则策略、CloudSim Java和补丁 | 不再作为当前入口；历史Git标签可恢复原布局 |
| history/results/ | v0–v2生成结果及图片 | V3所需v2成本锚点已单独保存在baseline/v2_reference.csv |
| history/docs/ | 旧报告、配图与旧规程 | 历史解释材料，不参与V3运行 |
| history/baseline/ | v1基线与参数 | 当前默认回归使用baseline/v3_reference.csv |

历史代码按原样保存，不保证脱离原目录布局后直接执行。需要完整历史项目时，从Git标签`v3-before-layout`另行检出。当前参考验收内核在sim/reference/，不要连同本目录一起误判为过时代码。
