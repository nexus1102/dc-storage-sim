"""Discounted incremental project cash flow; wear proxy is not charged twice."""
import numpy as np
import pandas as pd
from dataclasses import replace
from config import Config
from dispatch import solve

def crf(rate,years):
    if years<=0 or rate<0: raise ValueError('invalid discount horizon')
    return 1/years if rate==0 else rate*(1+rate)**years/((1+rate)**years-1)

def lifetime(load,price,modules,base_cost,cfg=Config(),coupled=True,**options):
    capex=modules*.5*1000*cfg.capex_kwh
    soh=1.; rows=[]; cumulative=-capex; payback=None
    fixed_dispatch=None
    for year in range(1,cfg.years+1):
        replacement=0.
        if coupled and soh<cfg.replacement_soh:
            replacement=capex*cfg.replacement_fraction; soh=1.
        if not coupled and fixed_dispatch is not None: r=fixed_dispatch
        else:
            r=solve(load,price,modules,cfg,capacity_soh=soh if coupled else 1.,**options)
            if not coupled: fixed_dispatch=r
        # Physical replacement cash flow replaces the accounting wear cost.
        opex=365*(r['cost']-r['deg_cost'])
        benefit=365*base_cost-opex-replacement
        discounted=benefit/(1+cfg.discount)**year
        old=cumulative; cumulative+=discounted
        if payback is None and cumulative>=0 and discounted>0:
            payback=year-1+max(0.,-old)/discounted
        fade=cfg.calendar_fade+cfg.cycle_fade*365*r['throughput']/((2 if cfg.accounting_version==4 else 1)*modules*.5)
        rows.append(dict(year=year,soh=soh,opex=opex,replacement=replacement,
                         benefit=benefit,discounted=discounted,cumulative_npv=cumulative,
                         discharge_mwh=365*cfg.dt*r['trace'].discharge.sum()))
        if coupled: soh=max(.01,soh-fade)
    df=pd.DataFrame(rows)
    discounts=(1+cfg.discount)**df.year
    lcoe=(capex+float(((df.opex+df.replacement)/discounts).sum()))/max(1e-9,float((df.discharge_mwh/discounts).sum()))
    return dict(npv=float(cumulative),discounted_payback=payback,lcoe_site_cost_per_discharge=lcoe,
                capex=capex,cashflows=df,replacements=int((df.replacement>0).sum()))
