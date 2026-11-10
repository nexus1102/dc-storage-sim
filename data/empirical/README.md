# 外部实测数据的精简衍生表

此目录与`data/inputs/`分开管理：前者是外部观测的离线校准材料，后者是平台已冻结的运行输入。本轮没有将这些观测自动接入调度、修改默认参数或重写基线。

`20261111/`包含：

| 文件 | 内容与来源 | 变换及边界 |
|---|---|---|
| hust_two_cells.csv | HUST公开LFP/石墨数据集v2的1-1、1-2两颗电芯，4182个循环 | mA、秒转换为A、Wh；同一充/放电状态内梯形积分；状态间隙不补造波形；保留间隙能量界、容量一致性和名义DC Wh EFC |
| azure_code_2024_minute.csv | Azure 2024代码推理完整7天，16803695条请求 | 逐分钟请求数、输入/输出token总量、完整窗口标记；零请求分钟保留；不把重复时间戳当重复请求删除 |
| azure_code_2023_minute.csv | Azure 2023代码样本8819条，约57分钟 | 与2024分别统计，不拼接成连续日期 |
| azure_conv_2023_minute.csv | Azure 2023会话样本19366条，约58分钟 | 原始时区未给定，不假定UTC，不复制成全天 |
| mlperf_llm_power.csv | MLPerf Inference v5.1固定提交的8个LLM功耗结果 | 有效功耗结果的单位核查与精简；tokens/s不是requests/s，W/(tokens/s)不是请求边际能耗 |

原始下载放在Git忽略的`external/empirical_20261111/`。源码URL、固定提交/发布资产、大小及SHA256见[校准证据](../../docs/research/calibration/external_calibration_evidence.json)。Azure 2024完整资产SHA与发布方digest一致；HUST只下载两个ZIP成员并验证CRC32及解压后SHA，**未验证整包SHA**。不提交原始pickle、691 MB请求文件、规范PDF或仿真运行目录。

HUST的`relative_capacity_retention`按每颗电芯最初10个循环容量中位数归一；跨电芯留出需要该电芯的初始容量测量。拟合仅用1-1第11至902循环，尾部602循环及1-2第11至2678循环作留出。间隙界由记录的最大电流/电压估计，并不证明间隙内实际功率；DC电芯结果不能直接替换AC侧UPS效率或寿命参数。

Azure按完整分钟先后70%/30%拆分；首末不完整分钟保留在表中、排除于拟合/留出评价。原始精确token分位数从逐请求文件计算，不能由分钟token总量反推。UTC只用于2024原始文件明确带时区的记录。

来源与许可：

- Azure Public Dataset，Microsoft/Azure，CC BY 4.0；[固定版本](https://github.com/Azure/AzurePublicDataset/tree/215becdacba1ce682c7368642ce97d5a332de7a6)，本目录为分钟聚合衍生数据。2023引用Splitwise（ISCA 2024），2024引用DynamoLLM（HPCA 2025），详见发布方说明。
- Ye Yuan、Guijun Ma、Songpei Xu，HUST电池数据v2，DOI [10.17632/nsc7hnsg4s.2](https://doi.org/10.17632/nsc7hnsg4s.2)，CC BY 4.0；本目录为两颗电芯的积分和容量衍生数据，不代表77颗总体。相关论文Ma等，Energy & Environmental Science 2022，15:4083–4094。
- MLCommons及提交者Lenovo等，[Inference v5.1固定提交](https://github.com/mlcommons/inference_results_v5.1/tree/5ea4f62ef62536e6bf4d78a9b440fb9035ddfb4a)，Apache-2.0；本目录为汇总结果节选和单位换算，并非新的MLPerf认证结果。

对应许可文本保存在`licenses/CC-BY-4.0.txt`与`licenses/MLPerf-Apache-2.0.txt`。项目本身仍为私有研究代码，第三方数据许可独立适用；这些文本不是项目的开源许可证。
