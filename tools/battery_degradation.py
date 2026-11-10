"""
用 PyBaMM 标定电池循环退化成本，输出供 PyPSA 使用的 元/MWh 参数。

方法：
  对每个放电深度 DoD，跑 N 个恒流循环，用线性拟合得到每循环容量衰减率，
  再按 电池造价 / 可吞吐能量 折算为边际退化成本。

注意：这是 v0 的粗标定（线性拟合、恒温、无日历老化）。
      v1 应替换为 雨流计数 + 半经验寿命模型，并分离日历/循环老化。
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd
import pybamm

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'sim'))
from config import INPUTS
OUT = INPUTS

CAPEX_YUAN_PER_KWH = 800.0   # LFP 系统造价（含 PCS 分摊），可按需改
N_CYCLES = 12
C_RATE = 0.5
DODS = [0.3, 0.6, 0.9]


def cycle_fade(dod: float, n_cycles: int = N_CYCLES) -> tuple[float, np.ndarray]:
    """返回 (每循环容量衰减比例, 各循环放电容量数组)。"""
    v_hi = 4.2
    v_lo = 4.2 - dod * (4.2 - 3.0)
    model = pybamm.lithium_ion.SPM({"SEI": "solvent-diffusion limited"})
    param = pybamm.ParameterValues("OKane2022")
    step = (f"Discharge at {C_RATE}C until {v_lo:.3f} V",
            f"Charge at {C_RATE}C until {v_hi} V",
            f"Hold at {v_hi} V until 50 mA")
    exp = pybamm.Experiment([step] * n_cycles)
    sol = pybamm.Simulation(model, parameter_values=param, experiment=exp).solve()

    q = np.array([float(c["Discharge capacity [A.h]"].entries.max())
                  for c in sol.cycles])
    # 跳过第 1 圈（化成/初始 SEI 瞬态），只拟合稳态段
    idx = np.arange(2, len(q) + 1)
    b, a = np.polyfit(idx, q[1:], 1)
    q0 = float(param["Nominal cell capacity [A.h]"])
    fade_per_cycle = -b / q0
    return float(fade_per_cycle), q


def main() -> None:
    OUT.mkdir(parents=True,exist_ok=True)
    rows = []
    for dod in DODS:
        fade, q = cycle_fade(dod)
        # 每循环吞吐（按名义容量折算，kWh/kWh_额定 = 2*DoD）
        throughput_per_cycle = 2 * dod
        # 元/kWh_capex × (衰减/循环) ÷ (每循环吞吐 kWh/kWh_额定) × 1000 -> 元/MWh
        cost_per_mwh = CAPEX_YUAN_PER_KWH * fade / throughput_per_cycle * 1000
        rows.append(dict(DoD=dod, fade_per_cycle=fade,
                         fade_pct=100 * fade,
                         Q1=q[0], QN=q[-1],
                         cost_yuan_per_MWh=cost_per_mwh))
        print(f"DoD={dod:.1f}: 每循环衰减 {100*fade:.4f}%  "
              f"-> {cost_per_mwh:.1f} 元/MWh")

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "degradation_calibration.csv", index=False, float_format="%.6f")
    print(f"\n写入 {OUT/'degradation_calibration.csv'}")
    print("建议取 DoD=0.9 对应值作为 PyPSA 的 DEG_COST 参数。")


if __name__ == "__main__":
    main()
