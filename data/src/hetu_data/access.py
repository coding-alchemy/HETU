"""产品及操作许可；不以凭据、付费模式或本地文件名替代授权。"""

from collections.abc import Mapping
from datetime import date, datetime
from zoneinfo import ZoneInfo


class AccessDenied(PermissionError):
    """来源、用途或期间未获准。"""


def check_product_access(
    mode: str, product: Mapping[str, object], grant: Mapping[str, object] | None, *, operation: str
) -> None:
    if mode not in {"free", "paid"} or operation not in {"acquire", "import", "read", "backup"}:
        raise AccessDenied("unknown mode or operation")
    fee = product.get("fee_class")
    if fee == "free":
        permission = product.get("permission") or {}
        if permission.get("status") not in {"verified_free", "declared_free"}:
            raise AccessDenied("free permission not established")
        if operation == "backup" and operation not in permission.get("allowed_operations", ()):
            raise AccessDenied("backup permission missing")
        return
    if fee != "paid" or mode != "paid":
        raise AccessDenied("paid product is not allowed in free mode or fee_class is unknown")
    if (
        not grant
        or grant.get("product_id") != product.get("product_id")
        or not grant.get("approved_scope_ref")
        or not grant.get("license_ref")
        or operation not in grant.get("allowed_operations", [])
    ):
        raise AccessDenied("product or operation grant missing")
    until = grant.get("acquire_until")
    if operation == "read" and grant.get("retained_use") is True:
        return
    if not until or datetime.now(ZoneInfo("Asia/Shanghai")).date() > date.fromisoformat(str(until)):
        raise AccessDenied("current-use permission ended or validity unknown; retained use is not established")
