"""V3 business parameter definitions. Queue/deadline equations live in dispatch.py.
The historical V2 queue implementation is isolated in reference/business_v2.py.
"""
from __future__ import annotations

from dataclasses import dataclass



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
