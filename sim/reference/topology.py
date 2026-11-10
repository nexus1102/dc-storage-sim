"""
冗余架构的实体化定义：模块 → 串并联 → N-1 准则 → 可调度域。

之前的实现把五种架构简化成"装机倍数"（1.0 / 1.2 / 2.0 / 2.2 / 2.4 倍 E_R），
这样 N 与 N+1 的差别只是一个系数，看不出冗余是怎么换来的。本模块改成
物理结构：

    一个 UPS 模块 = 0.5 MWh / 2 MW
    关键负荷 10 MW 需要 5 个模块同时工作

五种架构只是模块数量与串并联方式的差别：

    N       5 模块，1 串              —— 没有备用
    N+1     6 模块，1 串，1 备用       —— 单模块冗余
    2N      10 模块，2 串，每串 5       —— 双总线，每串可独立带满负荷
    2N+1    11 模块，2 串 + 1 备用
    2(N+1)  12 模块，2 串，每串 6       —— 双总线 + 单模块冗余

N-1 准则在这里第一次成为可判定的性质，而不是一个假设：
失去任意一个模块后，剩余模块必须仍能带满 10 MW 关键负荷。

结论（可验证）：
    N       失 1 模块 → 8 MW < 10 MW   **不合规**
    N+1 及以上                        合规

由此得到的"故障后可用能量" E_post 才是真正能拿来调度的部分：
    E_disp = E_post − E_ride
其中 E_ride 是柴发启动期间的穿越电量（由 CVaR 给出，见 pipeline）。
"""
from __future__ import annotations

from dataclasses import dataclass

# ---------------- 物理参数 ----------------
MODULE_E_MWH = 0.5      # 单模块能量
MODULE_P_MW = 2.0       # 单模块功率（4C，15 min 满载放电，典型的 UPS 锂电）
N_REQUIRED = 5          # 带满 10 MW 关键负荷所需模块数
P_CRIT_MW = MODULE_P_MW * N_REQUIRED   # 10 MW


@dataclass(frozen=True)
class Architecture:
    name: str
    modules: int          # 模块总数
    strings: int          # 独立母线串数
    standby: int          # 热备用模块数（N+1 那个"+1"）

    # ---- 正常工况 ----
    @property
    def e_installed(self) -> float:
        return self.modules * MODULE_E_MWH

    @property
    def p_installed(self) -> float:
        return self.modules * MODULE_P_MW

    @property
    def p_deliver(self) -> float:
        """正常工况可交付功率，受关键负荷额定值封顶。"""
        return min(self.p_installed, P_CRIT_MW)

    # ---- N-1 故障后 ----
    @property
    def p_post(self) -> float:
        """失去一个模块后的可交付功率。"""
        return min((self.modules - 1) * MODULE_P_MW, P_CRIT_MW)

    @property
    def e_post(self) -> float:
        """失去一个模块后的可用能量。"""
        return (self.modules - 1) * MODULE_E_MWH

    @property
    def n_minus_1(self) -> bool:
        return self.p_post >= P_CRIT_MW - 1e-9

    def e_dispatchable(self, e_ride_mwh: float, require_n1: bool = True,
                       soc_max: float = 0.95) -> float:
        """冗余内生的可调度能量 = 可用能量上限 − 常备下限。

        require_n1=True  常备下限 = 穿越电量 + 一个模块（N-1 合规时）；
                         不合规架构记为 0——不能一边承诺 N-1 一边拿去套利。
        require_n1=False 只备穿越电量，放弃冗余承诺。
        """
        if require_n1 and not self.n_minus_1:
            return 0.0
        floor = e_ride_mwh + (MODULE_E_MWH if require_n1 else 0.0)
        return max(0.0, soc_max * self.e_installed - floor)

    def e_stranded(self, e_ride_mwh: float, require_n1: bool = True,
                   soc_max: float = 0.95) -> float:
        """闲置容量 = 装机 − 可调度域。"""
        return self.e_installed - self.e_dispatchable(e_ride_mwh, require_n1, soc_max)

    def reserve_floor(self, e_ride_mwh: float, require_n1: bool = True) -> float:
        """必须常备、不可调度的能量下限。"""
        floor = e_ride_mwh + (MODULE_E_MWH if require_n1 else 0.0)
        if require_n1 and not self.n_minus_1:
            return self.e_installed
        return floor


ARCHITECTURES: dict[str, Architecture] = {
    "N":      Architecture("N",      modules=5,  strings=1, standby=0),
    "N+1":    Architecture("N+1",    modules=6,  strings=1, standby=1),
    "2N":     Architecture("2N",     modules=10, strings=2, standby=0),
    "2N+1":   Architecture("2N+1",   modules=11, strings=2, standby=1),
    "2(N+1)": Architecture("2(N+1)", modules=12, strings=2, standby=2),
}


def describe_all(e_ride_mwh: float, soc_max: float = 0.95) -> list[dict]:
    """生成架构属性表，供报告与图使用。"""
    rows = []
    for a in ARCHITECTURES.values():
        rows.append({
            "arch": a.name,
            "modules": a.modules,
            "strings": a.strings,
            "standby": a.standby,
            "E_installed": a.e_installed,
            "P_deliver": a.p_deliver,
            "P_post": a.p_post,
            "E_post": a.e_post,
            "N-1": "合规" if a.n_minus_1 else "不合规",
            # 主口径：按"是否承诺 N-1"分别给出可调度域
            "E_dispatchable": a.e_dispatchable(e_ride_mwh, False, soc_max),
            "E_dispatchable_n1": a.e_dispatchable(e_ride_mwh, True, soc_max),
            "E_stranded": a.e_stranded(e_ride_mwh, False, soc_max),
            "stranded_frac": a.e_stranded(e_ride_mwh, False, soc_max) / a.e_installed,
        })
    return rows


def audit() -> list[str]:
    """结构自检：模块数、串数、备用数必须自洽。"""
    problems = []
    for a in ARCHITECTURES.values():
        if a.strings * N_REQUIRED + a.standby != a.modules:
            problems.append(
                f"{a.name}: {a.strings}×{N_REQUIRED} + {a.standby} ≠ {a.modules}")
        if a.modules * MODULE_P_MW < P_CRIT_MW:
            problems.append(f"{a.name}: 装机功率不足以带满关键负荷")
    return problems


if __name__ == "__main__":
    issues = audit()
    if issues:
        raise SystemExit("结构自检失败：\n" + "\n".join(issues))
    print("结构自检通过\n")
    ride = 0.181
    hdr = (f"{'架构':<8}{'模块':>4}{'串':>3}{'备用':>4}{'装机MWh':>9}{'故障后MWh':>10}"
           f"{'N-1':>8}{'可调度':>9}{'承诺N-1后':>10}{'闲置占比':>9}")
    print(hdr)
    for r in describe_all(ride):
        print(f"{r['arch']:<8}{r['modules']:>4}{r['strings']:>3}{r['standby']:>4}"
              f"{r['E_installed']:>9.1f}{r['E_post']:>10.1f}{r['N-1']:>8}"
              f"{r['E_dispatchable']:>9.2f}{r['E_dispatchable_n1']:>10.2f}"
              f"{r['stranded_frac']:>9.1%}")
