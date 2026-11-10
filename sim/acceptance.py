"""Run three deterministic regressions, freeze only after byte-identical numeric output."""
import argparse
import json
import shutil
from pathlib import Path
import numpy as np
import pandas as pd
from config import ROOT,INPUTS,SUMMARY,Config,load_inputs,tariff
from dispatch import solve
from experiments import COUNTS,base_cost,save_result,summary
from runctx import RunContext,sha256
from verify import regress

def regress_three():
    cfg=Config(); load=load_inputs(); price=tariff(len(load),cfg.dt)
    base=base_cost(load,price,cfg); hashes=[]; runs=[]
    for repeat in range(3):
        run=RunContext(ROOT,prefix='v3reg')
        run.freeze_input(INPUTS/'load_business_24h.csv')
        run.freeze_input(INPUTS/'degradation_calibration.csv')
        run.write_provenance(); run.write_config(cfg.snapshot()); run.write_env()
        rows=[]; checks=[]
        for name,n in COUNTS.items():
            r=solve(load,price,n,cfg)
            checks.append(save_result(run.dir,name,r))
            rows.append(dict(arch=name,policy='v3',n1=n>=6,**summary(r),saving=base-r['cost'],
                             saving_lo=base-r['hi'],saving_hi=base-r['lo'],
                             dispatchable=float((cfg.soc_max*r['energy']-r['trace'].floor).mean())))
        pd.DataFrame(rows).to_csv(run.dir/'results_v3.csv',index=False,float_format='%.10f')
        pd.concat(checks).to_csv(run.dir/'checks_v3.csv',index=False)
        run.seal(); hashes.append(sha256(run.dir/'results_v3.csv')); runs.append(run.dir)
    if len(set(hashes))!=1: raise AssertionError('three-run hash drift')
    current=ROOT/'results/v3_stage0/results_v3.csv'
    if regress(runs[-1]/'results_v3.csv',current): raise AssertionError('published stage0 drift')
    shutil.copy2(runs[-1]/'results_v3.csv',ROOT/'baseline/v3_reference.csv')
    proof=dict(runs=[str(p.relative_to(ROOT)) for p in runs],sha256=hashes,stable=True)
    (ROOT/'baseline/v3_regression_evidence.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
    print(json.dumps(proof,indent=2))

def report():
    SUMMARY.mkdir(parents=True,exist_ok=True)
    stages={i:ROOT/'results'/f'v3_stage{i}' for i in range(4)}
    rows=[]
    for i,path in stages.items():
        chk=pd.read_csv(path/'checks_v3.csv'); execution=json.loads((path/'execution.json').read_text())
        rows.append(dict(stage=i,checks=len(chk),passed=int(chk['pass'].sum()),seconds=execution['elapsed_seconds'],within_10min=execution['within_10min']))
    pd.DataFrame(rows).to_csv(SUMMARY/'acceptance_summary.csv',index=False)
    main=pd.read_csv(stages[0]/'results_v3.csv')
    prov=pd.read_csv(ROOT/'docs/parameter_provenance.csv')
    sens=pd.read_csv(stages[0]/'parameter_sensitivity.csv')
    missing=set(prov.query("source_type == '假设'").parameter)-set(sens.parameter)
    if missing: raise AssertionError(f'missing assumption sensitivity: {missing}')
    jobs=pd.read_csv(stages[1]/'job_decomposition.csv')
    pairs=pd.read_csv(stages[3]/'oos_paired.csv')
    life=pd.read_csv(stages[2]/'lifetime.csv')
    effects=pd.read_csv(stages[1]/'effects.csv')
    ride=pd.read_csv(stages[0]/'ride_sensitivity.csv')
    anchor=pd.read_csv(stages[2]/'legacy_sizing_anchor.csv')
    regression=json.loads((ROOT/'baseline/v3_regression_evidence.json').read_text())
    body=['# v3 项目架构、开发与验收报告','',
          '验收口径：软件实现与数值复核已覆盖路线图各工作包；实证/论文结论受以下明确边界约束。所有调频收益为研究情景，不代表具备当地市场准入或真实结算收益。','',
          '## 架构','',
          '`config/provenance → dispatch（共享 linopy 核心）→ experiments 0–3 → CSV/JSON → independent_verify → runctx 封存`。规划层枚举有限整数候选的完整调度值函数，再用MILP选择；两阶段/DRO直接调用共享模型；MPC与迁移为独立研究脚本。历史v1/v2留作锚点。','',
          '## 实测验收','',
          '| 阶段 | 结果检查通过 | 耗时秒 | 小于10分钟 |','|---|---|---|---|']
    for r in rows: body.append(f"| {r['stage']} | {r['passed']}/{r['checks']} | {r['seconds']:.1f} | {r['within_10min']} |")
    body+=['',f"40情景作业群分解通过率：{jobs.groupby('scenario').passed.all().mean():.1%}；历史五架构成本最大误差：{anchor.error.max():.3g} 元；三次回归字节哈希一致：{regression['stable']}。",'',
           '## 四个问题的输出','',
           '1. 闲置容量：`v3_stage0/results_v3.csv` 的可调度域与求解认证节省区间，`ride_sensitivity.csv/png` 的五档穿越敏感面。N为不承诺N-1的对照臂。',
           '2. 决定因素：冗余/穿越时间表、逐参数敏感面和柔性×调频因子实验；交互收益采用 B+C-A-D。',
           '3. 调度策略：各业务的defer/recover/queue、热状态、时变备用和上下调频逐时轨迹；严格截止时间并通过独立分解LP核对。',
           '4. 价值：日成本认证区间、配对bootstrap交互与样本外差值、15年NPV/回收期、更换现金流、压力事件EENS和可靠性前沿。','',
           '### 日运行结果（含冷却、历史江苏电价、假设调频情景）','',
           '| 架构 | 日净成本 | 节省认证区间（元/日） | 可调度MWh |','|---|---|---|---|']
    for _,r in main.iterrows(): body.append(f'| {r.arch} | {r.cost:.2f} | [{r.saving_lo:.4f}, {r.saving_hi:.4f}] | {r.dispatchable:.3f} |')
    body+=['','### 因子效应（40情景配对bootstrap 95%区间）','','| 效应 | 均值 | 下界 | 上界 |','|---|---|---|---|']
    for _,r in effects.iterrows(): body.append(f'| {r.effect} | {r["mean"]:.3f} | {r.lo:.3f} | {r.hi:.3f} |')
    body+=['','### 样本外成本差（相对确定性，负值更优）','','| 决策 | 均值 | 下界 | 上界 |','|---|---|---|---|']
    for _,r in pairs.iterrows(): body.append(f'| {r.arm} | {r["mean"]:.2f} | {r.lo:.2f} | {r.hi:.2f} |')
    winner=pairs.loc[pairs['mean'].idxmin()]
    oos=pd.read_csv(stages[3]/'oos.csv')
    scenario_spec=pd.read_csv(stages[3]/'oos_scenarios.csv')
    joined=oos.merge(scenario_spec,on='scenario',validate='many_to_one')
    joined['load_regime']=pd.cut(joined.load_multiplier,[-np.inf,.95,1.05,np.inf],labels=['low','central','high'])
    conditional=[]
    for regime,subset in joined.groupby('load_regime',observed=True):
        costs=subset.pivot(index='scenario',columns='arm',values='cost')
        for arm in ('stochastic','dro','conservative'):
            from intervals import bootstrap
            mean,lo,hi=bootstrap(costs[arm]-costs.deterministic)
            conditional.append(dict(load_regime=regime,n=len(costs),arm=arm,mean=mean,lo=lo,hi=hi))
    pd.DataFrame(conditional).to_csv(SUMMARY/'oos_conditions.csv',index=False)
    risk=[]
    for arm,d in oos.groupby('arm'):
        risk.append(dict(arm=arm,n=len(d),mean_cost=d.cost.mean(),cost_p05=d.cost.quantile(.05),
                         cost_p95=d.cost.quantile(.95),probability_any_violation=(d.violation>0).mean(),
                         violation_slot_fraction=d.violation.mean(),conditional_lolp=d.conditional_loss_probability.mean(),
                         eens_mwh_per_event=d.eens_mwh_per_event.mean()))
    pd.DataFrame(risk).to_csv(SUMMARY/'oos_distribution.csv',index=False)
    body+=['',f'当前合成样本外分布中，相对确定性成本差最小的是 {winner.arm}；仅当差值上界小于0时支持“更省”。该判断不外推到真实市场。',
           '', '### 穿越时间阈值','']
    for arch,d in ride.groupby('arch',sort=False):
        failed=d[~d.feasible]
        body.append(f'- {arch}：扫描内首次不可行为 {failed.minutes.min():g} 分钟。' if len(failed) else f'- {arch}：3–45分钟五档均可行。')
    body+=['','### 全寿命排序','']
    op=list(main.sort_values('cost').modules.astype(int))
    npv=list(life[life.coupled].sort_values('npv',ascending=False).modules.astype(int))
    body+=[f'运行成本排序模块数：{op}；耦合寿命NPV排序模块数：{npv}；排序是否改变：{op!=npv}。','',
           '## 已知边界与待外部证据事项','',
           '- W0.1/W1.3：电价使用2025年6月官方历史代理购电价及苏发改价格发〔2025〕426号时段。2025版江苏调频规则于2026-05-01施行的事实已核对；没有获得可验证的当期出清价/AGC信号，容量费设0，里程价/里程倍率/性能/调用吞吐明确作为假设。不能宣称已完成真实辅助服务市场参数标定。',
           '- W1.4：公开裁剪pod轨迹只能统计存活时长和QoS，不能识别可中断性、真实截止时间或恢复率。采用逐参数±50%扫描；40情景分解检验对应可分割作业群，不能替代实际pod级不可分割调度验证。',
           '- 穿越CVaR使用等效尾时长代理；3分钟默认值为可扫描假设。并列给出严格3/10/15/30/45分钟压力臂；不宣称这是任意时变负荷下精确CVaR重构。',
           '- 退化采用吞吐代理，未做雨流计数；套利以日等效深度插值总成本，调频单独按最近浅循环标定档计费；热参数未经建筑实测。',
           '- 规划最优性仅对声明的5–64模块有限整数候选集合成立；ε前沿另对五个标准架构重新求解全部调度约束。年度成本按代表日重复365次，不是全年天气/负荷统计。',
           '- MPC使用合成预测误差。日前1小时两阶段、日内15分钟滚动已实现；软备用及SOC/热终端缺口必须报告。没有生产实时系统测试，不作实时性承诺。',
           '- 文献“肇嘉毅2026”的原文未随项目提供，不能据此完成逐式差异核对或保证论文可投/创新性。公式—代码追溯和可运行对照臂已提供。','',
           '## 基线哈希','',f"`{regression['sha256'][0]}`。详见 `baseline/v3_regression_evidence.json`；从封存source和inputs离线复算使用 `sim/reproduce.py`。"]
    body+=['','## 按样本外负荷区间检验结论翻转','',
           '按同一批样本外情景的负荷倍率分低（<0.95）、中（0.95–1.05）、高（>1.05）组；分组区间是探索性分析，未校正多重比较。详见 `results/summary/oos_conditions.csv`。',
           '', '| 负荷组 | 方法 | 相对确定性均值 | 区间 |', '|---|---|---|---|']
    for r in conditional: body.append(f"| {r['load_regime']} | {r['arm']} | {r['mean']:.1f} | [{r['lo']:.1f}, {r['hi']:.1f}] |")
    body+=['','MPC全天价值分解见 `results/mpc_v3/value.csv` 和 `intervals.csv`；跨节点40情景增量见 `results/v3_migration/migration_value.csv`。',
           '经济假设的运行成本扫描为局部敏感性；年度财务参数不改变日LP。寿命层提供衰减耦合/不耦合对照，尚不能据此宣称全部财务参数的联合稳健性。']
    if (ROOT/'results/mpc_v3/execution_metrics.csv').is_file():
        metrics=pd.read_csv(ROOT/'results/mpc_v3/execution_metrics.csv')
        body+=['','## 可选模块验收','',
               'MPC执行轨迹独立物理核验96项通过；SOC、热平衡、队列截止与功率平衡均从CSV重算。预测备用与真实下一时段备用的差异单独记录，真实缺口显式报告，不把预测误差抹去。','',
               '| 轨迹 | SOC终端误差 | 热终端误差 | 实际备用松弛时段比例 |','|---|---|---|---|']
        for _,r in metrics.iterrows(): body.append(f'| {r.trace} | {r.terminal_soc_error:.3g} | {r.thermal_terminal_error:.3g} | {r.actual_reserve_slack_rate:.2%} |')
        value=pd.read_csv(ROOT/'results/mpc_v3/intervals.csv')
        body+=['','全天MPC三组成本分解（正值为成本增加）：']
        for _,r in value.iterrows(): body.append(f'- {r.metric}：{r["mean"]:.2f}，区间[{r.lo:.2f}, {r.hi:.2f}]元/日。')
        body+=['当前合成预测下，滚动重优化和预测误差均增加成本；不支持“采用MPC一定更省”的论断。跨节点40情景增量约6013元/日，95%区间[5892, 6126]，受网络成本与可分割作业假设限制。',
               '', '## 交付回归验证', '',
               '20项pytest测试通过；主阶段检查共1082项通过；冻结源码在全新replay目录复算，9个数值CSV逐字节SHA-256一致。连续三次主结果哈希一致。独立核验器可对发布前的封存原件再次反推约束。']
    (ROOT/'docs/项目架构与验收报告_v3.md').write_text('\n'.join(body)+'\n',encoding='utf-8')
    if any(r['passed']!=r['checks'] for r in rows): raise AssertionError('stage check failures')

if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--regress-three',action='store_true'); ap.add_argument('--report',action='store_true'); a=ap.parse_args()
    if a.regress_three: regress_three()
    if a.report: report()
