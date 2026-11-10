"""
可靠性蒙特卡洛：把失负荷概率从"备用约束的副产品"变成独立评价指标。

事件模型（每次模拟一次电网失电）
    t0      故障发生时刻，在日内均匀分布
    τ       柴发启动到带载的时间，对数正态（中位数 20 s，形状 0.6）
    fail    启动失败（概率 1e-3），此时按 15 min 穿越计
    ε       负荷预测误差，N(0, σ)，截断在 ±3σ
    τ 与 ε 与 t0 独立

失供能量
    need = critical_load * duration
    delivered = min(SOC * eta, UPS_power * duration)
    short = max(0, need - delivered)
功率和电量限制作用于同一交付量，不能相加重复计算缺额。

输出
    LOLP   失负荷概率 = P(short > 0)
    EENS   给定失电事件的条件期望失供能量（MWh/事件），非MWh/日
    CVaR95 最坏 5% 情景的平均失供能量
    risk_by_hour  各时刻发生故障时的失负荷概率
"""
from __future__ import annotations

import numpy as np


def monte_carlo(
    *,
    soc: np.ndarray,            # MWh，逐时点
    load: np.ndarray,           # MW
    p_ups: float,               # MW，故障期间可交付功率
    eta_dispatch: float = 0.95,
    t_gen_med_s: float = 20.0,
    t_gen_sigma: float = 0.6,
    p_gen_fail: float = 1e-3,
    t_gen_fail_s: float = 900.0,
    load_sigma: float = 0.05,
    n_scen: int = 20000,
    seed: int = 20261110,
) -> dict:
    rng = np.random.default_rng(seed)
    n_t = len(load)

    t0 = rng.integers(0, n_t, n_scen)
    tau_s = rng.lognormal(np.log(t_gen_med_s), t_gen_sigma, n_scen)
    fail = rng.random(n_scen) < p_gen_fail
    tau_s[fail] = t_gen_fail_s
    eps = rng.normal(0.0, load_sigma, n_scen)
    eps = np.clip(eps, -3 * load_sigma, 3 * load_sigma)

    p_it = load[t0] * (1.0 + eps)
    need = p_it * tau_s / 3600.0                       # MWh
    avail = soc[t0] * eta_dispatch                     # MWh
    # Power and energy limitations act on the SAME delivered energy. Adding
    # their shortages double-counts when both bind.
    delivered = np.minimum(avail, p_ups * tau_s / 3600.0)
    short = np.maximum(0.0, need - delivered)

    fail_mask = short > 1e-9
    lolp = float(fail_mask.mean())
    eens = float(short.mean())
    cvar95 = float(np.sort(short)[-max(1, int(0.05 * n_scen)):].mean())

    # EENS 的 95% bootstrap 区间（情景独立同分布，可直接重抽）
    brng = np.random.default_rng(seed + 1)
    idx = brng.integers(0, n_scen, size=(1000, n_scen))
    boots = short[idx].mean(axis=1)
    eens_lo, eens_hi = float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))

    # 按时段的风险曲线
    risk_by_hour = np.zeros(n_t)
    for t in range(n_t):
        m = t0 == t
        if m.any():
            risk_by_hour[t] = fail_mask[m].mean()

    return {
        "metric_basis": "conditional_per_grid_outage_event",
        "EENS_MWh_per_event": eens,
        "zero_loss_probability_upper95": float(1-.05**(1/n_scen)) if not fail_mask.any() else None,
        "LOLP": lolp,
        "EENS_MWh": eens,
        "CVaR95_MWh": cvar95,
        "EENS_ci_lo": eens_lo,
        "EENS_ci_hi": eens_hi,
        "worst_event_MWh": float(short.max()),
        "risk_by_hour": risk_by_hour,
        "n_scen": n_scen,
    }


def summarize(rows: list[dict]) -> str:
    lines = [f"{'架构':<8}{'LOLP':>10}{'EENS MWh':>12}{'CVaR95 MWh':>13}{'最坏事件':>12}"]
    for r in rows:
        lines.append(f"{r['arch']:<8}{r['LOLP']:>9.3%}{r['EENS_MWh']:>12.4f}"
                     f"{r['CVaR95_MWh']:>13.4f}{r['worst_event_MWh']:>12.3f}")
    return "\n".join(lines)
