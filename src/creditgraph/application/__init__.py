"""应用层：用例编排。

- `ports.py`           对外部世界的抽象（Protocol）。
- `material_analysis.py` 提交材料接收与解析。
- `credit_rules.py`    规则引擎编排（阈值来自配置）。
- `credit_service.py`  审批用例（提交 / 审批 / 升级 / 复审 / 审计链）。
- `state.py` / `routing.py` / `tools.py` / `graph.py` 信贷审批工作流的编排（LangGraph）。
"""
