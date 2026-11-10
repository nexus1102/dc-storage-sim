"""Observed trace durations/QoS, explicitly separated from unobservable flexibility."""
from pathlib import Path
import pandas as pd

def summarize(data_dir):
    rows=[]
    for name in ('openb_pod_list_default.csv','openb_pod_list_cpu050.csv'):
        df=pd.read_csv(Path(data_dir)/name)
        duration=(df.deletion_time-df.creation_time)/3600
        groups={'all':df.index}
        for qos in df.qos.dropna().unique(): groups['qos_'+qos]=df.index[df.qos==qos]
        for key,idx in groups.items():
            d=duration.loc[idx]
            rows.append(dict(file=name,group=key,n=len(d),duration_p10_h=d.quantile(.1),
                             duration_median_h=d.median(),duration_p90_h=d.quantile(.9),
                             flexibility_identifiable=False,
                             reason='No checkpoint, deadline or preemption labels in cropped pod trace'))
    return pd.DataFrame(rows)
