#!/usr/bin/env python3
"""生成文档用流程图 PDF（矢量图）。

用法（需要 matplotlib，未列入项目依赖，属可选工具）：
    python scripts/generate_diagrams.py

输出：
    docs/assets/approval-flow.pdf    审批流程图（README 用）
    docs/assets/approval-state.pdf   审批状态图（架构设计用）
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

# 中文字体（Windows 自带微软雅黑；其他平台可换成可用中文字体）
plt.rcParams["font.family"] = ["Microsoft YaHei", "SimHei", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "assets"

# 浅色主题配色
BG = "white"
BOX_FILL = "#EFF6FF"      # 浅蓝：普通节点
BOX_EDGE = "#2563EB"
DECISION_FILL = "#FDF6EC"  # 浅橙：HITL 人工节点
DECISION_EDGE = "#D97706"
FINAL_FILL = "#F0FDF4"     # 浅绿：终局节点
FINAL_EDGE = "#16A34A"
TERMINAL_FILL = "#F3F4F6"  # 灰：START / END
TERMINAL_EDGE = "#6B7280"
TEXT = "#1F2937"
ARROW = "#4B5563"


def box(ax, x, y, w, h, title, caption="", fill=BOX_FILL, edge=BOX_EDGE):
    """绘制圆角节点，title 主文字 + caption 副文字。返回中心坐标。"""
    patch = FancyBboxPatch(
        (x - w / 2, y - h / 2), w, h,
        boxstyle="round,pad=0.02,rounding_size=0.08",
        facecolor=fill, edgecolor=edge, linewidth=1.4,
    )
    ax.add_patch(patch)
    if caption:
        ax.text(x, y + 0.14, title, ha="center", va="center",
                fontsize=10.5, fontweight="bold", color=TEXT)
        ax.text(x, y - 0.17, caption, ha="center", va="center",
                fontsize=8, color="#4B5563")
    else:
        ax.text(x, y, title, ha="center", va="center",
                fontsize=10.5, fontweight="bold", color=TEXT)
    return (x, y)


def terminal(ax, x, y, label):
    """START / END 胶囊形端点。"""
    patch = FancyBboxPatch(
        (x - 0.55, y - 0.22), 1.1, 0.44,
        boxstyle="round,pad=0.02,rounding_size=0.22",
        facecolor=TERMINAL_FILL, edgecolor=TERMINAL_EDGE, linewidth=1.4,
    )
    ax.add_patch(patch)
    ax.text(x, y, label, ha="center", va="center",
            fontsize=10, fontweight="bold", color=TEXT)


def arrow(ax, start, end, label="", label_pos=None, rad=0.0, ls="-"):
    """带可选标签的箭头；rad 控制弧度（用于回环）。"""
    patch = FancyArrowPatch(
        start, end, arrowstyle="-|>", mutation_scale=14,
        color=ARROW, linewidth=1.3, linestyle=ls,
        connectionstyle=f"arc3,rad={rad}", shrinkA=2, shrinkB=2,
    )
    ax.add_patch(patch)
    if label:
        sx, sy = start
        ex, ey = end
        lx, ly = label_pos or ((sx + ex) / 2, (sy + ey) / 2)
        ax.text(lx, ly, label, ha="center", va="center", fontsize=8.5,
                color=DECISION_EDGE, fontweight="bold",
                bbox={"boxstyle": "round,pad=0.15", "facecolor": "white",
                      "edgecolor": "none"})


def new_canvas(w, h, xlim, ylim):
    fig, ax = plt.subplots(figsize=(w, h))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.axis("off")
    return fig, ax


def draw_approval_flow() -> None:
    """审批流程图：与 README 原 ASCII 图一致的纵向流程。"""
    fig, ax = new_canvas(10, 9.2, (0, 14), (-0.6, 10.4))

    terminal(ax, 7, 9.9, "START")
    box(ax, 7, 8.6, 4.1, 0.78, "intake",
                 "材料接收与解析（收入证明/征信报告/资产负债表）")
    box(ax, 7, 7.1, 4.1, 0.78, "risk_assessment",
               "RAG 检索政策依据 → 大模型生成风险评估报告")
    box(ax, 7, 5.6, 4.1, 0.78, "rule_engine",
               "自动拒绝 / 自动通过 / 转人工 / 反洗钱合规")
    box(ax, 7, 4.1, 4.1, 0.78, "approval_route",
                "审批矩阵：风险等级 × 金额 × 合规 → 审批层级")
    box(ax, 3.2, 2.3, 2.9, 0.78, "final_decision",
                "最终决策，写入数据库", fill=FINAL_FILL, edge=FINAL_EDGE)
    box(ax, 10.3, 2.3, 3.0, 0.78, "human_approval",
                "人工审批（HITL interrupt）", fill=DECISION_FILL,
                edge=DECISION_EDGE)
    box(ax, 8.1, 0.5, 2.6, 0.72, "merge_opinion", "审批意见合并")
    box(ax, 12.4, 0.5, 2.4, 0.72, "escalate", "升级到更高级审批人")
    terminal(ax, 3.2, 0.5, "END")

    arrow(ax, (7, 9.68), (7, 9.0))
    arrow(ax, (7, 8.21), (7, 7.49))
    arrow(ax, (7, 6.71), (7, 5.99))
    arrow(ax, (7, 5.21), (7, 4.49))
    arrow(ax, (5.3, 4.1), (3.2, 2.69), "自动终局", (3.4, 3.5), rad=0.12)
    arrow(ax, (8.7, 4.1), (10.3, 2.69), "需人工", (10.6, 3.5), rad=-0.12)
    arrow(ax, (9.4, 1.91), (8.6, 0.9), "意见回流", (8.5, 1.5))
    arrow(ax, (11.3, 1.91), (12.1, 0.9), "升级", (12.4, 1.5))
    # escalate 升级后重新回到人工审批（重新中断）
    arrow(ax, (13.3, 0.9), (11.9, 1.91), "", None, rad=-0.4)
    # merge_opinion 意见合并后回流到 final_decision
    arrow(ax, (6.8, 0.5), (4.6, 2.0), "合并回流", (5.2, 1.05), rad=0.1)
    arrow(ax, (3.2, 1.91), (3.2, 0.74))

    ax.set_title("CreditGraph 信贷审批工作流", fontsize=13, fontweight="bold",
                 color=TEXT, pad=14)
    fig.savefig(OUT_DIR / "approval-flow.pdf", bbox_inches="tight",
                facecolor=BG)
    plt.close(fig)


def draw_approval_state() -> None:
    """审批状态图：与架构设计.md 原 ASCII 图一致的横向状态迁移。"""
    fig, ax = new_canvas(11, 5.4, (0, 18.2), (-1.6, 6.2))

    terminal(ax, 0.7, 4.6, "START")
    box(ax, 2.5, 4.6, 2.1, 0.72, "intake")
    box(ax, 5.3, 4.6, 2.4, 0.72, "risk_assessment")
    box(ax, 8.2, 4.6, 2.1, 0.72, "rule_engine")
    box(ax, 11.0, 4.6, 2.3, 0.72, "approval_route")
    box(ax, 14.3, 4.6, 2.3, 0.72, "final_decision",
                fill=FINAL_FILL, edge=FINAL_EDGE)
    terminal(ax, 16.85, 4.6, "END")

    box(ax, 12.9, 1.6, 2.6, 0.72, "human_approval",
                fill=DECISION_FILL, edge=DECISION_EDGE)
    box(ax, 9.3, 1.6, 2.4, 0.72, "merge_opinion")
    box(ax, 12.9, -0.9, 2.2, 0.7, "escalate")

    arrow(ax, (1.25, 4.6), (1.45, 4.6))
    arrow(ax, (3.55, 4.6), (4.1, 4.6))
    arrow(ax, (6.5, 4.6), (7.15, 4.6))
    arrow(ax, (9.25, 4.6), (9.85, 4.6))
    arrow(ax, (12.15, 4.6), (13.15, 4.6), "auto", (12.65, 5.0))
    arrow(ax, (15.45, 4.6), (16.3, 4.6))
    arrow(ax, (11.4, 4.24), (12.7, 1.96), "manual", (10.9, 3.2), rad=0.1)
    arrow(ax, (11.6, 1.6), (10.5, 1.6), "opinion", (11.05, 1.98))
    arrow(ax, (9.3, 1.96), (14.3, 4.24), "", None, rad=-0.15)
    arrow(ax, (13.9, 1.24), (13.9, -0.55), "escalate", (14.9, 0.35))
    # escalate 回环到 human_approval（重新中断）
    loop = FancyArrowPatch(
        (12.9, -0.55), (12.9, 1.24), arrowstyle="-|>", mutation_scale=14,
        color=ARROW, linewidth=1.3,
        connectionstyle="arc3,rad=0.55", shrinkA=2, shrinkB=2,
    )
    ax.add_patch(loop)

    ax.set_title("审批状态迁移（approval_route 按 auto / manual 分流）",
                 fontsize=12.5, fontweight="bold", color=TEXT, pad=12)
    fig.savefig(OUT_DIR / "approval-state.pdf", bbox_inches="tight",
                facecolor=BG)
    plt.close(fig)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    draw_approval_flow()
    draw_approval_state()
    print(f"已生成：{OUT_DIR / 'approval-flow.pdf'}")
    print(f"已生成：{OUT_DIR / 'approval-state.pdf'}")


if __name__ == "__main__":
    main()
