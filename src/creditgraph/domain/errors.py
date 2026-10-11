"""领域异常：表达"业务规则不允许"，与 HTTP/DB 无关。"""

from __future__ import annotations


class DomainError(ValueError):
    """领域层错误基类。"""


class CreditError(DomainError):
    """信贷审批相关的业务错误（如金额非法、状态不允许该操作、找不到申请）。"""
