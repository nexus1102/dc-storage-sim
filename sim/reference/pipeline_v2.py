"""
v2 流水线：架构拓扑实体化 + 业务级可延迟 + CVaR 备用 + 调频 + 可靠性评价

相对 v1 的四处改动
  1. 架构不再是"装机倍数"，而是模块—串并联结构（见 topology.py），
     N-1 准则第一次成为可判定的性质
  2. 备用下限由拓扑推导：E_ride + 一个模块的能量（N-1 合规时）
  3. 四类业务进入调度决策：可延迟负荷用排队变量平移（见 business.py）
  4. 可靠性由独立蒙特卡洛评价（见 reliability.py），输出 LOLP 与 EENS

输出写入 runs/v2_<时间戳>/，并发布到 results/。
"""
from __future__ import annotations

import logging
import pathlib
import sys

import numpy as np
import pandas as pd
import pypsa
import xarray as xr
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from reference import business_v2 as business, checks, topology  # noqa: E402
import reliability         # noqa: E402
from runctx import RunContext   # noqa: E402
from config import ROOT,INPUTS  # noqa: E402

logging.getLogger("linopy").setLevel(logging.WARNING)
logging.getLogger("pypsa").setLevel(logging.WARNING)

RESULTS = ROOT / 'results' / 'reference_v2'

# ---------------- 站点与市场参数 ----------------
DT_H = 0.25
SOC_MAX = 0.95
P_DESIGN_MW = 10.0
E_R_FIXED = 2.5                 # 行业现行固定备用：满载 15 min
DEG_COST = 15.0                 # 元/MWh 吞吐
GRID_PNOM = 20.0
DEMAND_CHARGE = 40.0 / 30.0 * 1000
C_REG_CAP = 25.0                # 元/MW/h
T_REG_H = 0.5                   # 调频持续交付时长
EPS_CVAR = 0.01

T_GEN_MED_S = 20.0
T_GEN_SIGMA = 0.6
P_GEN_FAIL = 1e-3
T_RIDE_H = 0.25
N_SCEN_CVAR = 20000

PRICE = np.array([300]*7 + [1100]*4 + [650]*6 + [1100]*4 + [650]*3, dtype=float)
RNG = np.random.default_rng(20261110)

CHG_C_RATE = 0.5                # 充电倍率上限（放电按 UPS 额定，不按 C 率）


def gen_start_scenarios(n: int = N_SCEN_CVAR, seed: int | None = None) -> np.ndarray:
    rng = RNG if seed is None else np.random.default_rng(seed)
    t = rng.lognormal(np.log(T_GEN_MED_S), T_GEN_SIGMA, n)
    fail = rng.random(n) < P_GEN_FAIL
    t[fail] = T_RIDE_H * 3600.0
    return t / 3600.0


def cvar_reserve(load_mw: np.ndarray, t_req_h: np.ndarray,
                 eps: float = EPS_CVAR) -> tuple[np.ndarray, float, float]:
    s = np.sort(t_req_h)
    k = max(1, int(np.ceil(eps * len(s))))
    tail = s[-k:]
    return load_mw * tail.mean(), float(tail.mean()), float(tail.min())


def solve_case(arch: topology.Architecture, policy: str,
               load_b: pd.DataFrame, price: np.ndarray, e_ride: np.ndarray,
               reg_on: bool = False, flex_on: bool = False) -> dict | None:
    load = load_b.sum(axis=1).to_numpy()
    n_t = len(load)
    e = arch.e_installed
    p_ups = arch.p_deliver
    p_chg_cap = CHG_C_RATE * e

    # ---- 备用下限 ----
    if policy == "fixed":
        floor = np.full(n_t, E_R_FIXED)
    elif policy == "cvar":
        floor = np.asarray(e_ride, dtype=float)
    elif policy == "topology":
        floor = np.asarray(e_ride, dtype=float) + (topology.MODULE_E_MWH if arch.n_minus_1 else 0.0)
    elif policy == "worst":
        # 按最坏情景配置：柴发启动失败时需撑满 15 min
        floor = np.full(n_t, float(load.max()) * T_RIDE_H) + (
            topology.MODULE_E_MWH if arch.n_minus_1 else 0.0)
    else:
        raise ValueError(policy)

    if np.any(floor > SOC_MAX * e - 1e-9):
        return None                      # 备用要求超过可用能量，方案不可行

    snaps = pd.date_range("2026-01-01", periods=n_t, freq="15min")
    n = pypsa.Network()
    n.set_snapshots(snaps)
    n.snapshot_weightings.loc[:, :] = DT_H     # 必须显式设置，否则 15 min 会被当成 1 h

    n.add("Carrier", "ac")
    n.add("Bus", "dc", carrier="ac")
    n.add("Generator", "grid", bus="dc", carrier="ac",
          p_nom=GRID_PNOM, marginal_cost=price)
    n.add("Load", "it", bus="dc", p_set=load)
    # 放电按 UPS 额定功率（要能带满关键负荷），充电按 0.5C 另加约束
    n.add("StorageUnit", "bess", bus="dc", p_nom=p_ups, max_hours=e / p_ups,
          efficiency_store=0.95, efficiency_dispatch=0.95,
          cyclic_state_of_charge=True)

    specs = business.declare(n, load_b) if flex_on else {}

    m = n.optimize.create_model()
    soc = m["StorageUnit-state_of_charge"].sel(name="bess")
    p_dis = m["StorageUnit-p_dispatch"].sel(name="bess")
    p_store = m["StorageUnit-p_store"].sel(name="bess")
    p_grid = m["Generator-p"].sel(name="grid")

    floor_da = xr.DataArray(floor, coords={"snapshot": snaps})
    m.add_constraints(soc - floor_da >= 0, name="reserve_floor")
    m.add_constraints(soc <= SOC_MAX * e, name="soc_upper")
    m.add_constraints(p_store <= p_chg_cap, name="charge_c_rate")

    # 退化成本：与报表口径逐项一致
    m.objective = m.objective + DEG_COST * DT_H * 0.5 * (p_dis + p_store).sum()

    # 需量电费
    peak = m.add_variables(lower=0, name="peak_import")
    m.add_constraints(peak - p_grid >= 0, name="peak_def")
    m.objective = m.objective + DEMAND_CHARGE * peak

    # 调频
    r_up_v = r_dn_v = None
    if reg_on:
        r_up = m.add_variables(lower=0, coords=[pd.Index(snaps, name="snapshot")], name="reg_up")
        r_dn = m.add_variables(lower=0, coords=[pd.Index(snaps, name="snapshot")], name="reg_dn")
        m.add_constraints(p_dis + r_up <= p_ups, name="reg_power_up")
        m.add_constraints(p_store + r_dn <= p_chg_cap, name="reg_power_dn")
        m.add_constraints(soc - r_up * T_REG_H - floor_da >= 0, name="reg_energy_up")
        m.add_constraints(soc + r_dn * T_REG_H <= SOC_MAX * e, name="reg_energy_dn")
        m.objective = m.objective - C_REG_CAP * DT_H * (r_up.sum() + r_dn.sum())

    flex = business.constrain(m, specs, snaps) if flex_on else {}

    n.optimize.solve_model(solver_name="highs", log_to_console=False)

    soc_v = n.storage_units_t.state_of_charge["bess"].to_numpy()
    p_dis_v = n.storage_units_t.p_dispatch["bess"].to_numpy()
    p_store_v = n.storage_units_t.p_store["bess"].to_numpy()
    pg = n.generators_t.p["grid"].to_numpy()
    reg = 0.0
    if reg_on:
        r_up_v = n.model.variables["reg_up"].solution.to_numpy()
        r_dn_v = n.model.variables["reg_dn"].solution.to_numpy()
        reg = float(r_up_v.sum() + r_dn_v.sum())

    energy_cost = float((pg * price).sum() * DT_H)
    demand_cost = DEMAND_CHARGE * float(pg.max())
    throughput = float(np.abs(p_dis_v - p_store_v).sum() * DT_H * 0.5)
    deg_cost = throughput * DEG_COST
    reg_rev = reg * DT_H * C_REG_CAP

    bus_extra = None
    if flex:
        bus_extra = (n.generators_t.p[[f"def_{k}" for k in flex]].sum(axis=1).to_numpy()
                     + n.generators_t.p[[f"rec_{k}" for k in flex]].sum(axis=1).to_numpy())
    rep = checks.verify_solution(
        soc=soc_v, p_dispatch=p_dis_v, p_store=p_store_v, p_grid=pg, load=load,
        dt_h=DT_H, eta_store=0.95, eta_dispatch=0.95,
        e_rated=e, soc_max_frac=SOC_MAX, floor=floor,
        p_rated=p_ups, throughput=throughput,
        reg_up=r_up_v, reg_dn=r_dn_v, t_reg_h=T_REG_H if reg_on else None,
        bus_extra=bus_extra,
    )
    # 充电倍率与功率上限的额外约束
    rep.add("charge_c_rate", "p_chg ≤ 0.5C", float(max(0.0, (p_store_v - p_chg_cap).max())), 1e-6)
    rep.add("soc_upper", f"soc ≤ {SOC_MAX:.2f}·E", float(max(0.0, (soc_v - SOC_MAX * e).max())), 1e-6)
    biz_df = None
    if flex:
        rep.add("work_conservation", "日内推迟电量 = 补做电量",
                business.check_conservation(n, flex, DT_H), 1e-6)
        biz_df = business.summarize(n, flex, DT_H)
    rep.checks.extend(checks.verify_accounting(
        objective=n.objective, energy_cost=energy_cost, demand_cost=demand_cost,
        deg_cost=deg_cost, reg_rev=reg_rev).checks)

    return dict(
        arch=arch.name, policy=policy, e_installed=e, p_ups=p_ups,
        energy_cost=energy_cost, demand_cost=demand_cost,
        deg_cost=deg_cost, reg_rev=reg_rev,
        cost=energy_cost + demand_cost + deg_cost - reg_rev,
        peak=float(pg.max()), throughput=throughput, cycles=throughput / e,
        reg_mw=reg / n_t,
        reg_up_mw=float(r_up_v.mean()) if reg_on else 0.0,
        floor_mwh=float(np.mean(floor)),
        usable_mwh=float(np.mean(SOC_MAX * e - floor)),
        _soc=soc_v, _pg=pg, _flex=biz_df, _checks=rep,
    )


def main() -> None:
    RESULTS.mkdir(parents=True,exist_ok=True)
    run = RunContext(ROOT, prefix="v2")
    out = run.dir
    load_path = run.freeze_input(INPUTS / "load_business_24h.csv")
    run.write_config({
        "DT_H": DT_H, "SOC_MAX": SOC_MAX, "P_DESIGN_MW": P_DESIGN_MW,
        "E_R_FIXED": E_R_FIXED, "DEG_COST": DEG_COST, "DEMAND_CHARGE": DEMAND_CHARGE,
        "C_REG_CAP": C_REG_CAP, "T_REG_H": T_REG_H, "EPS_CVAR": EPS_CVAR,
        "CHG_C_RATE": CHG_C_RATE, "PRICE": PRICE.tolist(),
        "MODULE_E_MWH": topology.MODULE_E_MWH, "MODULE_P_MW": topology.MODULE_P_MW,
        "architectures": {k: dict(modules=a.modules, strings=a.strings, standby=a.standby)
                          for k, a in topology.ARCHITECTURES.items()},
        "business": {k: dict(f=v.f, window_h=v.window_h, ramp_frac=v.ramp_frac)
                     for k, v in business.BUSINESS.items()},
        "reliability": dict(n_scen=20000, load_sigma=0.05, t_gen_med_s=T_GEN_MED_S,
                            t_gen_sigma=T_GEN_SIGMA, p_gen_fail=P_GEN_FAIL),
    })
    run.write_env()
    run.write_provenance()
    print(f"运行目录 {out.relative_to(ROOT)}")

    issues = topology.audit()
    if issues:
        raise SystemExit("拓扑自检失败：\n" + "\n".join(issues))

    df_load = pd.read_csv(load_path)
    load_b = df_load[["p_train_MW", "p_infer_MW", "p_batch_MW", "p_deliv_MW"]].rename(
        columns=lambda c: c.replace("p_", "").replace("_MW", ""))
    load = load_b.sum(axis=1).to_numpy()
    price = PRICE.repeat(4)[:len(load)]

    e_ride, cvar_t, var_t = cvar_reserve(load, gen_start_scenarios())
    print(f"CVaR 穿越备用: τ={cvar_t*3600:.0f} s，平均 {e_ride.mean():.3f} MWh\n")

    # ---------------- 架构属性表 ----------------
    topo = pd.DataFrame(topology.describe_all(float(e_ride.mean()), SOC_MAX))
    topo.to_csv(out / "topology_v2.csv", index=False, float_format="%.4f")
    print("架构拓扑：")
    print(topo[["arch", "modules", "E_installed", "P_post", "N-1",
                "E_dispatchable", "E_dispatchable_n1", "stranded_frac"]].to_string(index=False))
    print()

    # ---------------- 主对比 ----------------
    rows, all_checks = [], []
    for name, arch in topology.ARCHITECTURES.items():
        for policy, reg, flex in (("fixed", False, False),
                                  ("topology", True, True)):
            r = solve_case(arch, policy, load_b, price, e_ride, reg_on=reg, flex_on=flex)
            if r is None:
                rows.append(dict(arch=name, policy=policy, e_installed=arch.e_installed,
                                 cost=np.nan, energy_cost=np.nan, demand_cost=np.nan,
                                 deg_cost=0.0, reg_rev=0.0, peak=np.nan, throughput=0.0,
                                 cycles=0.0, reg_mw=0.0, reg_up_mw=0.0,
                                 floor_mwh=np.nan, usable_mwh=np.nan))
                continue
            rep = r.pop("_checks")
            all_checks.append((name, policy, rep))
            print(f"  {name:<7s} {policy:<9s} {rep.summary()}")
            r.pop("_soc"); r.pop("_pg"); r.pop("_flex")
            rows.append(r)
    df = pd.DataFrame(rows)

    base_cost = float((load * price).sum() * DT_H + DEMAND_CHARGE * load.max())
    df["saving"] = base_cost - df["cost"]
    df["saving_per_mwh"] = df["saving"] / df["e_installed"]
    df["saving_per_dispatchable"] = df["saving"] / df["usable_mwh"]
    df.to_csv(out / "results_v2.csv", index=False, float_format="%.4f")

    piv = df.pivot(index="arch", columns="policy", values="cost").reindex(list(topology.ARCHITECTURES))
    sav = df[df.policy == "topology"].set_index("arch").reindex(list(topology.ARCHITECTURES))
    print(f"\n无储能日成本 {base_cost:,.0f} 元")
    print("\n主对比（元/日）：")
    summary = pd.DataFrame({
        "装机MWh": [a.e_installed for a in topology.ARCHITECTURES.values()],
        "N-1": ["合规" if a.n_minus_1 else "不合规" for a in topology.ARCHITECTURES.values()],
        "基线": piv["fixed"].round(0),
        "模块": piv["topology"].round(0),
        "节省": sav["saving"].round(0),
        "元/MWh": sav["saving_per_mwh"].round(0),
        "调频收益": sav["reg_rev"].round(0),
        "可调度MWh": sav["usable_mwh"].round(2),
    })
    print(summary.to_string())

    # ---------------- 消融 ----------------
    configs = [
        ("A 固定备用", "fixed", False, False),
        ("B CVaR 穿越备用", "cvar", False, False),
        ("C + 拓扑冗余备用", "topology", False, False),
        ("D + 调频", "topology", True, False),
        ("E + 业务柔性", "topology", True, True),
    ]
    abl = {}
    for label, pol, reg, flex in configs:
        col = {}
        for name, arch in topology.ARCHITECTURES.items():
            r = solve_case(arch, pol, load_b, price, e_ride, reg_on=reg, flex_on=flex)
            col[name] = np.nan if r is None else r["cost"]
        abl[label] = col
    abl_df = pd.DataFrame(abl).reindex(list(topology.ARCHITECTURES))
    abl_df.to_csv(out / "ablation_v2.csv", float_format="%.1f")
    print("\n消融（日净成本，元）：")
    print(abl_df.round(0).to_string())

    # ---------------- 业务平移明细 ----------------
    r_demo = solve_case(topology.ARCHITECTURES["2(N+1)"], "topology", load_b,
                        price, e_ride, reg_on=True, flex_on=True)
    biz = r_demo["_flex"]
    biz.to_csv(out / "business_v2.csv", index=False, float_format="%.4f")
    print("\n四类业务的平移（2(N+1)，模块方案）：")
    print(biz[["name", "deferred_MWh", "recovered_MWh", "peak_defer_MW",
               "queue_max_MWh", "q_cap_MWh"]].to_string(index=False))

    # ---------------- 可靠性 ----------------
    rel_rows = []
    for i_arch, (name, arch) in enumerate(topology.ARCHITECTURES.items()):
        for policy, reg, flex in (("fixed", False, False),
                                  ("worst", True, True),
                                  ("topology", True, True)):
            r = solve_case(arch, policy, load_b, price, e_ride, reg_on=reg, flex_on=flex)
            if r is None:
                rel_rows.append(dict(arch=name, policy=policy, LOLP=np.nan,
                                     EENS_MWh=np.nan, CVaR95_MWh=np.nan,
                                     worst_event_MWh=np.nan, EENS_ci_lo=np.nan,
                                     EENS_ci_hi=np.nan, cost=np.nan, saving=np.nan))
                continue
            mc = reliability.monte_carlo(soc=r["_soc"], load=load, p_ups=arch.p_deliver,
                                         seed=20261110 + 101 * i_arch)
            rel_rows.append(dict(arch=name, policy=policy, LOLP=mc["LOLP"],
                                 EENS_MWh=mc["EENS_MWh"], CVaR95_MWh=mc["CVaR95_MWh"],
                                 worst_event_MWh=mc["worst_event_MWh"],
                                 EENS_ci_lo=mc["EENS_ci_lo"], EENS_ci_hi=mc["EENS_ci_hi"],
                                 cost=r["cost"], saving=base_cost - r["cost"]))
    rel = pd.DataFrame(rel_rows)
    rel.to_csv(out / "reliability_v2.csv", index=False, float_format="%.6f")
    print("\n可靠性（20,000 次故障情景；LOLP 为条件概率，EENS 为单次故障期望失供）：")
    print(rel[["arch", "policy", "LOLP", "EENS_MWh", "saving"]].to_string(
        index=False, float_format=lambda x: f"{x:.4g}"))

    # ---------------- 核验明细 ----------------
    chk = pd.DataFrame([dict(arch=a, policy=p, **row)
                        for a, p, rep in all_checks for row in rep.to_rows()])
    chk.to_csv(out / "checks_v2.csv", index=False, float_format="%.3e")
    n_fail = int((~chk["pass"]).sum())
    print(f"\n[checks] {len(chk)} 项，失败 {n_fail} 项")

    # ---------------- 图 ----------------
    _plot(out, topo, summary, abl_df, biz, rel)

    # ---------------- 封存 ----------------
    md = _write_md(out, base_cost, cvar_t, summary, abl_df, biz, rel, topo)
    seal = run.seal()
    run.publish(["results_v2.csv", "topology_v2.csv", "ablation_v2.csv",
                 "business_v2.csv", "reliability_v2.csv", "checks_v2.csv",
                 "comparison_v2.md", "fig_v2_topology.png", "fig_v2_business.png",
                 "fig_v2_reliability.png"], RESULTS)
    print(f"\n封存 {seal['count']} 个文件 -> {out.relative_to(ROOT)}")
    if n_fail:
        print(f"警告：{n_fail} 项核验未通过")
        raise SystemExit(1)


def _plot(out, topo, summary, abl_df, biz, rel):
    fig, ax = plt.subplots(1, 3, figsize=(16, 4.6))
    names = topo["arch"].tolist()
    x = np.arange(len(names))
    ax[0].bar(x, topo["E_dispatchable"], 0.55, color="#4f81bd", label="可调度")
    ax[0].bar(x, topo["E_stranded"], 0.55, bottom=topo["E_dispatchable"],
              color="#d9d9d9", label="闲置")
    ax[0].set_xticks(x); ax[0].set_xticklabels(names)
    ax[0].set_ylabel("MWh"); ax[0].set_title("(a) 可调度域与闲置容量（不承诺 N-1）")
    ax[0].legend(fontsize=8); ax[0].grid(axis="y", alpha=.3)
    for xi, (d, s) in enumerate(zip(topo["E_dispatchable"], topo["E_stranded"])):
        ax[0].text(xi, d + s + 0.08, f"{s/(d+s):.0%}", ha="center", fontsize=8)

    ax[1].bar(x, summary["节省"], 0.55, color="#c0504d")
    ax[1].set_xticks(x); ax[1].set_xticklabels(names)
    ax[1].set_ylabel("元/日"); ax[1].set_title("(b) 相对无储能的日节省")
    ax[1].grid(axis="y", alpha=.3)

    ax[2].bar(x, summary["元/MWh"], 0.55, color="#9bbb59")
    ax[2].set_xticks(x); ax[2].set_xticklabels(names)
    ax[2].set_ylabel("元/MWh/日"); ax[2].set_title("(c) 单位装机收益")
    ax[2].grid(axis="y", alpha=.3)
    ax[2].set_ylim(summary["元/MWh"].min() * 0.93, summary["元/MWh"].max() * 1.03)
    fig.tight_layout(); fig.savefig(out / "fig_v2_topology.png", dpi=160)

    fig2, ax2 = plt.subplots(1, 2, figsize=(12, 4.2))
    ax2[0].barh(biz["name"], biz["deferred_MWh"], color="#4f81bd")
    ax2[0].set_xlabel("日内平移电量（MWh）"); ax2[0].set_title("(a) 各类业务的实际平移量")
    ax2[0].grid(axis="x", alpha=.3)
    ax2[1].bar(biz["name"], biz["queue_max_MWh"] / biz["q_cap_MWh"].replace(0, np.nan),
               color="#c0504d")
    ax2[1].set_ylabel("队列占用 / 容量上限")
    ax2[1].set_title("(b) 积压队列的使用率"); ax2[1].tick_params(axis="x", rotation=15)
    ax2[1].grid(axis="y", alpha=.3)
    fig2.tight_layout(); fig2.savefig(out / "fig_v2_business.png", dpi=160)

    fig3, ax3 = plt.subplots(1, 2, figsize=(13, 4.6))
    styles = {"fixed": ("s", "#8fa6c4", "基线（固定 2.5 MWh）"),
              "worst": ("^", "#9bbb59", "最坏情景备用"),
              "topology": ("o", "#c0504d", "CVaR 备用（模块方案）")}
    floor_x = 1e-7
    for pol, (mk, col, lab) in styles.items():
        d = rel[rel.policy == pol]
        x = d["EENS_MWh"].clip(lower=floor_x)
        ax3[0].scatter(x, d["saving"], marker=mk, color=col, s=70, label=lab, zorder=3)
    for _, r in rel[rel.policy == "topology"].iterrows():
        ax3[0].annotate(r["arch"], (max(r["EENS_MWh"], floor_x), r["saving"]),
                        textcoords="offset points", xytext=(6, 4), fontsize=8)
    ax3[0].set_xscale("log")
    ax3[0].set_xlabel(f"EENS（MWh/次故障，对数轴；{floor_x:g} 表示未观测到失负荷）")
    ax3[0].set_ylabel("相对无储能日节省（元/日）")
    ax3[0].set_title("(a) 可靠性—收益前沿")
    ax3[0].legend(fontsize=8); ax3[0].grid(alpha=.3)

    piv_sav = rel.pivot(index="arch", columns="policy", values="saving").reindex(names)
    xx = np.arange(len(names)); w = 0.26
    for k, (pol, (_mk, col, lab)) in enumerate(styles.items()):
        ax3[1].bar(xx + (k - 1) * w, piv_sav[pol], w, color=col, label=lab)
    ax3[1].set_xticks(xx); ax3[1].set_xticklabels(names)
    ax3[1].set_ylabel("元/日"); ax3[1].set_title("(b) 三种备用方案的收益")
    ax3[1].legend(fontsize=8); ax3[1].grid(axis="y", alpha=.3)
    fig3.tight_layout(); fig3.savefig(out / "fig_v2_reliability.png", dpi=160)


def _write_md(out, base_cost, cvar_t, summary, abl_df, biz, rel, topo):
    md = ["# v2：拓扑实体化 + 业务柔性 + 可靠性评价\n",
          f"无储能日成本 {base_cost:,.0f} 元；CVaR 穿越备用 τ={cvar_t*3600:.0f} s\n",
          "## 架构可调度域\n",
          "| 架构 | 模块 | 装机 MWh | 故障后 MWh | N-1 | 可调度 MWh | 承诺 N-1 后 | 闲置占比 |",
          "|---|---|---|---|---|---|---|---|"]
    for _, r in topo.iterrows():
        md.append(f"| {r['arch']} | {r['modules']} | {r['E_installed']:.1f} | "
                  f"{r['E_post']:.1f} | {r['N-1']} | {r['E_dispatchable']:.2f} | "
                  f"{r['E_dispatchable_n1']:.2f} | {r['stranded_frac']:.1%} |")
    md += ["", "## 主对比（元/日）", "",
           "| 架构 | 装机 MWh | N-1 | 基线 | 模块 | 节省 | 元/MWh | 调频收益 |",
           "|---|---|---|---|---|---|---|---|"]
    for name in summary.index:
        r = summary.loc[name]
        base_txt = "不可行" if pd.isna(r["基线"]) else f"{r['基线']:,.0f}"
        md.append(f"| {name} | {r['装机MWh']:.1f} | {r['N-1']} | {base_txt} | "
                  f"{r['模块']:,.0f} | {r['节省']:,.0f} | {r['元/MWh']:,.0f} | "
                  f"{r['调频收益']:,.0f} |")
    md += ["", "## 消融（日净成本，元）", "",
           "| 架构 | " + " | ".join(abl_df.columns) + " |",
           "|---" * (len(abl_df.columns) + 1) + "|"]
    for name in abl_df.index:
        md.append(f"| {name} | " + " | ".join(
            "不可行" if pd.isna(abl_df.loc[name, c]) else f"{abl_df.loc[name, c]:,.0f}"
            for c in abl_df.columns) + " |")
    md += ["", "## 业务平移", "",
           "| 业务 | 平移电量 MWh | 峰值 MW | 队列峰值 MWh | 队列上限 MWh |",
           "|---|---|---|---|---|"]
    for _, r in biz.iterrows():
        md.append(f"| {r['name']} | {r['deferred_MWh']:.3f} | {r['peak_defer_MW']:.3f} | "
                  f"{r['queue_max_MWh']:.3f} | {r['q_cap_MWh']:.3f} |")
    md += ["", "## 可靠性", "",
           "LOLP 为**条件失负荷概率**——给定发生一次电网失电，储能与柴发未能覆盖穿越期的概率；"
           "EENS 为**单次故障**的期望失供电量。二者均不含故障发生频率 λ，"
           "年度指标需再乘 λ·365。", "",
           "| 架构 | 方案 | LOLP\\|故障 | EENS MWh/次 | 95% 区间 | 日节省 元 |",
           "|---|---|---|---|---|---|"]
    for _, r in rel.iterrows():
        if pd.isna(r["LOLP"]):
            md.append(f"| {r['arch']} | {r['policy']} | 不可行 | — | — | — |")
            continue
        md.append(f"| {r['arch']} | {r['policy']} | {r['LOLP']:.4%} | "
                  f"{r['EENS_MWh']:.5f} | [{r['EENS_ci_lo']:.5f}, {r['EENS_ci_hi']:.5f}] | "
                  f"{r['saving']:,.0f} |")
    (out / "comparison_v2.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return md


if __name__ == "__main__":
    main()
