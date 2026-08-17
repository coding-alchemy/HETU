from datetime import timedelta
from pathlib import Path

import pytest

from hetu_stock.helpers.authorization import (
    AuthorizationInput,
    SourceRegistry,
    evaluate_authorization,
)

FIXTURES = Path(__file__).parent / "fixtures"
REGISTRY_FILE = FIXTURES / "data_sources.yaml"
REQUEST_FILE = FIXTURES / "authorization_request.json"


@pytest.fixture
def registry() -> SourceRegistry:
    import yaml

    return SourceRegistry.model_validate(
        yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))
    )


@pytest.fixture
def valid_request() -> AuthorizationInput:
    return AuthorizationInput.model_validate_json(
        REQUEST_FILE.read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    "dimension",
    ["secret", "user-dataset", "expired-grant"],
)
def test_repair_requires_a_new_explicit_check(
    registry: SourceRegistry,
    valid_request: AuthorizationInput,
    dimension: str,
) -> None:
    denied_registry = registry
    denied_request = valid_request
    repaired_registry = registry

    if dimension == "secret":
        denied_request = valid_request.model_copy(update={"secret_resolved": False})
        repaired_request = denied_request.model_copy(update={"secret_resolved": True})
    elif dimension == "user-dataset":
        denied_request = valid_request.model_copy(update={"user_datasets": ()})
        repaired_request = denied_request.model_copy(
            update={"user_datasets": (valid_request.dataset,)}
        )
    else:
        source = registry.sources[valid_request.source_id]
        denied_request = valid_request.model_copy(
            update={"checked_at": source.valid_until + timedelta(seconds=1)}
        )
        repaired_source = source.model_copy(
            update={"valid_until": denied_request.checked_at + timedelta(days=1)}
        )
        repaired_registry = registry.model_copy(
            update={
                "sources": {
                    **registry.sources,
                    valid_request.source_id: repaired_source,
                }
            }
        )
        repaired_request = denied_request

    first = evaluate_authorization(denied_registry, denied_request)
    first_snapshot = first.model_dump()
    second = evaluate_authorization(repaired_registry, repaired_request)

    assert first.allowed is False
    assert second.allowed is True
    assert first.model_dump() == first_snapshot
    assert first.model_dump() != second.model_dump()
    assert evaluate_authorization(denied_registry, denied_request) == first


def test_one_denied_dataset_does_not_decide_other_data(
    registry: SourceRegistry,
    valid_request: AuthorizationInput,
) -> None:
    denied = evaluate_authorization(
        registry,
        valid_request.model_copy(update={"dataset": "unlicensed"}),
    )
    unaffected = evaluate_authorization(registry, valid_request)

    assert denied.allowed is False
    assert denied.dataset == "unlicensed"
    assert unaffected.allowed is True
    assert unaffected.dataset == valid_request.dataset
    assert not hasattr(denied, "workflow_status")
    assert not hasattr(denied, "next_action")
