import json
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from hetu_stock.helpers import app
from hetu_stock.helpers.authorization import (
    AuthorizationDecision,
    AuthorizationFinding,
    AuthorizationFindingCode,
    AuthorizationInput,
    AuthorizedSourceGrant,
    LicenseGrant,
    SourceRegistry,
    evaluate_authorization,
    evaluate_authorization_files,
)

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_FILE = FIXTURES / "data_sources.yaml"
REQUEST_FILE = FIXTURES / "authorization_request.json"
runner = CliRunner()

Mutation = Callable[
    [SourceRegistry, AuthorizationInput],
    tuple[SourceRegistry, AuthorizationInput],
]


def _load_models() -> tuple[SourceRegistry, AuthorizationInput]:
    import yaml

    registry = SourceRegistry.model_validate(
        yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))
    )
    request = AuthorizationInput.model_validate_json(
        REQUEST_FILE.read_text(encoding="utf-8")
    )
    return registry, request


def test_phase2_example_registry_matches_authorization_contract() -> None:
    import yaml

    payload = yaml.safe_load(
        (REPO_ROOT / "config/data_sources.example.yaml").read_text(
            encoding="utf-8"
        )
    )

    registry = SourceRegistry.model_validate(payload)

    assert set(registry.sources) == {"licensed-example"}


@pytest.fixture
def registry() -> SourceRegistry:
    return _load_models()[0]


@pytest.fixture
def valid_request() -> AuthorizationInput:
    return _load_models()[1]


def _source_update(
    registry: SourceRegistry,
    request: AuthorizationInput,
    **updates: object,
) -> tuple[SourceRegistry, AuthorizationInput]:
    source = registry.sources[request.source_id].model_copy(update=updates)
    return registry.model_copy(
        update={"sources": {**registry.sources, request.source_id: source}}
    ), request


def _request_update(
    registry: SourceRegistry,
    request: AuthorizationInput,
    **updates: object,
) -> tuple[SourceRegistry, AuthorizationInput]:
    return registry, request.model_copy(update=updates)


CASES: tuple[tuple[str, Mutation, tuple[str, ...]], ...] = (
    (
        "source missing",
        lambda registry, request: (
            registry.model_copy(update={"sources": {}}),
            request,
        ),
        ("SOURCE_MISSING",),
    ),
    (
        "source disabled",
        lambda registry, request: _source_update(
            registry, request, enabled=False
        ),
        ("SOURCE_DISABLED",),
    ),
    (
        "grant not yet valid",
        lambda registry, request: _request_update(
            registry,
            request,
            checked_at=registry.sources[request.source_id].valid_from
            - timedelta(microseconds=1),
        ),
        ("GRANT_NOT_YET_VALID",),
    ),
    (
        "grant expired",
        lambda registry, request: _request_update(
            registry,
            request,
            checked_at=registry.sources[request.source_id].valid_until
            + timedelta(microseconds=1),
        ),
        ("GRANT_EXPIRED",),
    ),
    (
        "dataset not granted",
        lambda registry, request: _source_update(
            registry, request, datasets=("market",)
        ),
        ("DATASET_NOT_GRANTED",),
    ),
    (
        "user source out of scope",
        lambda registry, request: _request_update(
            registry, request, user_sources=()
        ),
        ("USER_SOURCE_OUT_OF_SCOPE",),
    ),
    (
        "user dataset out of scope",
        lambda registry, request: _request_update(
            registry, request, user_datasets=()
        ),
        ("USER_DATASET_OUT_OF_SCOPE",),
    ),
    (
        "point in time not supported",
        lambda registry, request: _source_update(
            registry, request, point_in_time=False
        ),
        ("PIT_NOT_SUPPORTED",),
    ),
    (
        "secret reference mismatch",
        lambda registry, request: _source_update(
            registry, request, secret_ref="secret://hetu/test/another-fixture"
        ),
        ("SECRET_REF_MISMATCH",),
    ),
    (
        "secret unresolved",
        lambda registry, request: _request_update(
            registry, request, secret_resolved=False
        ),
        ("SECRET_UNRESOLVED",),
    ),
    (
        "purpose mismatch",
        lambda registry, request: _request_update(
            registry, request, purpose="risk_review"
        ),
        ("PURPOSE_MISMATCH",),
    ),
    (
        "license declaration mismatch",
        lambda registry, request: _request_update(
            registry,
            request,
            declared_license=request.declared_license.model_copy(
                update={"cache": "none"}
            ),
        ),
        ("LICENSE_DECLARATION_MISMATCH",),
    ),
    (
        "operation failed",
        lambda registry, request: _request_update(
            registry, request, operation_status="failed"
        ),
        ("OPERATION_FAILED",),
    ),
    (
        "operation rate limited",
        lambda registry, request: _request_update(
            registry, request, operation_status="rate-limited"
        ),
        ("OPERATION_RATE_LIMITED",),
    ),
)


def test_cases_exactly_cover_the_frozen_finding_codes() -> None:
    expected_codes = tuple(code for _, _, codes in CASES for code in codes)
    assert expected_codes == get_args(AuthorizationFindingCode)


@pytest.mark.parametrize(("name", "mutate", "expected_codes"), CASES)
def test_each_authorization_dimension_returns_complete_ordered_findings(
    name: str,
    mutate: Mutation,
    expected_codes: tuple[str, ...],
    registry: SourceRegistry,
    valid_request: AuthorizationInput,
) -> None:
    del name
    changed_registry, changed_request = mutate(registry, valid_request)

    decision = evaluate_authorization(changed_registry, changed_request)

    assert tuple(finding.code for finding in decision.findings) == expected_codes
    assert decision.allowed is False
    assert decision.mode == "authorized"
    assert decision.source_id == changed_request.source_id
    assert decision.dataset == changed_request.dataset
    assert decision.secret_ref == changed_request.secret_ref
    assert not hasattr(decision, "workflow_status")
    assert not hasattr(decision, "next_action")


def test_all_findings_are_returned_in_frozen_code_order(
    registry: SourceRegistry,
    valid_request: AuthorizationInput,
) -> None:
    source = registry.sources[valid_request.source_id].model_copy(
        update={
            "enabled": False,
            "datasets": (),
            "point_in_time": False,
            "secret_ref": "secret://hetu/test/another-fixture",
        }
    )
    changed_registry = registry.model_copy(
        update={"sources": {valid_request.source_id: source}}
    )
    changed_request = valid_request.model_copy(
        update={
            "checked_at": source.valid_until + timedelta(seconds=1),
            "user_sources": (),
            "user_datasets": (),
            "secret_resolved": False,
            "purpose": "risk_review",
            "declared_license": valid_request.declared_license.model_copy(
                update={"display": "none"}
            ),
            "operation_status": "failed",
        }
    )

    decision = evaluate_authorization(changed_registry, changed_request)

    assert tuple(finding.code for finding in decision.findings) == (
        "SOURCE_DISABLED",
        "GRANT_EXPIRED",
        "DATASET_NOT_GRANTED",
        "USER_SOURCE_OUT_OF_SCOPE",
        "USER_DATASET_OUT_OF_SCOPE",
        "PIT_NOT_SUPPORTED",
        "SECRET_REF_MISMATCH",
        "SECRET_UNRESOLVED",
        "PURPOSE_MISMATCH",
        "LICENSE_DECLARATION_MISMATCH",
        "OPERATION_FAILED",
    )


@pytest.mark.parametrize("operation_status", ["failed", "rate-limited"])
def test_reliable_recovery_suppresses_only_the_operation_finding(
    operation_status: str,
    registry: SourceRegistry,
    valid_request: AuthorizationInput,
) -> None:
    request = valid_request.model_copy(
        update={
            "operation_status": operation_status,
            "reliable_recovery_available": True,
            "secret_resolved": False,
        }
    )

    decision = evaluate_authorization(registry, request)

    assert tuple(finding.code for finding in decision.findings) == (
        "SECRET_UNRESOLVED",
    )
    dumped = decision.model_dump()
    assert "retry" not in json.dumps(dumped).lower()
    assert set(dumped) == {
        "allowed",
        "mode",
        "source_id",
        "dataset",
        "secret_ref",
        "findings",
    }


@pytest.mark.parametrize("operation_status", ["failed", "rate-limited"])
def test_reliable_recovery_can_leave_an_otherwise_valid_check_allowed(
    operation_status: str,
    registry: SourceRegistry,
    valid_request: AuthorizationInput,
) -> None:
    request = valid_request.model_copy(
        update={
            "operation_status": operation_status,
            "reliable_recovery_available": True,
        }
    )

    decision = evaluate_authorization(registry, request)

    assert decision.allowed is True
    assert decision.findings == ()


def test_as_of_does_not_control_expired_grant_currentness(
    registry: SourceRegistry,
    valid_request: AuthorizationInput,
) -> None:
    source = registry.sources[valid_request.source_id]
    expired = valid_request.model_copy(
        update={"checked_at": source.valid_until + timedelta(seconds=1)}
    )

    first = evaluate_authorization(
        registry,
        expired.model_copy(update={"as_of": source.valid_from}),
    )
    second = evaluate_authorization(
        registry,
        expired.model_copy(update={"as_of": source.valid_until}),
    )

    assert tuple(finding.code for finding in first.findings) == ("GRANT_EXPIRED",)
    assert first == second


@pytest.mark.parametrize(
    ("offset", "expected_codes"),
    [
        (timedelta(microseconds=-1), ("GRANT_NOT_YET_VALID",)),
        (timedelta(0), ()),
    ],
)
def test_checked_at_controls_valid_from_boundary(
    offset: timedelta,
    expected_codes: tuple[str, ...],
    registry: SourceRegistry,
    valid_request: AuthorizationInput,
) -> None:
    source = registry.sources[valid_request.source_id]
    request = valid_request.model_copy(update={"checked_at": source.valid_from + offset})
    decision = evaluate_authorization(registry, request)
    assert tuple(finding.code for finding in decision.findings) == expected_codes


@pytest.mark.parametrize(
    ("offset", "expected_codes"),
    [
        (timedelta(0), ()),
        (timedelta(microseconds=1), ("GRANT_EXPIRED",)),
    ],
)
def test_checked_at_controls_valid_until_boundary(
    offset: timedelta,
    expected_codes: tuple[str, ...],
    registry: SourceRegistry,
    valid_request: AuthorizationInput,
) -> None:
    source = registry.sources[valid_request.source_id]
    request = valid_request.model_copy(update={"checked_at": source.valid_until + offset})
    decision = evaluate_authorization(registry, request)
    assert tuple(finding.code for finding in decision.findings) == expected_codes


def test_validity_comparison_normalizes_offsets_to_utc(
    registry: SourceRegistry,
    valid_request: AuthorizationInput,
) -> None:
    request = valid_request.model_copy(
        update={"checked_at": datetime.fromisoformat("2025-12-31T16:00:00+00:00")}
    )
    assert evaluate_authorization(registry, request).allowed is True


@pytest.mark.parametrize("field", ["as_of", "checked_at"])
def test_request_rejects_naive_decision_datetimes(field: str) -> None:
    payload = json.loads(REQUEST_FILE.read_text(encoding="utf-8"))
    payload[field] = "2026-08-01T12:00:00"
    with pytest.raises(ValidationError, match=f"{field} must be timezone-aware"):
        AuthorizationInput.model_validate(payload)


def test_source_rejects_reversed_validity_interval() -> None:
    import yaml

    payload = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))
    source = payload["sources"]["licensed-fixture"]
    source["valid_from"], source["valid_until"] = (
        source["valid_until"],
        source["valid_from"],
    )
    with pytest.raises(ValidationError, match="valid_from must be at or before valid_until"):
        SourceRegistry.model_validate(payload)


@pytest.mark.parametrize("field", ["source_id", "dataset", "purpose"])
@pytest.mark.parametrize("blank", ["", "   "])
def test_request_rejects_blank_identifiers_and_purpose(field: str, blank: str) -> None:
    payload = json.loads(REQUEST_FILE.read_text(encoding="utf-8"))
    payload[field] = blank
    with pytest.raises(ValidationError, match=f"{field} must be nonblank"):
        AuthorizationInput.model_validate(payload)


@pytest.mark.parametrize(
    "model,payload",
    [
        (
            AuthorizationInput,
            {
                **json.loads(REQUEST_FILE.read_text(encoding="utf-8")),
                "secret_ref": "plaintext-value",
            },
        ),
        (
            AuthorizedSourceGrant,
            {
                "enabled": True,
                "adapter": "fixture",
                "secret_ref": "secret://   ",
                "datasets": ["financials"],
                "point_in_time": True,
                "valid_from": "2026-01-01T00:00:00+08:00",
                "valid_until": "2026-12-31T23:59:59+08:00",
                "license": {
                    "purpose": "internal_research",
                    "cache": "derived_only",
                    "display": "internal_users",
                    "redistribution": "forbidden",
                },
            },
        ),
    ],
)
def test_secret_references_require_a_nonblank_secret_path(
    model: type[AuthorizationInput] | type[AuthorizedSourceGrant],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="secret_ref"):
        model.model_validate(payload)


@pytest.mark.parametrize(
    "model,payload",
    [
        (
            LicenseGrant,
            {
                "purpose": "internal_research",
                "cache": "derived_only",
                "display": "internal_users",
                "redistribution": "forbidden",
                "unknown": True,
            },
        ),
        (
            SourceRegistry,
            {"sources": {}, "unknown": True},
        ),
        (
            AuthorizationInput,
            {
                **json.loads(REQUEST_FILE.read_text(encoding="utf-8")),
                "unknown": True,
            },
        ),
        (
            AuthorizationFinding,
            {
                "code": "SOURCE_MISSING",
                "message": "missing",
                "meaningful_after_external_repair": True,
                "unknown": True,
            },
        ),
        (
            AuthorizationDecision,
            {
                "allowed": False,
                "source_id": "licensed-fixture",
                "dataset": "financials",
                "secret_ref": "secret://hetu/test/licensed-fixture",
                "findings": [],
                "unknown": True,
            },
        ),
    ],
)
def test_models_forbid_unknown_fields(
    model: type[Any],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    "model",
    [
        LicenseGrant,
        AuthorizedSourceGrant,
        SourceRegistry,
        AuthorizationInput,
        AuthorizationFinding,
        AuthorizationDecision,
    ],
)
def test_models_are_frozen_and_forbid_extra_fields(model: type[Any]) -> None:
    assert model.model_config["frozen"] is True
    assert model.model_config["extra"] == "forbid"


@pytest.mark.parametrize(
    ("bad_file", "contents", "registry_file", "request_file"),
    [
        ("registry.yaml", "sources: [", "bad", "valid"),
        ("request.json", "{", "valid", "bad"),
    ],
)
def test_file_adapter_rejects_malformed_yaml_and_json(
    tmp_path: Path,
    bad_file: str,
    contents: str,
    registry_file: str,
    request_file: str,
) -> None:
    malformed = tmp_path / bad_file
    malformed.write_text(contents, encoding="utf-8")
    selected_registry = REGISTRY_FILE if registry_file == "valid" else malformed
    selected_request = REQUEST_FILE if request_file == "valid" else malformed
    with pytest.raises(ValueError):
        evaluate_authorization_files(
            registry=selected_registry,
            request=selected_request,
        )


def _write_canary_input(
    *,
    tmp_path: Path,
    input_case: str,
    canary: str,
) -> tuple[Path, Path, str]:
    import yaml

    registry = REGISTRY_FILE
    request = REQUEST_FILE
    if input_case == "request-validation":
        payload = json.loads(REQUEST_FILE.read_text(encoding="utf-8"))
        payload["secret_value"] = canary
        request = tmp_path / "request.json"
        request.write_text(json.dumps(payload), encoding="utf-8")
        expected_message = "authorization request validation input is invalid"
    elif input_case == "request-parser":
        request = tmp_path / "request.json"
        request.write_text(f'{{"secret_value":"{canary}"', encoding="utf-8")
        expected_message = "authorization request parse input is invalid"
    elif input_case == "registry-validation":
        payload = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))
        payload["sources"]["licensed-fixture"]["secret_value"] = canary
        registry = tmp_path / "registry.yaml"
        registry.write_text(yaml.safe_dump(payload), encoding="utf-8")
        expected_message = "authorization registry validation input is invalid"
    elif input_case == "registry-parser":
        registry = tmp_path / "registry.yaml"
        registry.write_text(f"sources: [{canary}\n", encoding="utf-8")
        expected_message = "authorization registry parse input is invalid"
    else:
        raise AssertionError(f"unsupported input case: {input_case}")
    return registry, request, expected_message


def _exception_observable_values(exception: BaseException) -> tuple[str, ...]:
    values: list[str] = []
    pending: list[BaseException] = [exception]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        values.extend((str(current), repr(current), repr(vars(current))))
        if isinstance(current, ValidationError):
            values.append(
                repr(
                    current.errors(
                        include_url=False,
                        include_context=True,
                        include_input=True,
                    )
                )
            )
            values.append(
                current.json(
                    include_url=False,
                    include_context=True,
                    include_input=True,
                )
            )
        if isinstance(current, UnicodeError) and hasattr(current, "object"):
            values.append(repr(current.object))
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return tuple(values)


def _helper_traceback_values(exception: BaseException) -> tuple[str, ...]:
    values: list[str] = []
    pending: list[BaseException] = [exception]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        traceback = current.__traceback__
        while traceback is not None:
            filename = traceback.tb_frame.f_code.co_filename
            if "/src/hetu_stock/helpers/" in filename:
                values.append(repr(traceback.tb_frame.f_locals))
            traceback = traceback.tb_next
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return tuple(values)


@pytest.mark.parametrize(
    "input_case",
    [
        "request-validation",
        "request-parser",
        "registry-validation",
        "registry-parser",
    ],
)
def test_file_adapter_normalizes_invalid_input_without_retaining_raw_values(
    tmp_path: Path,
    input_case: str,
) -> None:
    canary = secrets.token_urlsafe(32)
    registry, request, expected_message = _write_canary_input(
        tmp_path=tmp_path,
        input_case=input_case,
        canary=canary,
    )

    with pytest.raises(ValueError) as caught:
        evaluate_authorization_files(registry=registry, request=request)

    error = caught.value
    assert type(error) is ValueError
    assert str(error) == expected_message
    assert error.__cause__ is None
    assert error.__context__ is None
    assert all(canary not in value for value in _exception_observable_values(error))


@pytest.mark.parametrize(
    "input_case",
    [
        "request-validation",
        "request-parser",
        "registry-validation",
        "registry-parser",
    ],
)
def test_authorization_cli_does_not_expose_invalid_input_or_exception_payloads(
    tmp_path: Path,
    input_case: str,
) -> None:
    canary = secrets.token_urlsafe(32)
    registry, request, expected_message = _write_canary_input(
        tmp_path=tmp_path,
        input_case=input_case,
        canary=canary,
    )

    result = runner.invoke(
        app,
        [
            "authorization-check",
            "--registry",
            str(registry),
            "--request",
            str(request),
        ],
    )

    assert result.exit_code == 1
    assert result.stderr == f"authorization-check failed: {expected_message}\n"
    assert result.exception is not None
    visible_values = (
        result.stdout,
        result.stderr,
        result.output,
        repr(result.exc_info),
        *_exception_observable_values(result.exception),
    )
    assert all(canary not in value for value in visible_values)


def test_secret_value_is_rejected_without_echo(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    canary = secrets.token_urlsafe(32)
    payload = json.loads(REQUEST_FILE.read_text(encoding="utf-8"))
    payload["secret_value"] = canary

    with pytest.raises(ValidationError) as caught:
        AuthorizationInput.model_validate(payload)

    validation_error = caught.value
    line_errors = validation_error.errors(
        include_url=False,
        include_context=True,
        include_input=True,
    )
    assert validation_error.title == "AuthorizationInput"
    assert line_errors == [
        {
            "type": "extra_forbidden",
            "loc": ("<redacted>",),
            "msg": "Extra inputs are not permitted",
            "input": None,
        }
    ]
    assert validation_error.__cause__ is None
    assert validation_error.__context__ is None
    assert all(
        canary not in value
        for value in (
            *_exception_observable_values(validation_error),
            *_helper_traceback_values(validation_error),
        )
    )
    request = tmp_path / "request.json"
    request.write_text(json.dumps(payload), encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "authorization-check",
            "--registry",
            str(REGISTRY_FILE),
            "--request",
            str(request),
        ],
    )
    assert result.exit_code == 1
    assert result.exception is not None
    visible_values = (
        result.stdout,
        result.stderr,
        result.output,
        repr(result.exc_info),
        caplog.text,
        *_exception_observable_values(result.exception),
    )
    assert all(canary not in value for value in visible_values)

    valid = AuthorizationInput.model_validate(
        json.loads(REQUEST_FILE.read_text(encoding="utf-8"))
    )
    registry, _ = _load_models()
    decision = evaluate_authorization(registry, valid)
    request_dump = valid.model_dump(mode="json")
    decision_dump = decision.model_dump(mode="json")
    assert set(request_dump) == {
        "source_id",
        "dataset",
        "purpose",
        "as_of",
        "checked_at",
        "require_point_in_time",
        "user_sources",
        "user_datasets",
        "secret_ref",
        "secret_resolved",
        "declared_license",
        "operation_status",
        "reliable_recovery_available",
    }
    assert set(decision_dump) == {
        "allowed",
        "mode",
        "source_id",
        "dataset",
        "secret_ref",
        "findings",
    }
    expected_secret_ref = "secret://hetu/test/licensed-fixture"
    assert type(request_dump["secret_ref"]) is str
    assert request_dump["secret_ref"] == expected_secret_ref
    assert request_dump["secret_resolved"] is True
    assert type(decision_dump["secret_ref"]) is str
    assert decision_dump["secret_ref"] == expected_secret_ref
    assert "secret_value" not in request_dump
    assert "secret_value" not in decision_dump
    assert canary not in json.dumps(request_dump, sort_keys=True)
    assert canary not in json.dumps(decision_dump, sort_keys=True)


def test_malformed_secret_reference_is_not_retained_by_public_validation() -> None:
    canary = secrets.token_urlsafe(32)
    payload = json.loads(REQUEST_FILE.read_text(encoding="utf-8"))
    payload["secret_ref"] = canary

    with pytest.raises(ValidationError, match="secret_ref must use secret://") as caught:
        AuthorizationInput.model_validate(payload)

    error = caught.value
    assert error.__cause__ is None
    assert error.__context__ is None
    assert all(
        canary not in value
        for value in (
            *_exception_observable_values(error),
            *_helper_traceback_values(error),
        )
    )


@pytest.mark.parametrize(
    "method_name",
    ["model_validate", "model_validate_json", "model_validate_strings"],
)
def test_public_validation_methods_remove_rejected_input(method_name: str) -> None:
    canary = secrets.token_urlsafe(32)
    payload = {
        "purpose": "internal_research",
        "cache": "derived_only",
        "display": "internal_users",
        "redistribution": "forbidden",
        "secret_value": canary,
    }
    argument: object = json.dumps(payload) if method_name == "model_validate_json" else payload

    with pytest.raises(ValidationError) as caught:
        getattr(LicenseGrant, method_name)(argument)

    error = caught.value
    assert error.title == "LicenseGrant"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert all(canary not in value for value in _exception_observable_values(error))


@pytest.mark.parametrize(
    "method_name",
    ["model_validate", "model_validate_json", "model_validate_strings"],
)
def test_public_validation_traceback_does_not_retain_context(
    method_name: str,
) -> None:
    canary = secrets.token_urlsafe(32)
    payload = {
        "purpose": "internal_research",
        "cache": "derived_only",
        "display": "internal_users",
        "redistribution": "forbidden",
        "unknown": True,
    }
    argument: object = json.dumps(payload) if method_name == "model_validate_json" else payload

    with pytest.raises(ValidationError) as caught:
        getattr(LicenseGrant, method_name)(
            argument,
            context={"caller_private_data": canary},
        )

    assert all(
        canary not in value
        for value in (
            *_exception_observable_values(caught.value),
            *_helper_traceback_values(caught.value),
        )
    )


class _RaisingPurpose:
    cache = "derived_only"
    display = "internal_users"
    redistribution = "forbidden"

    def __init__(self, canary: str) -> None:
        self._canary = canary

    @property
    def purpose(self) -> str:
        raise RuntimeError(self._canary)


def test_attribute_validation_error_context_does_not_retain_exception_text() -> None:
    canary = secrets.token_urlsafe(32)

    with pytest.raises(ValidationError) as caught:
        LicenseGrant.model_validate(
            _RaisingPurpose(canary),
            from_attributes=True,
        )

    error = caught.value
    assert error.errors(
        include_url=False,
        include_context=True,
        include_input=True,
    ) == [
        {
            "type": "get_attribute_error",
            "loc": ("purpose",),
            "msg": "Error extracting attribute: attribute access failed",
            "input": None,
            "ctx": {"error": "attribute access failed"},
        }
    ]
    assert error.__cause__ is None
    assert error.__context__ is None
    assert all(
        canary not in value
        for value in (
            *_exception_observable_values(error),
            *_helper_traceback_values(error),
        )
    )


@pytest.mark.parametrize(
    "method_name",
    ["model_validate", "model_validate_json", "model_validate_strings"],
)
def test_public_validation_methods_cannot_override_extra_forbid(
    method_name: str,
) -> None:
    canary = secrets.token_urlsafe(32)
    payload = {
        "purpose": "internal_research",
        "cache": "derived_only",
        "display": "internal_users",
        "redistribution": "forbidden",
        "secret_value": canary,
    }
    argument: object = json.dumps(payload) if method_name == "model_validate_json" else payload

    with pytest.raises(ValidationError) as caught:
        getattr(LicenseGrant, method_name)(argument, extra="allow")

    error = caught.value
    assert error.errors(
        include_url=False,
        include_context=True,
        include_input=True,
    ) == [
        {
            "type": "extra_forbidden",
            "loc": ("<redacted>",),
            "msg": "Extra inputs are not permitted",
            "input": None,
        }
    ]
    assert error.__cause__ is None
    assert error.__context__ is None
    assert all(
        canary not in value
        for value in (
            *_exception_observable_values(error),
            *_helper_traceback_values(error),
        )
    )


def test_direct_constructor_sanitizes_forbidden_secret_value() -> None:
    canary = secrets.token_urlsafe(32)
    valid_payload = json.loads(REQUEST_FILE.read_text(encoding="utf-8"))
    payload = {**valid_payload, "secret_value": canary}

    with pytest.raises(ValidationError) as caught:
        AuthorizationInput(**payload)

    error = caught.value
    assert error.errors(
        include_url=False,
        include_context=True,
        include_input=True,
    ) == [
        {
            "type": "extra_forbidden",
            "loc": ("<redacted>",),
            "msg": "Extra inputs are not permitted",
            "input": None,
        }
    ]
    assert error.__cause__ is None
    assert error.__context__ is None
    assert all(
        canary not in value
        for value in (
            *_exception_observable_values(error),
            *_helper_traceback_values(error),
        )
    )

    assert AuthorizationInput(**valid_payload) == AuthorizationInput.model_validate(
        valid_payload
    )


def test_unknown_field_name_is_redacted_from_validation_location() -> None:
    canary = f"RAW-SECRET-{secrets.token_urlsafe(32)}"
    payload = json.loads(REQUEST_FILE.read_text(encoding="utf-8"))
    payload[canary] = True

    with pytest.raises(ValidationError) as caught:
        AuthorizationInput.model_validate(payload)

    error = caught.value
    assert error.errors(
        include_url=False,
        include_context=True,
        include_input=True,
    ) == [
        {
            "type": "extra_forbidden",
            "loc": ("<redacted>",),
            "msg": "Extra inputs are not permitted",
            "input": None,
        }
    ]
    assert all(canary not in value for value in _exception_observable_values(error))


def test_registry_source_key_is_redacted_from_nested_validation_location() -> None:
    import yaml

    canary = f"RAW-SECRET-{secrets.token_urlsafe(32)}"
    payload = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))
    source = payload["sources"].pop("licensed-fixture")
    source["enabled"] = "not-a-boolean"
    payload["sources"][canary] = source

    with pytest.raises(ValidationError) as caught:
        SourceRegistry.model_validate(payload)

    error = caught.value
    assert error.errors(
        include_url=False,
        include_context=True,
        include_input=True,
    )[0]["loc"] == ("sources", "<redacted>", "enabled")
    assert all(canary not in value for value in _exception_observable_values(error))


def test_numeric_registry_source_key_is_redacted_from_validation_location() -> None:
    import yaml

    canary = 987654321012345678
    payload = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))
    source = payload["sources"].pop("licensed-fixture")
    source["enabled"] = "not-a-boolean"
    payload["sources"][canary] = source

    with pytest.raises(ValidationError) as caught:
        SourceRegistry.model_validate(payload)

    error = caught.value
    assert all(
        location[:2] == ("sources", "<redacted>")
        for location in (
            line_error["loc"]
            for line_error in error.errors(
                include_url=False,
                include_context=True,
                include_input=True,
            )
        )
    )
    canary_text = str(canary)
    assert all(
        canary_text not in value
        for value in (
            *_exception_observable_values(error),
            *_helper_traceback_values(error),
        )
    )


@pytest.mark.parametrize(
    ("model_kind", "field", "value"),
    [
        ("request", "as_of", "0001-01-01T00:00:00+14:00"),
        ("request", "as_of", "9999-12-31T23:59:59-14:00"),
        ("request", "checked_at", "0001-01-01T00:00:00+14:00"),
        ("request", "checked_at", "9999-12-31T23:59:59-14:00"),
        ("registry", "valid_from", "0001-01-01T00:00:00+14:00"),
        ("registry", "valid_until", "9999-12-31T23:59:59-14:00"),
    ],
)
def test_authorization_models_reject_timezone_conversion_overflow(
    model_kind: str,
    field: str,
    value: str,
) -> None:
    if model_kind == "request":
        payload = json.loads(REQUEST_FILE.read_text(encoding="utf-8"))
        payload[field] = value
        model: type[AuthorizationInput] | type[SourceRegistry] = AuthorizationInput
    else:
        import yaml

        payload = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))
        payload["sources"]["licensed-fixture"][field] = value
        model = SourceRegistry

    with pytest.raises(
        ValidationError,
        match=rf"{field} is outside the supported timezone conversion range",
    ):
        model.model_validate(payload)


def test_every_public_authorization_model_uses_sanitized_validation() -> None:
    registry, request = _load_models()
    decision = evaluate_authorization(registry, request)
    finding = AuthorizationFinding(
        code="SOURCE_MISSING",
        message="The requested source is not present.",
        meaningful_after_external_repair=True,
    )
    model_payloads: tuple[tuple[type[Any], dict[str, object]], ...] = (
        (LicenseGrant, request.declared_license.model_dump(mode="json")),
        (
            AuthorizedSourceGrant,
            registry.sources[request.source_id].model_dump(mode="json"),
        ),
        (SourceRegistry, registry.model_dump(mode="json")),
        (AuthorizationInput, request.model_dump(mode="json")),
        (AuthorizationFinding, finding.model_dump(mode="json")),
        (AuthorizationDecision, decision.model_dump(mode="json")),
    )

    for model, valid_payload in model_payloads:
        canary = secrets.token_urlsafe(32)
        payload = {**valid_payload, "secret_value": canary}
        with pytest.raises(ValidationError) as caught:
            model.model_validate(payload)
        assert caught.value.title == model.__name__
        assert all(
            canary not in value
            for value in _exception_observable_values(caught.value)
        )


def _write_invalid_utf8(
    *,
    tmp_path: Path,
    input_kind: str,
    raw_payload: bytes,
) -> tuple[Path, Path, str]:
    if input_kind == "registry":
        registry = tmp_path / "registry.yaml"
        registry.write_bytes(raw_payload)
        return registry, REQUEST_FILE, "authorization registry parse input is invalid"
    if input_kind == "request":
        request = tmp_path / "request.json"
        request.write_bytes(raw_payload)
        return REGISTRY_FILE, request, "authorization request parse input is invalid"
    raise AssertionError(f"unsupported input kind: {input_kind}")


@pytest.mark.parametrize("input_kind", ["registry", "request"])
def test_file_adapter_normalizes_invalid_utf8_without_retaining_bytes(
    tmp_path: Path,
    input_kind: str,
) -> None:
    canary = f"BINARY-{secrets.token_urlsafe(32)}"
    raw_payload = canary.encode() + b"\xff"
    registry, request, expected_message = _write_invalid_utf8(
        tmp_path=tmp_path,
        input_kind=input_kind,
        raw_payload=raw_payload,
    )

    with pytest.raises(ValueError) as caught:
        evaluate_authorization_files(registry=registry, request=request)

    error = caught.value
    assert type(error) is ValueError
    assert str(error) == expected_message
    assert error.__cause__ is None
    assert error.__context__ is None
    observable_values = (
        *_exception_observable_values(error),
        *_helper_traceback_values(error),
    )
    assert all(canary not in value for value in observable_values)
    assert all(repr(raw_payload) not in value for value in observable_values)


@pytest.mark.parametrize("input_kind", ["registry", "request"])
def test_authorization_cli_hides_invalid_utf8_and_exception_objects(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    input_kind: str,
) -> None:
    canary = f"BINARY-{secrets.token_urlsafe(32)}"
    raw_payload = canary.encode() + b"\xff"
    registry, request, expected_message = _write_invalid_utf8(
        tmp_path=tmp_path,
        input_kind=input_kind,
        raw_payload=raw_payload,
    )

    result = runner.invoke(
        app,
        [
            "authorization-check",
            "--registry",
            str(registry),
            "--request",
            str(request),
        ],
    )

    assert result.exit_code == 1
    assert result.stderr == f"authorization-check failed: {expected_message}\n"
    assert result.exception is not None
    observable_values = (
        result.stdout,
        result.stderr,
        result.output,
        repr(result.exc_info),
        caplog.text,
        *_exception_observable_values(result.exception),
        *_helper_traceback_values(result.exception),
    )
    assert all(canary not in value for value in observable_values)
    assert all(repr(raw_payload) not in value for value in observable_values)


def test_fixture_output_is_allowed_stable_and_secret_value_free() -> None:
    first = evaluate_authorization_files(registry=REGISTRY_FILE, request=REQUEST_FILE)
    second = evaluate_authorization_files(registry=REGISTRY_FILE, request=REQUEST_FILE)

    assert first == second
    assert json.loads(first) == {
        "allowed": True,
        "mode": "authorized",
        "source_id": "licensed-fixture",
        "dataset": "financials",
        "secret_ref": "secret://hetu/test/licensed-fixture",
        "findings": [],
    }
    assert "secret_resolved" not in first
    assert "canary" not in first.lower()
