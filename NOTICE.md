# 第三方组件与数据声明

## 代码依赖（不随包分发）
- PyPSA — MIT
- PyBaMM — BSD-3-Clause
- HiGHS — MIT
- CloudSim Plus — **GPLv3**（仅原电脑历史示例；本次GitHub快照不包含，不参与V3 Python流水线）

## 随包分发的数据（裁剪样本）
- Alibaba clusterdata — 见 github.com/alibaba/clusterdata 的许可条款
- Azure Public Dataset — 原始请求文件不随包分发；新增分钟聚合表及归属见下。

本项目用于科研复现。第三方数据的具体使用、改编与再分发权利分别依其许可，不以项目用途说明覆盖原始授权。

## 2026-11-11外部实测衍生数据

`data/empirical/20261111/`为离线校准使用的衍生表，不自动替换平台冻结输入：

- HUST LFP/石墨v2，Ye Yuan、Guijun Ma、Songpei Xu，DOI 10.17632/nsc7hnsg4s.2，CC BY 4.0。两颗电芯的积分/容量表经本项目变换，不代表作者或数据发布方认可本项目结论。
- Microsoft/Azure Public Dataset，CC BY 4.0；2023和2024推理请求按分钟聚合。保留来源版本、发布资产及SHA；不转存原始请求大文件。
- MLCommons Inference v5.1及各提交者，Apache-2.0；有效LLM功耗结果节选与单位换算。结果协议、软硬件及原始提交仍由发布方定义。

作者、固定链接、变换、适用边界见`data/empirical/README.md`，许可全文在该目录`licenses/`。未复制第三方模型代码，未提交原始pickle或标准PDF。上述第三方许可不构成本项目代码的开源授权。

## 私有研究仓库

本项目未授予开源使用许可。第三方代码、依赖与数据仍分别遵守其原始许可；私有仓库不改变这些义务。原论文PDF与机器凭据不随仓库上传。
