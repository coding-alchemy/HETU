"""免费准入边界：网络、缓存、导入三入口统一准入；任何路径不产生收费调用。"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from fixtures import make_case
from hetu_quant.data import AccessDenied, DataStore
from hetu_quant.models import DataRequest

CN = ZoneInfo("Asia/Shanghai")


class FakeProvider:
    """可控免费源替身：仅隔离网络；准入与存储使用真实实现。"""

    def __init__(self, failing: tuple[str, ...] = ()) -> None:
        self.failing = set(failing)
        self.calls: list[str] = []

    def fetch(
        self, product_id: str, codes: tuple[str, ...], start: date, end: date
    ) -> pd.DataFrame:
        self.calls.append(product_id)
        if product_id in self.failing:
            raise ConnectionError(f"fake source unavailable: {product_id}")
        return pd.DataFrame(
            {"date": [start.isoformat()], "code": [codes[0]], "close_raw": [10.0]}
        )


def _write_config(tmp_path: Path, variant: str = "base") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    config_path = tmp_path / "products.json"
    config_path.write_text(
        json.dumps(make_case("product_config", variant), ensure_ascii=False),
        encoding="utf-8",
    )
    return config_path


def _store(
    tmp_path: Path, variant: str = "base", provider: FakeProvider | None = None
) -> DataStore:
    providers = {"fake": provider} if provider is not None else {}
    return DataStore(
        tmp_path / "store", config_path=_write_config(tmp_path, variant), providers=providers
    )


def _request(
    product_ids: tuple[str, ...] = ("test_prices",),
    codes: tuple[str, ...] = ("000001.SZ",),
    mode: str = "free",
    purpose: str = "ranking_current",
) -> DataRequest:
    return DataRequest(
        product_ids=product_ids,
        codes=codes,
        start=date(2024, 1, 1),
        end=date(2024, 6, 30),
        as_of=datetime(2024, 7, 1, 9, 0, tzinfo=CN),
        purpose=purpose,
        mode=mode,
    )


def _write_import(tmp_path: Path, variant: str) -> tuple[Path, tuple[Path, ...]]:
    case = make_case("import_manifest", variant)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(case["manifest"], ensure_ascii=False), encoding="utf-8")
    files = []
    for name, content in case["files"].items():
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        files.append(path)
    return manifest_path, tuple(files)


def _paid_invocations(store: DataStore) -> list[dict]:
    return [item for item in store.invocations if item["fee_class"] != "free"]


def test_free_prepare_succeeds_and_logs_only_free(tmp_path: Path) -> None:
    store = _store(tmp_path, provider=FakeProvider())
    ref = store.prepare(_request())
    assert ref.snapshot_id
    assert store.invocations
    assert _paid_invocations(store) == []


def test_paid_mode_rejected_even_with_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HETU_QUANT_PAID_CREDENTIAL", "secret-token")
    store = _store(tmp_path, provider=FakeProvider())
    with pytest.raises(AccessDenied, match="paid"):
        store.prepare(_request(mode="paid"))
    with pytest.raises(AccessDenied, match="paid"):
        store.check_access(
            "paid", {"fee_class": "free", "permission": {"status": "verified_free"}}
        )
    assert _paid_invocations(store) == []


def test_unverified_permission_product_is_not_called(tmp_path: Path) -> None:
    provider = FakeProvider()
    store = _store(tmp_path, "with_unverified", provider)
    with pytest.raises(AccessDenied, match="permission"):
        store.prepare(_request(("test_unverified",)))
    assert provider.calls == []
    assert store.invocations == []


def test_paid_product_is_not_called(tmp_path: Path) -> None:
    provider = FakeProvider()
    store = _store(tmp_path, "with_paid", provider)
    with pytest.raises(AccessDenied, match="fee_class"):
        store.prepare(_request(("test_paid",)))
    assert provider.calls == []
    assert _paid_invocations(store) == []


def test_free_failure_does_not_fall_back_to_paid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HETU_QUANT_PAID_CREDENTIAL", "secret-token")
    provider = FakeProvider(failing=("test_prices",))
    store = _store(tmp_path, provider=FakeProvider())
    failing_store = _store(tmp_path / "second", provider=provider)
    with pytest.raises(ConnectionError, match="unavailable"):
        failing_store.prepare(_request())
    # 免费失败后的付费尝试仍被拒绝，且不产生任何收费调用
    with pytest.raises(AccessDenied, match="paid"):
        failing_store.prepare(_request(mode="paid"))
    assert _paid_invocations(failing_store) == []
    # 合法免费域不受其他域失败影响
    ref = store.prepare(_request())
    assert ref.snapshot_id


def test_paid_file_disguised_as_local_import_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest_path, files = _write_import(tmp_path, "paid_disguised")
    with pytest.raises(AccessDenied, match="fee_class"):
        store.import_files(manifest_path, files)
    assert _paid_invocations(store) == []
    assert list((tmp_path / "store" / "snapshots").iterdir()) == []


def test_import_without_fee_class_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest_path, files = _write_import(tmp_path, "unknown_fee")
    with pytest.raises(AccessDenied, match="fee_class"):
        store.import_files(manifest_path, files)


def test_import_without_permission_basis_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest_path, files = _write_import(tmp_path, "missing_basis")
    with pytest.raises(AccessDenied, match="permission_basis"):
        store.import_files(manifest_path, files)


def test_legal_free_import_preserves_source_attributes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest_path, files = _write_import(tmp_path, "base")
    ref = store.import_files(manifest_path, files)
    manifest = json.loads(
        (ref.root / "snapshots" / ref.snapshot_id / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    item = manifest["items"][0]
    assert item["source"]["name"] == "手工编制合成财务样例"
    assert item["source"]["fee_class"] == "free"
    assert manifest["mode"] == "free"
    assert _paid_invocations(store) == []


def test_financial_domain_failure_does_not_block_price_domain(tmp_path: Path) -> None:
    provider = FakeProvider(failing=("test_fin",))
    config = make_case("product_config", "base")
    config["products"]["test_fin"]["provider"] = "fake"
    config_path = tmp_path / "products.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    store = DataStore(tmp_path / "store", config_path=config_path, providers={"fake": provider})
    with pytest.raises(ConnectionError):
        store.prepare(_request(("test_fin",)))
    ref = store.prepare(_request(("test_prices",)))
    assert ref.snapshot_id
    assert _paid_invocations(store) == []
