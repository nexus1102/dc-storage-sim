"""Explicit modeled energy boundary; never an ASHRAE 90.4 compliance shortcut."""
import numpy as np

def summarize(trace,cfg):
    dt=cfg.dt;eta=cfg.eta
    ec=trace['agc_charge_mwh'].sum() if 'agc_charge_mwh' in trace else 0.
    ed=trace['agc_discharge_mwh'].sum() if 'agc_discharge_mwh' in trace else 0.
    ch=dt*trace.charge.sum()+ec;dis=dt*trace.discharge.sum()+ed
    loss=(1-eta)*ch+(1/eta-1)*dis
    it=dt*trace.it.sum();site=dt*trace.site.sum()
    if not np.isfinite([it,site,loss]).all() or it<=0 or site<0 or loss<-1e-8:raise ValueError('invalid energy boundary')
    return dict(it_mwh=float(it),cooling_mwh=float(dt*trace.ac.sum()),battery_loss_mwh=float(loss),
                modeled_facility_mwh=float(site+loss),modeled_facility_to_it_ratio=float((site+loss)/it),
                complete_facility_pue=False,ashrae_compliance=False,
                excluded='unmodeled distribution/UPS auxiliary losses, lighting and other facilities; no annual MLC/ELC claim')
