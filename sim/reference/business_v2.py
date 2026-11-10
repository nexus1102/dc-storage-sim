"""
四类业务的可延迟特性与负荷平移约束。

每类业务有三个参数：

    f           瞬时最多可推迟的负荷占该类负荷的比例
    window_h    最长推迟时间（决定可积压工作的上限）
    ramp_frac   恢复速率上限（占该类日均负荷的比例）

排队模型（每类业务一条队列）：

    q[t] = q[t-1] + defer[t] - recover[t]        q ≥ 0
    defer[t]   ≤ f · load_b[t]
    recover[t] ≤ ramp_frac · mean(load_b)
    q[t]       ≤ f · mean(load_b) · window_h
    q[end]     = 0                               当日工作必须做完

q 是"已积压但尚未补做"的电量（MWh）。推迟多少、补做多少、什么时候补，
都由优化器决定，但工作量在日内守恒，积压量不超过窗口允许的范围。

参数取值是工程假设，不是实测值——报告里作为假设列出，并做敏感性。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BusinessSpec:
    key: str
    name: str
    f: float           # 可延迟比例
    window_h: float    # 延迟窗口
    ramp_frac: float   # 回收速率上限 / 日均负荷
    rationale: str


BUSINESS: dict[str, BusinessSpec] = {
    "train": BusinessSpec(
        "train", "AI 训练", f=0.20, window_h=2.0, ramp_frac=0.30,
        rationale="长任务可 checkpoint 后让出资源，但重启有开销，窗口取 2 h"),
    "infer": BusinessSpec(
        "infer", "AI 推理", f=0.00, window_h=0.0, ramp_frac=0.0,
        rationale="时延敏感，不做负荷平移"),
    "batch": BusinessSpec(
        "batch", "批处理", f=0.80, window_h=8.0, ramp_frac=0.50,
        rationale="尽力而为作业，可在日内任意时段补做"),
    "deliv": BusinessSpec(
        "deliv", "可交付", f=0.35, window_h=4.0, ramp_frac=0.40,
        rationale="带交付期，可在窗口内调整但不能拖过截止时间"),
}


def declare(n, load_b: pd.DataFrame) -> dict[str, dict]:
    """向网络加入"推迟"与"补做"两个可变注入/吸收元件，返回其参数。

    必须在 n.optimize.create_model() 之前调用。
    """
    specs: dict[str, dict] = {}
    for key, spec in BUSINESS.items():
        if spec.f <= 0.0 or load_b[key].max() <= 0:
            continue
        prof = load_b[key].to_numpy()
        mean_mw = float(prof.mean())
        specs[key] = {
            "spec": spec,
            "def_max": spec.f * prof,                 # MW
            "rec_max": spec.ramp_frac * mean_mw,      # MW
            "q_max": spec.f * mean_mw * spec.window_h,  # MWh
        }
        # 推迟 = 向母线注入功率（等价于减少用电）
        n.add("Generator", f"def_{key}", bus="dc", carrier="ac",
              p_nom=1.0, p_max_pu=specs[key]["def_max"], p_min_pu=0.0,
              marginal_cost=0.0)
        # 补做 = 从母线取电（p 为负）
        n.add("Generator", f"rec_{key}", bus="dc", carrier="ac",
              p_nom=1.0, p_max_pu=np.zeros_like(prof),
              p_min_pu=-specs[key]["rec_max"], marginal_cost=0.0)
    return specs


def constrain(m, specs: dict[str, dict], snaps: pd.DatetimeIndex) -> dict[str, dict]:
    """加入队列约束。必须在 create_model() 之后调用。"""
    snap_ix = pd.Index(snaps, name="snapshot")
    out: dict[str, dict] = {}
    for key, sp in specs.items():
        p_def = m["Generator-p"].sel(name=f"def_{key}")
        p_rec = -m["Generator-p"].sel(name=f"rec_{key}")
        q = m.add_variables(lower=0, coords=[snap_ix], name=f"queue_{key}")

        m.add_constraints(q <= sp["q_max"], name=f"queue_cap_{key}")
        m.add_constraints(q[0] - (p_def[0] - p_rec[0]) == 0, name=f"queue_init_{key}")
        m.add_constraints(q[1:] - q[:-1] - (p_def[1:] - p_rec[1:]) == 0,
                          name=f"queue_bal_{key}")
        m.add_constraints(q[-1] == 0, name=f"queue_end_{key}")

        out[key] = {"defer": p_def, "recover": p_rec, "queue": q,
                    "q_max": sp["q_max"], "rec_max": sp["rec_max"]}
    return out


def summarize(n, flex: dict, dt_h: float) -> pd.DataFrame:
    """从解中提取各类业务实际平移的电量与积压峰值。"""
    rows = []
    for key, v in flex.items():
        defer = n.generators_t.p[f"def_{key}"].to_numpy()
        rec = -n.generators_t.p[f"rec_{key}"].to_numpy()
        rows.append({
            "business": key,
            "name": BUSINESS[key].name,
            "deferred_MWh": float(defer.sum() * dt_h),
            "recovered_MWh": float(rec.sum() * dt_h),
            "peak_defer_MW": float(defer.max()),
            "queue_max_MWh": float(v["queue"].solution.to_numpy().max()),
            "q_cap_MWh": v["q_max"],
        })
    return pd.DataFrame(rows)


def check_conservation(n, flex: dict, dt_h: float) -> float:
    """日内工作量守恒：推迟总量 = 补做总量。返回最大残差。"""
    resid = 0.0
    for key in flex:
        defer = n.generators_t.p[f"def_{key}"].to_numpy()
        rec = -n.generators_t.p[f"rec_{key}"].to_numpy()
        resid = max(resid, abs(float((defer.sum() - rec.sum()) * dt_h)))
    return resid
