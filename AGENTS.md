# 接力开发约定

- 禁止批量删除文件或目录。不得使用`del /s`、`rd /s`、`rmdir /s`、`Remove-Item -Recurse`、`rm -rf`。确需删除时，每次只能处理一个明确路径的文件；批量清理由用户手动执行。
- 仓库是私有博士课题研究项目。不要公开仓库、添加开源许可证、上传凭据、原文PDF、虚拟环境或生成运行目录。
- 先读`README.md`、`docs/planning/下一步开发计划.md`和`docs/collaboration/跨电脑接力指南.md`。
- 当前共享调度核心为`sim/dispatch.py`。`sim/reference/`与`baseline/v2_reference.csv`是当前验收依赖，不能因名称带v2就移除。
- 必需冻结输入位于`data/inputs/`；两份openb原始裁剪输入位于`data/raw/`。输出写入`runs/`和`results/`，不作为新仿真的输入。
- 修改公式或计费口径前，补充独立推导与反例；不得为通过回归直接覆盖旧基线。模型修订需明确区分软件验收和科学实证。
- 每个开发工作包使用独立Git分支和可审查的提交；提交前运行相应测试。Git身份仅在仓库本地配置为`yuandingshan <yuandingshan1102@stu.xjtu.edu.cn>`。
- 文献优先口径为JCR Q1，同时记录中科院分区；没有年度、类别及证据时保持未核验。不要把合成服务合同当作真实GPU/LLM服务保证。
