"""
核验与回归：两个子命令，只依赖 Python，不需要重跑仿真。

  python3 sim/verify.py run      runs/v1_20261110_081335
      校验一次运行的封存完整性：SEAL.json 里的每个哈希、provenance 的冻结副本、
      checks_*.csv 是否全部通过。

  python3 sim/verify.py regress  [--baseline baseline/v1_reference.csv]
      把当前 results_v1.csv 与冻结基线逐格比对，报告任何漂移。
      改代码后先跑这个：数字变了是预期内的，变了却不知道才是问题。

  python3 sim/verify.py latest
     等价于对 runs/ 下最新一次运行执行 run。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_run(run_dir: Path) -> int:
    run_dir = Path(run_dir)
    errors: list[str] = []

    seal_path = run_dir / "SEAL.json"
    if not seal_path.is_file():
        print(f"[FAIL] {run_dir} 缺少 SEAL.json（运行未完成或未封存）")
        return 1
    seal = json.loads(seal_path.read_text(encoding="utf-8"))

    # 1. 文件哈希
    for item in seal["files"]:
        p = run_dir / item["path"]
        if not p.is_file():
            errors.append(f"缺失 {item['path']}")
        elif sha256(p) != item["sha256"]:
            errors.append(f"哈希不符 {item['path']}")

    # 2. 冻结输入与来源一致
    prov = pd.read_csv(run_dir / "provenance.csv")
    for _, row in prov.iterrows():
        frozen = run_dir / row["frozen_path"]
        if not frozen.is_file():
            errors.append(f"冻结输入缺失 {row['frozen_path']}")
        elif sha256(frozen) != row["frozen_sha256"]:
            errors.append(f"冻结输入哈希不符 {row['frozen_path']}")

    # 3. 自洽性核验
    cands = sorted(run_dir.glob("checks_*.csv"))
    chk_path = cands[-1] if cands else run_dir / "checks_v1.csv"
    n_checks = n_fail = 0
    if chk_path.is_file():
        chk = pd.read_csv(chk_path)
        n_checks, n_fail = len(chk), int((~chk["pass"]).sum())
        if n_fail:
            bad = chk[~chk["pass"]][["arch", "policy", "check", "residual", "tol"]]
            errors.append(f"{n_fail} 项自洽性核验失败：\n{bad.to_string(index=False)}")
    else:
        errors.append("缺少 checks_*.csv")

    if errors:
        print(f"[FAIL] {run_dir.name}")
        for e in errors:
            print("  -", e)
        return 1
    print(f"[PASS] {run_dir.name}：{len(seal['files'])} 个文件哈希一致，"
          f"{len(prov)} 份冻结输入完整，自洽性核验 {n_checks - n_fail}/{n_checks} 通过")
    return 0


def regress(baseline: Path, current: Path, rtol: float = 1e-6) -> int:
    base = pd.read_csv(baseline).set_index(["arch", "policy"]).sort_index()
    cur = pd.read_csv(current).set_index(["arch", "policy"]).sort_index()

    only_base = base.index.difference(cur.index)
    only_cur = cur.index.difference(base.index)
    diffs: list[str] = []
    if len(only_base) or len(only_cur):
        diffs.append(f"行集合不同：基线独有 {list(only_base)}，当前独有 {list(only_cur)}")

    common_cols = [c for c in base.columns if c in cur.columns]
    if set(base.columns) != set(cur.columns):
        diffs.append(f'列集合不同：{set(base.columns) ^ set(cur.columns)}')
    common_idx = base.index.intersection(cur.index)
    for col in common_cols:
        b, c = base.loc[common_idx, col], cur.loc[common_idx, col]
        if pd.api.types.is_numeric_dtype(b) and not pd.api.types.is_bool_dtype(b):
            both_nan = b.isna() & c.isna()
            close = (b - c).abs() <= rtol * b.abs().clip(lower=1e-9)
            bad = ~(both_nan | close | (b.isna() & c.isna()))
            for idx in b.index[bad]:
                diffs.append(f"{idx} {col}: 基线 {b[idx]:.6g} -> 当前 {c[idx]:.6g}")
        else:
            for idx in b.index[b != c]:
                diffs.append(f"{idx} {col}: {b[idx]!r} -> {c[idx]!r}")

    if diffs:
        print(f"[DRIFT] 相对 {Path(baseline).name} 有 {len(diffs)} 处差异（rtol={rtol:g}）")
        for d in diffs[:40]:
            print("  -", d)
        if len(diffs) > 40:
            print(f"  ... 另有 {len(diffs) - 40} 处")
        return 1
    print(f"[PASS] 与基线一致：{len(common_idx)} 行 × {len(common_cols)} 列，rtol={rtol:g}")
    return 0


def latest_run() -> Path:
    runs = [p for p in (ROOT / 'runs').glob('*') if p.is_dir()]
    if not runs:
        raise SystemExit("runs/ 下没有任何运行")
    return max(runs,key=lambda p:p.stat().st_mtime_ns)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="校验指定运行目录")
    p_run.add_argument("run_dir", nargs="?", default=None)

    sub.add_parser("latest", help="校验最新一次运行")

    p_reg = sub.add_parser("regress", help="与冻结基线比对")
    modern=(ROOT/'baseline/v3_reference.csv').is_file()
    p_reg.add_argument("--baseline", default=str(ROOT / 'baseline' / ('v3_reference.csv' if modern else 'v1_reference.csv')))
    p_reg.add_argument("--current", default=str(ROOT / ('results/v3_stage0/results_v3.csv' if modern else 'results/results_v1.csv')))
    p_reg.add_argument("--rtol", type=float, default=1e-6)

    args = ap.parse_args()
    if args.cmd == "latest":
        sys.exit(verify_run(latest_run()))
    if args.cmd == "run":
        sys.exit(verify_run(Path(args.run_dir) if args.run_dir else latest_run()))
    if args.cmd == "regress":
        sys.exit(regress(Path(args.baseline), Path(args.current), args.rtol))


if __name__ == "__main__":
    main()
