"""
模型自洽性核验：把物理上必然成立的恒等式变成可执行的断言。

设计原则
  1. 每条检查只读求解结果，不依赖求解器内部状态，因此换求解器仍可用
  2. 返回 (名称, 残差, 容差, 通过)，不抛异常，由调用方统一汇总与裁决
  3. 容差按量纲分别设定（功率 1e-6 MW、能量 1e-6 MWh、金额 1e-2 元），
     不共用一个数——金额和电量混用同一个 epsilon 是假严谨
  4. 检查失败不回滚结果，只标记。物理量对不上时，人要能看到是哪里对不上

对应课题中"备用—调频共用能量裕度"的建模假设，reg_* 检查是这套假设
能否成立的可执行证据，而不是文档里的一句说明。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# ---------------- 容差 ----------------
TOL_POWER = 1e-6      # MW
TOL_ENERGY = 1e-6     # MWh


@dataclass
class Check:
    name: str
    detail: str
    residual: float
    tol: float

    @property
    def passed(self) -> bool:
        return self.residual <= self.tol


@dataclass
class CheckReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, detail: str, residual: float, tol: float) -> None:
        self.checks.append(Check(name, detail, float(residual), float(tol)))

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def to_rows(self) -> list[dict]:
        return [
            {"check": c.name, "detail": c.detail, "residual": c.residual,
             "tol": c.tol, "pass": c.passed}
            for c in self.checks
        ]

    def summary(self) -> str:
        n_pass = sum(c.passed for c in self.checks)
        head = f"[checks] {n_pass}/{len(self.checks)} 通过"
        if self.passed:
            worst = max(self.checks, key=lambda c: c.residual / c.tol)
            return f"{head}（最紧：{worst.name} {worst.residual:.2e} / {worst.tol:.0e}）"
        lines = [head, "  失败项："]
        for c in self.failures():
            lines.append(f"    - {c.name}: 残差 {c.residual:.6g} > 容差 {c.tol:.0e}  [{c.detail}]")
        return "\n".join(lines)


def verify_solution(
    *,
    soc: np.ndarray,            # MWh，逐时点
    p_dispatch: np.ndarray,     # MW，正=放电上网
    p_store: np.ndarray,        # MW，正=充电
    p_grid: np.ndarray,         # MW，正=从电网取电
    load: np.ndarray,           # MW
    dt_h: float,
    eta_store: float,
    eta_dispatch: float,
    e_rated: float,
    soc_max_frac: float,
    floor: np.ndarray,          # MWh，逐时点备用下限
    p_rated: float,
    throughput: float,          # 代码里参与退化成本计算的那个量
    reg_up: np.ndarray | None = None,
    reg_dn: np.ndarray | None = None,
    t_reg_h: float | None = None,
    bus_extra: np.ndarray | None = None,
) -> CheckReport:
    """对一次 LP 解执行全部恒等式与约束检查。"""
    rep = CheckReport()
    n = len(load)

    # ---- 1. SoC 递推闭合 ----
    # PyPSA 约定：soc[t] 是第 t 时段末的状态，故 soc[t] − soc[t−1] = Δ(t)
    if n > 1:
        inc = (p_store * eta_store - p_dispatch / eta_dispatch) * dt_h
        resid = float(np.abs(soc[1:] - soc[:-1] - inc[1:]).max())
    else:
        resid = 0.0
    rep.add("soc_recursion", "soc[t+1] = soc[t] + (ηc·p_chg − p_dis/ηd)·Δt", resid, TOL_ENERGY)

    # ---- 2. 循环边界 ----
    # cyclic_state_of_charge=True：日内净增量应为 0
    resid = abs(float((p_store * eta_store - p_dispatch / eta_dispatch).sum() * dt_h))
    rep.add("cyclic_soc", "日内净充放能量为 0（cyclic_state_of_charge）", resid, TOL_ENERGY)

    # ---- 3. 吞吐口径一致 ----
    # 退化成本用的 throughput 必须等于解上的 0.5·Σ|p|·Δt
    expected = float(np.abs(p_dispatch - p_store).sum() * dt_h * 0.5)
    resid = abs(throughput - expected)
    rep.add("throughput", "throughput = 0.5·Σ|p_dis − p_chg|·Δt", resid, TOL_ENERGY)

    # ---- 4. 母线功率平衡 ----
    # bus_extra 用于负荷平移：推迟向母线注入、补做从母线取电
    extra = np.zeros(n) if bus_extra is None else np.asarray(bus_extra, dtype=float)
    resid = float(np.abs(p_grid + p_dispatch - p_store + extra - load).max())
    rep.add("bus_balance", "p_grid + p_dis − p_chg + 平移 = load", resid, TOL_POWER)

    # ---- 5. SoC 物理边界 ----
    resid = max(float((-soc).max()), float((soc - soc_max_frac * e_rated).max()))
    rep.add("soc_bounds", f"0 ≤ soc ≤ {soc_max_frac:.2f}·E_rated", resid, TOL_ENERGY)

    # ---- 6. 备用下限 ----
    resid = float((floor - soc).max())
    rep.add("reserve_floor", "soc ≥ 备用下限（逐时点）", resid, TOL_ENERGY)

    # ---- 7. 功率额定 ----
    resid = max(float((p_dispatch - p_rated).max()), float((p_store - p_rated).max()))
    rep.add("power_rating", "p_dis, p_chg ≤ p_rated", resid, TOL_POWER)

    # ---- 8-11. 调频：功率裕度与能量裕度 ----
    if reg_up is not None and reg_dn is not None and t_reg_h is not None:
        resid = float((p_dispatch + reg_up - p_rated).max())
        rep.add("reg_power_up", "p_dis + r_up ≤ p_rated", resid, TOL_POWER)

        resid = float((p_store + reg_dn - p_rated).max())
        rep.add("reg_power_dn", "p_chg + r_dn ≤ p_rated", resid, TOL_POWER)

        resid = float((reg_up * t_reg_h + floor - soc).max())
        rep.add("reg_energy_up", "soc − r_up·T_reg ≥ 备用下限", resid, TOL_ENERGY)

        resid = float((soc + reg_dn * t_reg_h - soc_max_frac * e_rated).max())
        rep.add("reg_energy_dn", "soc + r_dn·T_reg ≤ SOC_max·E_rated", resid, TOL_ENERGY)

        resid = max(float((-reg_up).max()), float((-reg_dn).max()))
        rep.add("reg_nonneg", "r_up, r_dn ≥ 0", resid, TOL_POWER)

    return rep


def verify_accounting(
    *,
    objective: float,
    energy_cost: float,
    demand_cost: float,
    deg_cost: float,
    reg_rev: float,
) -> CheckReport:
    """核算口径核验：报告的目标值必须等于逐项重算的目标值。

    这条防的是"事后加项"——比如把退化成本排除在目标函数外、只在报表里减掉。
    一旦两者不等，就说明优化器看到的成本和报告里写的成本不是同一个东西。
    """
    rep = CheckReport()
    recomputed = energy_cost + demand_cost + deg_cost - reg_rev
    rep.add("objective_reconcile", "objective = 购电 + 需量 + 退化 − 调频收益",
            abs(objective - recomputed), max(1e-2, abs(objective) * 1e-9))
    return rep
