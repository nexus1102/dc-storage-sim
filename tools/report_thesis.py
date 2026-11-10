"""Generate a concise, reproducible delivery review from validated run artifacts."""
import argparse
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import pandas as pd
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'sim'))
from config import ROOT
from runctx import sha256
from verify import verify_run,regress
from independent_verify import verify_directory


def report(run,tests,legacy_root,replay_root,current_legacy,matched_root):
    run=Path(run);tests=Path(tests);legacy_root=Path(legacy_root)
    if verify_run(run):raise AssertionError('unverified research run')
    acceptance=json.loads((run/'acceptance.json').read_text(encoding='utf-8'))
    if not acceptance['full_day'] or (acceptance['references'],acceptance['capability_cases'],acceptance['factorial_cells'])!=(20,80,16):raise AssertionError('incomplete frozen experiment matrix')
    checks=verify_directory(run)
    if not checks['pass'].all():raise AssertionError('serialized research verification failed')
    suites=list(ET.parse(tests).getroot().iter('testsuite'))
    totals={key:sum(int(s.get(key,0)) for s in suites) for key in ('tests','errors','failures','skipped')}
    if totals['tests']==0 or any(totals[k] for k in ('errors','failures','skipped')):raise AssertionError('full clean tests not passed')
    snapshot={str(p.relative_to(run/'source')):sha256(p) for p in (run/'source').rglob('*.py')}
    if any(sha256(ROOT/'sim'/name)!=digest for name,digest in snapshot.items()):raise AssertionError('model changed after experiment snapshot')
    legacy=[]
    for stage in range(4):
        published=json.loads((legacy_root/f'results/v3_stage{stage}/PUBLISHED.json').read_text(encoding='utf-8'))
        path=legacy_root/'runs'/published['latest_run']
        if verify_run(path):raise AssertionError('legacy software stage failed')
        legacy.append(dict(stage=stage,run=str(path),seal_sha256=sha256(path/'SEAL.json')))
    if regress(ROOT/'baseline/v3_reference.csv',legacy_root/'results/v3_stage0/results_v3.csv'):raise AssertionError('historical baseline drift')
    if regress(ROOT/'baseline/v3_reference.csv',Path(current_legacy)):raise AssertionError('current-source historical baseline drift')
    replay_root=Path(replay_root);replay_proof=replay_root/'replay_comparison.json'
    replay_records=json.loads(replay_proof.read_text(encoding='utf-8'))
    if not replay_records or not all(row['match'] for row in replay_records):raise AssertionError('frozen source replay drift')
    replay_run=next((replay_root/'runs').iterdir())
    if verify_run(replay_run):raise AssertionError('replay seal failed')
    matched_root=Path(matched_root)
    if verify_run(matched_root):raise AssertionError('matched seal failed')
    matched_checks=verify_directory(matched_root)
    if not matched_checks['pass'].all():raise AssertionError('matched independent checks failed')
    matched_cap=pd.read_csv(matched_root/'matched_capability.csv')
    if len(matched_cap)!=16:raise AssertionError('incomplete matched matrix')
    base=pd.read_csv(matched_root/'trace_N_reference.csv')
    base_jobs=pd.read_csv(matched_root/'service_N_reference.csv').sort_values(['job','t'])
    for arch in ('N','N+1','2N+1','2(N+1)'):
        frame=pd.read_csv(matched_root/f'trace_{arch}_reference.csv')
        jobs=pd.read_csv(matched_root/f'service_{arch}_reference.csv').sort_values(['job','t'])
        if not np.allclose(frame[['grid','it','ac','cold']],base[['grid','it','ac','cold']],atol=1e-6,rtol=0):raise AssertionError('cross-architecture reference mismatch')
        if not np.allclose(jobs.drop(columns=['job','kind']),base_jobs.drop(columns=['job','kind']),atol=1e-6,rtol=0):raise AssertionError('cross-architecture job mismatch')
    cap=pd.read_csv(run/'capability.csv');factor=pd.read_csv(run/'factorial.csv');design=pd.read_csv(run/'design_faults.csv')
    outages=json.loads((run/'outages.json').read_text(encoding='utf-8'))
    standards=json.loads((run/'standards_report.json').read_text(encoding='utf-8'))
    evidence=dict(software_status='PASS',standards_status=standards['standards_status'],certification=False,
        empirical_sla=False,run=str(run),seal_sha256=sha256(run/'SEAL.json'),tests=totals,test_report_sha256=sha256(tests),
        independent_checks=len(checks),acceptance=acceptance,model_source_sha256=snapshot,legacy_stages=legacy,
        current_source_legacy_sha256=sha256(Path(current_legacy)),
        frozen_smoke_replay=dict(csv_hashes_matched=len(replay_records),proof_sha256=sha256(replay_proof),seal_sha256=sha256(replay_run/'SEAL.json'),full_day=False),
        matched_reference_audit=dict(run=str(matched_root),capacity_points=len(matched_cap),independent_checks=len(matched_checks),seal_sha256=sha256(matched_root/'SEAL.json'),equal_reference_it_cooling_and_job_history=True),
        interpretation='representative-day mechanism study with synthetic contracts; no population, current-market or engineering certification claim',
        evidence_gaps=['standard clause reviews and external engineering tests','site topology and device calibration','real GPU/LLM service calibration','JCR/CAS partition evidence'])
    folder=ROOT/'docs/research/delivery';folder.mkdir(parents=True,exist_ok=True)
    (folder/'acceptance_evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    body=['# 博士课题研究平台交付验收','',
        '日期：2026-11-11。软件及本轮约定的代表日实验：PASS；标准总符合性：'+standards['standards_status']+'；现场与Tier认证：未取得。',
        '',f'运行：`{run}`。模型源码冻结与当前sim一致；SEAL SHA256：`{evidence["seal_sha256"]}`。',
        '',f'干净Git工作树测试：{totals["tests"]}项，失败/错误/跳过均为0；序列化轨迹独立审核{len(checks)}项通过。旧V3四阶段以2场景验证软件链路，阶段0与原基线一致；两份冻结输入、参考内核及旧基线未覆盖。',
        '',f'最终源码的历史V3五架构另行重算，与原基线一致。小规模课题实验从冻结源码和输入复算，{len(replay_records)}份CSV哈希逐字节一致；该复算是2小时软件检查，不冒称已完成全天逐字节复算。',
        '', '## 已完成的范围','',
        '- 退化完整吞吐计费与等效深度分离；默认accounting_version=4，历史V3入口显式使用3。',
        '- 给定逐时AGC激活的SOC、电表、购电、退化及逐电池闭合；负电价下计划充放电互斥。采用时段内计划循环/调频互斥的保守全站策略，不将平均能量叠加冒称单设备可执行瞬时轨迹。',
        '- 逐作业合同及架构路径约束，固定事件前历史的能力验证；固定服务失电回放、维护退出及修复后补能。',
        '- 保留冷却需求的顺序/联合×不调频/调频四组实验；11项规范的证据门槛与差距记录。',
        '', '## 冻结实验与解释','',
        '四架构×五业务组合=20个参考情景；每组两模式×两持续时间=80个能力点；混合业务四架构×四经济组=16格。代表日为24个1小时区间，能力事件为1/2小时；失电回放另用15/45分钟。它是合成合同下的机制研究，不是分钟/秒级设备验证、真实AI服务实证或统计总体推断。单一业务组按相同输入电能代理重新分配，不代表相同token或训练step。',
        '', '本轮参考合同中训练/批处理/可交付各为一个全日窗口任务，训练另含最短执行段和恢复耗电，批处理为显式高罚价软截止，可交付为硬截止；推理为稳态M/M/1代理。各组均保留共同的推理预留策略，零请求时仍有配置的待机和服务裕度；单一业务指输入工作量的分配，不代表拆除其他设施。相同服务合同可能给出相同能力，不能仅凭业务名称强行制造差异。实际业务到达、截止、检查点、长尾和单位算力耗电需外部校准，核心接口已支持逐作业输入。',
        '', '### 各架构自行优化参考下的混合业务能力','',
        '| 架构 | 持续小时 | 电池MW | 联合MW | 协同增量MW |','|---|---:|---:|---:|---:|']
    for (arch,h),d in cap[cap.workload=='mixed'].groupby(['arch','duration_h']):
        x=d.set_index('mode').power_mw;b=float(x['battery_only']);j=float(x['joint'])
        body.append(f'| {arch} | {h:g} | {b:.5f} | {j:.5f} | {j-b:.5f} |')
    body+=['','本表各架构的参考调度不同，不能直接据此给硬件架构排名。电池独立能力与联合能力分别解释，不能把全部联合电量称为电池闲置容量。完整单一业务结果见运行的capability.csv。','',
        '### 共同参考下的架构对照','',
        f'追加16个匹配能力点，独立审核{len(matched_checks)}项通过。四组固定相同IT、冷却、热状态、完整作业进度、事件时刻和合同，各单元按同一SOC上限初始化；只改变架构和对应固定资产。装机容量也随架构变化，因此不是等容量下纯连接结构效应；等容量/不同连接及SOC位置的反例由单独测试覆盖。',
        '', '| 架构 | 持续小时 | 电池MW | 联合MW | 协同增量MW |','|---|---:|---:|---:|---:|']
    for (arch,h),d in matched_cap.groupby(['arch','duration_h']):
        x=d.set_index('mode').power_mw;b=float(x['battery_only']);j=float(x['joint'])
        body.append(f'| {arch} | {h:g} | {b:.5f} | {j:.5f} | {j-b:.5f} |')
    body+=['',f'本匹配情景事件窗口的最小参考进口为{base.grid.iloc[8:10].min():.5f} MW，联合能力已碰到参考进口上界，不能据此推断架构没有影响；电池独立能力和增量成本仍有差异。数值依赖本轮全日窗口合成合同。','',
        '### 混合业务的成本对照（元/代表日）','',
        '| 架构 | 顺序无调频 | 联合无调频 | 顺序调频 | 联合调频 |','|---|---:|---:|---:|---:|']
    for arch,d in factor.groupby('arch'):
        c=d.set_index('arm').cost;body.append(f'| {arch} | {c.sequential_no_reg:.2f} | {c.joint_no_reg:.2f} | {c.sequential_reg:.2f} | {c.joint_reg:.2f} |')
    body+=['','成本已分解购电、需量、退化、服务缺额和容量/里程收入，详见factorial.csv。当前价格为历史研究情景；不外推真实收益或市场资格。','',
        '## 故障、规范与剩余外部证据','',
        f'独立失电回放{len(outages)}例，其中{sum(not x["service_preserved"] for x in outages)}例出现失供；满设计负荷检查{len(design)}例，其中{int((~design.meets_design).sum())}例不满足。这些结果保留，不强迫N或整路径退出情形通过。恢复策略在全部故障修复后开始，未恢复情形同样报告。',
        '', '供电成功时沿用原IT/冷却服务；失供时不声称业务或温度仍获保障。调度中的峰值备用仍是保守内近似，回放是给定策略的可行性核查。',
        '', '[11项规范矩阵](../../standards/验收规范与证据矩阵.md)及运行中的standards_report.json逐项给出阻断原因。模型反例与软件测试只提供支撑，不代替准确条款、设备试验和工程审阅。官方检索补查遇到Transport closed，未将失败检索当作条款证据。',
        '', '仍需外部取得：适用标准全文与逐条审阅、站点单线图、SOH/效率/放电曲线、真实GPU/LLM服务参数、并网/保护/接地等工程试验及市场准入证据；JCR与中科院分区未核验项继续保留。',
        '', '## 收尾与复核','',
        '本轮停止新增功能，转入论文结果分析与外部数据校准。重新运行：`python sim/thesis_experiments.py --slots 24`；导出审核：`python sim/independent_verify.py <run>`；封存审核：`python sim/verify.py run <run>`。`python sim/acceptance.py --standards`当前应返回2，表示标准证据尚不齐备，而非软件失败。',
        '', '冻结源码复算：`python sim/reproduce.py <run> <不存在的新目录>`。原PDF、虚拟环境及生成运行目录不纳入Git；本报告与精简证据随源码保留。']
    (folder/'验收报告.md').write_text('\n'.join(body)+'\n',encoding='utf-8')
    print(json.dumps(dict(report=str(folder/'验收报告.md'),**totals,checks=len(checks),matched_checks=len(matched_checks),standards_status=standards['standards_status']),ensure_ascii=False))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('run',type=Path);ap.add_argument('--tests',type=Path,required=True);ap.add_argument('--legacy-root',type=Path,required=True);ap.add_argument('--replay-root',type=Path,required=True);ap.add_argument('--current-legacy',type=Path,required=True);ap.add_argument('--matched-root',type=Path,required=True);a=ap.parse_args();report(a.run,a.tests,a.legacy_root,a.replay_root,a.current_legacy,a.matched_root)
