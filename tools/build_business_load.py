"""
把真实轨迹拆成四类业务负荷，供差异化调度使用。

划分依据只用 pod 上**可观测**的属性（QoS 等级与存活时长），不假设真实业务标签：

    AI 训练    GPU pod，存活 > 2 h          —— 长任务，可 checkpoint，有限可延迟
    AI 推理    GPU pod，QoS=LS，存活 ≤ 2 h   —— 时延敏感，不可延迟
    批处理     GPU pod，QoS=BE，存活 ≤ 2 h   —— 尽力而为，高度可延迟
    可交付     CPU pod                      —— 带交付期，中等可延迟

四类负荷由同一段真实时间窗口切出（窗口按总 GPU+CPU 并发量最大选取），
再整体缩放到与 v1 相同的日均 3.70 MW。缩放是单一系数，不改变各类之间的比例——
也就是说，四类业务的构成由数据决定，不由人为权重决定。

输出：data/inputs/load_business_24h.csv, results/input_build/fig_business_sources.png
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'sim'))
from config import ROOT, INPUTS, RAW_DATA
OUT = INPUTS

KW_PER_GPU = 0.45          # kW per GPU（含板卡与风扇）
KW_PER_CORE = 0.015        # kW per CPU core
TARGET_MEAN_MW = 3.70
N_BINS = 96
GRID_S = 900
TRAIN_MIN_H = 2.0          # 超过 2 h 的 GPU pod 记为训练


def _find(name: str) -> pathlib.Path:
    direct = RAW_DATA / name
    if direct.exists():
        return direct
    hits = sorted((ROOT / "external").rglob(name))
    if hits:
        return hits[0]
    raise FileNotFoundError(f"找不到数据文件 {name}")


def _concurrency(df: pd.DataFrame, value_col: str) -> pd.Series:
    """把 [creation, deletion) 的资源量扫成等间隔时间序列。"""
    d = df[df[value_col] > 0]
    if d.empty:
        return pd.Series(dtype=float)
    starts = pd.DataFrame({"t": d["creation_time"].values, "v": d[value_col].values})
    ends = pd.DataFrame({"t": d["deletion_time"].values, "v": -d[value_col].values})
    ev = pd.concat([starts, ends]).sort_values("t")
    ev["cum"] = ev["v"].cumsum()
    ev = ev.groupby("t", as_index=False)["cum"].last()
    grid = np.arange(0, ev["t"].max(), GRID_S)
    idx = np.searchsorted(ev["t"].values, grid, side="right") - 1
    vals = np.where(idx >= 0, ev["cum"].values[np.clip(idx, 0, None)], 0.0)
    return pd.Series(vals, index=grid)


def _slice_on(grid: pd.Series, start: float, n: int = N_BINS) -> np.ndarray:
    """在公共时间轴上按起点截取 n 个 bin，缺的补 0。"""
    out = np.zeros(n)
    vals = grid.reindex(grid.index).to_numpy()
    idx = np.searchsorted(grid.index.to_numpy(), start, side="left")
    seg = vals[idx: idx + n]
    out[:len(seg)] = seg
    return out


def build() -> pd.DataFrame:
    gpu = pd.read_csv(_find("openb_pod_list_default.csv"))
    cpu = pd.read_csv(_find("openb_pod_list_cpu050.csv"))

    gpu["gpu_units"] = gpu["num_gpu"] * gpu["gpu_milli"] / 1000.0
    gpu["dur_h"] = (gpu["deletion_time"] - gpu["creation_time"]) / 3600.0
    gpu["qos"] = gpu["qos"].fillna("NA")

    is_train = gpu["dur_h"] > TRAIN_MIN_H
    is_infer = (~is_train) & (gpu["qos"] == "LS")
    gpu_batch = (~is_train) & (gpu["qos"] == "BE")
    leftover = ~(is_train | is_infer | gpu_batch)

    cpu["cores"] = cpu["cpu_milli"] / 1000.0
    cpu["qos"] = cpu["qos"].fillna("NA")
    cpu_batch = cpu["qos"] == "BE"          # 尽力而为的 CPU 作业，可延迟
    cpu_deliv = ~cpu_batch                  # 其余 CPU 作业，带交付期

    g_all = _concurrency(gpu, "gpu_units")
    c_all = _concurrency(cpu, "cores")

    # 公共时间窗口：在总并发（GPU 单位 + CPU 核 /30）上取最繁忙的 24 h
    joint = g_all.add(c_all / 30.0, fill_value=0.0)
    start = float(joint.rolling(N_BINS).sum().idxmax())
    # rolling 的 idxmax 给出窗口末端，换算回起点
    t_index = joint.index.to_numpy()
    end_pos = int(np.searchsorted(t_index, start))
    start = float(t_index[max(0, end_pos - N_BINS + 1)])

    gpu_batch_s = _slice_on(_concurrency(gpu[gpu_batch], "gpu_units"), start) * KW_PER_GPU
    cpu_batch_s = _slice_on(_concurrency(cpu[cpu_batch], "cores"), start) * KW_PER_CORE
    series = {
        "train": _slice_on(_concurrency(gpu[is_train], "gpu_units"), start) * KW_PER_GPU,
        "infer": _slice_on(_concurrency(gpu[is_infer], "gpu_units"), start) * KW_PER_GPU,
        "batch": gpu_batch_s + cpu_batch_s,
        "deliv": _slice_on(_concurrency(cpu[cpu_deliv], "cores"), start) * KW_PER_CORE,
    }
    raw = sum(series.values())
    scale = TARGET_MEAN_MW / raw.mean()
    df = pd.DataFrame({
        "hour": np.arange(N_BINS) * 0.25,
        "p_train_MW": scale * series["train"],
        "p_infer_MW": scale * series["infer"],
        "p_batch_MW": scale * series["batch"],
        "p_deliv_MW": scale * series["deliv"],
    })
    df["p_total_MW"] = df[["p_train_MW", "p_infer_MW", "p_batch_MW", "p_deliv_MW"]].sum(axis=1)

    meta = {
        "pods_train": int(is_train.sum()), "pods_infer": int(is_infer.sum()),
        "pods_batch": int(gpu_batch.sum()) + int(cpu_batch.sum()),
        "pods_leftover": int(leftover.sum()),
        "pods_cpu": int(len(cpu)), "scale": float(scale),
        "window_start_s": start, "share": {
            k: float(v.mean() / raw.mean()) for k, v in series.items()},
    }
    return df, meta


def main() -> None:
    OUT.mkdir(parents=True,exist_ok=True)
    df, meta = build()
    df.to_csv(OUT / "load_business_24h.csv", index=False, float_format="%.4f")
    p = df.p_total_MW.to_numpy()
    print(f"日均 {p.mean():.2f} MW，峰 {p.max():.2f}，谷 {p.min():.2f}，"
          f"峰谷比 {p.max()/p.min():.2f}")
    print(f"pod 划分：训练 {meta['pods_train']}，推理 {meta['pods_infer']}，"
          f"批处理 {meta['pods_batch']}，未归类 {meta['pods_leftover']}，CPU {meta['pods_cpu']}")
    print("负荷构成：" + "，".join(f"{k} {v:.1%}" for k, v in meta["share"].items()))
    for c in ["p_train_MW", "p_infer_MW", "p_batch_MW", "p_deliv_MW"]:
        print(f"  {c:<12s} 均值 {df[c].mean():.3f} MW  峰谷比 {df[c].max()/max(df[c].min(),1e-9):.2f}")

    fig, ax = plt.subplots(1, 2, figsize=(13, 4.4))
    ax[0].stackplot(df.hour, df.p_train_MW, df.p_infer_MW, df.p_batch_MW, df.p_deliv_MW,
                    labels=["AI 训练", "AI 推理", "批处理", "可交付"],
                    colors=["#c0504d", "#4f81bd", "#9bbb59", "#f79646"], alpha=.9)
    ax[0].set_xlabel("小时"); ax[0].set_ylabel("功率（MW）")
    ax[0].set_title("(a) 四类业务的 24 h 负荷构成")
    ax[0].legend(fontsize=8); ax[0].grid(alpha=.25)
    for c, col in zip(["p_train_MW", "p_infer_MW", "p_batch_MW", "p_deliv_MW"],
                      ["#c0504d", "#4f81bd", "#9bbb59", "#f79646"]):
        ax[1].plot(df.hour, df[c] / df[c].mean(), color=col, lw=1.2, label=c.replace("p_", "").replace("_MW", ""))
    ax[1].axhline(1.0, ls="--", color="gray", lw=0.8)
    ax[1].set_xlabel("小时"); ax[1].set_ylabel("归一化")
    ax[1].set_title("(b) 各类的日内形状")
    ax[1].legend(fontsize=8); ax[1].grid(alpha=.25)
    figures=ROOT/'results/input_build'; figures.mkdir(parents=True,exist_ok=True)
    fig.tight_layout(); fig.savefig(figures / "fig_business_sources.png", dpi=160)
    print(f"写入 {OUT/'load_business_24h.csv'} 与 {figures/'fig_business_sources.png'}")


if __name__ == "__main__":
    main()
