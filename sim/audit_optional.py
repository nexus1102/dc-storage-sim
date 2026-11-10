"""Validate and seal optional MPC and migration outputs after the standalone runs."""
import json
import numpy as np
import pandas as pd
from config import ROOT,Config,KEYS
from mpc import state
from independent_verify import verify_trace
from degradation import calibration,unit_cost
from runctx import RunContext

def main():
    cfg=Config(); out=ROOT/'results/mpc_v3'; checks=[]; metrics=[]
    for filename in ('rolling_perfect.csv','hierarchical.csv'):
        df=pd.read_csv(out/filename)
        init=state(6); init['reference_mw']={k:float(df['load_'+k].mean()) for k in KEYS}
        term=state(6)
        # Physical state and deadline checks use actual executed slots. Reserve
        # forecasts are audited separately, since tomorrow's load is not known.
        expected=[]
        for t in range(len(df)):
            expected.append(float(df.site.iloc[(t+1)%len(df)]*cfg.reserve_h/cfg.eta+.5))
        reserve_forecast_error=float(np.max(np.abs(df.floor-expected)))
        df['floor']=expected
        # Store actual shortages explicitly, never silently assert feasibility.
        df['reserve_slack']=np.maximum.reduce([np.zeros(len(df)),df.floor-df.reserve_commit,
                           df.reserve_commit+df.reg_up*cfg.reg_duration/cfg.eta-df.soc])
        throughput=cfg.dt*(.5*(df.charge+df.discharge).sum()+cfg.activation_beta*(df.reg_up+df.reg_dn).sum())
        deg=unit_cost(.3)*throughput
        thermal_error=abs(df.cold.iloc[-1]-.5)
        terminal_error=abs(df.soc.iloc[-1]-init['soc'])
        penalty=cfg.penalty*(cfg.dt*df.reserve_slack.sum()+terminal_error+thermal_error*cfg.thermal_c*cfg.thermal_range)
        cost=cfg.dt*np.dot(df.grid,df.price)+cfg.demand_rate*df.grid.max()+deg-cfg.dt*(cfg.reg_capacity_price+cfg.reg_mileage_price*cfg.mileage_ratio*cfg.performance)*(df.reg_up+df.reg_dn).sum()+penalty
        meta=dict(config=cfg.snapshot(),options=dict(initial=init,terminal=term,flex=True,thermal=True,policy='dynamic',
                  degradation='shallow',calibration=[a.tolist() for a in calibration()]),modules=6,energy=3.,power=10.,
                  terminal_slack=terminal_error,thermal_terminal_slack=thermal_error,
                  throughput=throughput,deg_cost=deg,penalty=penalty,cost=cost)
        chk=verify_trace(df,meta); chk['arch']=filename; chk['policy']='mpc_actual'
        checks.append(chk)
        df.to_csv(out/f'audited_{filename}',index=False)
        metrics.append(dict(trace=filename,physical_checks_pass=bool(chk['pass'].all()),
                            terminal_soc_error=terminal_error,thermal_terminal_error=thermal_error,
                            reserve_forecast_max_error_mwh=reserve_forecast_error,
                            actual_reserve_slack_rate=float((df.reserve_slack>1e-6).mean())))
    chk=pd.concat(checks,ignore_index=True)
    chk.to_csv(out/'checks_v3.csv',index=False)
    pd.DataFrame(metrics).to_csv(out/'execution_metrics.csv',index=False)
    if not chk['pass'].all():
        print(chk[~chk['pass']].to_string(index=False)); raise AssertionError('MPC physical audit failed')
    run=RunContext(ROOT,prefix='v3mpc')
    for path in sorted(out.iterdir()):
        if path.is_file(): run.freeze_input(path)
    # Validator expects checks at the top level as well as frozen input copies.
    chk.to_csv(run.dir/'checks_v3.csv',index=False)
    run.write_provenance(); run.write_config(cfg.snapshot()); run.write_env(); run.seal()
    print(f'MPC audit: {len(chk)} checks passed, sealed {run.dir.name}')

if __name__=='__main__': main()
