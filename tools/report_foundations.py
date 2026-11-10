"""Summarize and independently audit a sealed topology/service/capability study."""
import argparse
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'sim'))
from config import ROOT
from independent_verify import verify_directory
from verify import verify_run
from runctx import sha256
from physical_topology import architecture,Fault
from capability import deliverable_load
from dataclasses import replace

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--run');args=ap.parse_args()
    publication=ROOT/'results/foundations'
    run=Path(args.run).resolve() if args.run else ROOT/'runs'/json.loads((publication/'PUBLISHED.json').read_text(encoding='utf-8'))['latest_run']
    if verify_run(run):raise AssertionError('unverified foundation run')
    checks=verify_directory(run)
    if not checks['pass'].all():raise AssertionError(checks[~checks['pass']].to_string(index=False))
    cap=pd.read_csv(run/'capability.csv');cost=pd.read_csv(run/'operation.csv');fault=pd.read_csv(run/'fault_design_audit.csv')
    for name,d in cap.groupby('arch'):
        wide=d.pivot(index='duration_h',columns='mode',values='power_mw')
        if (wide.joint+1e-5<wide.battery_only).any():raise AssertionError('joint set smaller than battery-only subset')
        for mode,v in d.groupby('mode'):
            if (v.sort_values('duration_h').power_mw.diff().dropna()>1e-5).any():raise AssertionError('nested event duration nonmonotonic')
    output=ROOT/'results/foundation_review';output.mkdir(parents=True,exist_ok=True)
    fig,axes=plt.subplots(1,2,figsize=(11,4))
    for ax,mode in zip(axes,('battery_only','joint')):
        for name,d in cap[cap['mode']==mode].groupby('arch',sort=False):
            ax.plot(d.duration_h,d.power_mw,'o-',label=name)
        ax.set(xlabel='Sustained duration (h)',ylabel='Firm import reduction (MW)',title=mode)
        ax.legend();ax.grid(alpha=.25)
    fig.tight_layout();fig.savefig(output/'power_duration.png',dpi=150);plt.close(fig)
    suites=list(ET.parse(ROOT/'results/summary/foundation_final_tests.xml').getroot().iter('testsuite'))
    tests={k:sum(int(s.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
    a=architecture('2N');f=Fault('path',paths=('A',));pooled=replace(a,units=tuple(replace(u,paths=('A','B')) for u in a.units))
    examples=dict(equal_capacity_isolated=deliverable_load(a,[.475]*10,.5,f),
                  equal_capacity_pooled=deliverable_load(pooled,[.475]*10,.5,f),
                  same_soc_stranded=deliverable_load(a,[.475]*5+[.05]*5,.25,f),
                  same_soc_accessible=deliverable_load(a,[.05]*5+[.475]*5,.25,f))
    config=json.loads((run/'run_config.json').read_text(encoding='utf-8'))
    evidence=dict(run=str(run.relative_to(ROOT)),seal_sha256=sha256(run/'SEAL.json'),tests=tests,
                  independent_checks=len(checks),all_passed=bool(checks['pass'].all()),
                  reference=config.get('reference_mode','economic'),slots=config['slots'],dt=config['config']['dt'],
                  numerical_counterexamples=examples,capability_rows=len(cap),fault_states=len(fault),
                  jcr_gate='pending official year/category/quartile evidence',cas_gate='pending official partition evidence',
                  empirical_sla=False,field_topology_verified=False)
    (ROOT/'evidence/foundation_validation.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    body=['# 三项基础建模补强：实验与验收','',
          f"源运行：`{evidence['run']}`；参考模式：{evidence['reference']}；{config['slots']}时隙，步长{config['config']['dt']}h。",'',
          f"测试{tests['tests']}项，失败{tests['failures']}，错误{tests['errors']}；序列化CSV独立检查{len(checks)}项全部通过。",'',
          '## 已证明的软件性质','',
          '- 同等装机的不同接线图可产生不同故障后可供能力，排除仅比较模块数的混淆。',
          '- 单元SOC位置影响故障后可交付能力，汇总SOC不能替代可达性。',
          '- 共享备用同一状态至多接一路；功率和能量同时检查。',
          '- 有用工作、重启电量、硬/软截止和推理代理SLO独立核对。',
          '- 能力见证固定事件前状态与作业执行历史；不允许增加业务缺额换取容量，保留日末恢复。',
          '- 同一事件下joint能力不小于battery_only；持续时间变长时能力不增。','',
          '## 反例数值（MW）','']
    for key,value in examples.items():body.append(f'- {key}: {value:.4f}')
    body+=['','这些数值是给定接线和SOC的可计算反例，不是现场测量。',
           '', '## 功率—持续时间边界','', '| 架构 | 持续h | 纯电池MW | 协同MW | 协同增量MW |','|---|---|---|---|---|']
    for (name,duration),d in cap.groupby(['arch','duration_h'],sort=False):
        w=d.set_index('mode').power_mw;body.append(f'| {name} | {duration:g} | {w.battery_only:.4f} | {w.joint:.4f} | {w.joint-w.battery_only:.4f} |')
    body+=['','输出上下界为求解数值认证界，不是统计置信区间。事件交付MWh为电网进口减少量，包含业务调整时不得改称电池闲置电量。','',
           '## 满设计负荷故障审核','', '| 架构 | 最差单元故障可供MW | 最差路径故障可供MW | 设计MW |','|---|---|---|---|']
    for name,d in fault.groupby('arch',sort=False):
        unit=d[d.fault.str.startswith('lose_')&~d.fault.str.startswith('lose_path_')].critical_mw.min()
        path=d[d.fault.str.startswith('lose_path_')].critical_mw.min()
        body.append(f'| {name} | {unit:.4f} | {path:.4f} | {d.design_mw.iloc[0]:g} |')
    body+=['','0.5MWh/2MW名义参数在SOCmax=0.95、eta=0.95时，不能凭额定功率声称必然维持15分钟满载。架构名称也不等同Tier认证。','',
           '## 参数与文献证据边界','',
           '所有服务合同、到达率转换、单位请求电耗、重启能耗和最短训练段均为明示研究假设。工作量按负荷等效能量生成，不是实际pod截止时间。默认备电参考为配置SOC上限，不是测得SOC。',
           '研究工作采用JCR Q1优先、同时记录中科院分区的筛选口径；已核验7篇期刊论文元数据与OpenAlex引用数，其中5篇引用数不少于100。官方年度分区证据仍未取得，分区门槛未通过，不能声称已完成Q1文献筛选。',
           '三项模型已经形成可运行、可反驳的形式化实现；仍需站点单线图、实际UPS/电池参数、业务SLA与请求/作业日志标定后，才能形成实证论断。','',
           '完整定义见model_specification.md，文献取证见README.md、literature_matrix.csv和search_audit.json。']
    (ROOT/'docs/research/foundations/validation_report.md').write_text('\n'.join(body)+'\n',encoding='utf-8')
    print(json.dumps(evidence,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
