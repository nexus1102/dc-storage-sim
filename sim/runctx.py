"""
运行封存：让每个数字都能追到"哪次运行、哪份输入、哪版参数"。

三条规则
  1. 每次运行独占一个新目录 runs/<时间戳>/，已存在就报错，不覆盖
  2. 运行目录自带冻结输入（inputs/）与参数快照（run_config.json），
     拷走这个目录就能重算，不依赖原始数据在哪
  3. results/ 只是"发布副本"，由 PUBLISHED.json 记录它来自哪次运行、
     每个文件的哈希是多少；发布副本与封存原件不一致时，以 runs/ 为准

第 3 条对应 CloudSim-VM-Energy-Lab 里 COPIED_HASH_VERIFIED 的区分：
哈希一致只证明副本没被改坏，不证明它是最新一次运行。
"""
from __future__ import annotations

import hashlib
import json
import platform
import shutil
import sys
from datetime import datetime
from importlib import metadata
from pathlib import Path

TRACKED_PACKAGES = ("pypsa", "linopy", "highspy", "pandas", "numpy", "xarray", "matplotlib", "scipy", "pytest")


def snapshot_source(destination: Path) -> None:
    """Snapshot current source including the mandatory reference subpackage."""
    source = Path(__file__).resolve().parent
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for path in sorted(source.rglob('*.py')):
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    (destination/'source_layout.json').write_text(
        json.dumps({'layout_version': 2, 'inputs': 'data/inputs', 'raw': 'data/raw',
                    'historical_baseline': 'baseline/v2_reference.csv'}, indent=2), encoding='utf-8')


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class RunContext:
    def __init__(self, root: Path, prefix: str = "v1") -> None:
        self.root = Path(root).resolve()
        self.run_id = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        self.dir = self.root / "runs" / self.run_id
        if self.dir.exists():
            raise FileExistsError(f"运行目录已存在，拒绝覆盖：{self.dir}")
        self.dir.mkdir(parents=True)
        self._inputs: list[dict] = []

    # ---------------- 输入 ----------------
    def freeze_input(self, path: Path) -> Path:
        """把输入文件复制进运行目录并登记哈希。返回冻结副本的路径。"""
        path = Path(path)
        dest_dir = self.dir / "inputs"
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / path.name
        if dest.exists():
            raise FileExistsError(f'冻结输入重名：{path.name}')
        shutil.copy2(path, dest)
        self._inputs.append({
            "name": path.name,
            "source_path": str(path.resolve().relative_to(self.root)),
            "source_sha256": sha256(path),
            "frozen_path": str(dest.relative_to(self.dir)),
            "frozen_sha256": sha256(dest),
            "bytes": dest.stat().st_size,
        })
        return dest

    def write_provenance(self) -> None:
        lines = ["name,source_path,source_sha256,frozen_path,frozen_sha256,bytes"]
        for r in self._inputs:
            lines.append(",".join(str(r[k]) for k in
                                  ("name", "source_path", "source_sha256",
                                   "frozen_path", "frozen_sha256", "bytes")))
        (self.dir / "provenance.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---------------- 配置与环境 ----------------
    def write_config(self, cfg: dict) -> None:
        (self.dir / "run_config.json").write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_env(self) -> None:
        pkgs = {}
        for name in TRACKED_PACKAGES:
            try:
                pkgs[name] = metadata.version(name)
            except metadata.PackageNotFoundError:
                pkgs[name] = None
        env = {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "packages": pkgs,
        }
        (self.dir / "env.json").write_text(
            json.dumps(env, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------------- 封存 ----------------
    def seal(self) -> dict:
        files = []
        for p in sorted(self.dir.rglob("*")):
            if p.is_file() and p.name != "SEAL.json":
                files.append({"path": str(p.relative_to(self.dir)),
                              "sha256": sha256(p), "bytes": p.stat().st_size})
        seal = {"run_id": self.run_id, "files": files, "count": len(files)}
        (self.dir / "SEAL.json").write_text(
            json.dumps(seal, ensure_ascii=False, indent=2), encoding="utf-8")
        return seal

    # ---------------- 发布 ----------------
    def publish(self, names: list[str], dest: Path) -> None:
        """把指定产物复制到 results/，并写 PUBLISHED.json 指向本次运行。"""
        dest = Path(dest)
        dest.mkdir(parents=True,exist_ok=True)
        published = {}
        for name in names:
            src = self.dir / name
            if not src.is_file():
                continue
            shutil.copy2(src, dest / name)
            published[name] = {"run_id": self.run_id,
                               "source": str(src.relative_to(self.root)),
                               "sha256": sha256(dest / name)}
        (dest / "PUBLISHED.json").write_text(
            json.dumps({"latest_run": self.run_id,
                        "sealed_at": datetime.now().isoformat(timespec="seconds"),
                        "files": published}, ensure_ascii=False, indent=2),
            encoding="utf-8")
