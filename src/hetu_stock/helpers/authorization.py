import json
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self, cast

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic.config import ExtraValues

AuthorizationFindingCode = Literal[
    "SOURCE_MISSING",
    "SOURCE_DISABLED",
    "GRANT_NOT_YET_VALID",
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
    "OPERATION_RATE_LIMITED",
]


def _nonblank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must be nonblank")
    return value


def _secret_reference(value: str) -> str:
    prefix = "secret://"
    if not value.startswith(prefix) or not value.removeprefix(prefix).strip():
        raise ValueError("secret_ref must use secret:// followed by a nonblank path")
    return value


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    _to_utc(value, field_name)
    return value


def _to_utc(value: datetime, field_name: str) -> datetime:
    converted: datetime | None = None
    with suppress(OverflowError, ValueError):
        converted = value.astimezone(UTC)
    if converted is None:
        raise ValueError(
            f"{field_name} is outside the supported timezone conversion range"
        )
    return converted


_SAFE_VALUE_ERROR_MESSAGES = frozenset(
    {
        "as_of must be timezone-aware",
        "as_of is outside the supported timezone conversion range",
        "checked_at must be timezone-aware",
        "checked_at is outside the supported timezone conversion range",
        "dataset must be nonblank",
        "purpose must be nonblank",
        "secret_ref must use secret:// followed by a nonblank path",
        "source_id must be nonblank",
        "valid_from must be at or before valid_until",
        "valid_from must be timezone-aware",
        "valid_from is outside the supported timezone conversion range",
        "valid_until must be timezone-aware",
        "valid_until is outside the supported timezone conversion range",
    }
)
_GENERIC_VALIDATION_MESSAGE = "validation failed"
_ATTRIBUTE_ACCESS_ERROR_MESSAGE = "attribute access failed"
_REDACTED_LOCATION = "<redacted>"
_SAFE_ERROR_LOCATION_SEGMENTS = frozenset(
    {
        "adapter",
        "allowed",
        "as_of",
        "cache",
        "checked_at",
        "code",
        "dataset",
        "datasets",
        "declared_license",
        "display",
        "enabled",
        "findings",
        "license",
        "meaningful_after_external_repair",
        "message",
        "mode",
        "operation_status",
        "point_in_time",
        "purpose",
        "redistribution",
        "reliable_recovery_available",
        "require_point_in_time",
        "secret_ref",
        "secret_resolved",
        "source_id",
        "sources",
        "user_datasets",
        "user_sources",
        "valid_from",
        "valid_until",
    }
)
_DYNAMIC_MAPPING_LOCATION_FIELDS = frozenset({"sources"})
_SEQUENCE_LOCATION_FIELDS = frozenset(
    {"datasets", "findings", "user_datasets", "user_sources"}
)


def _sanitized_error_location(location: tuple[Any, ...]) -> tuple[Any, ...]:
    sanitized: list[Any] = []
    for index, segment in enumerate(location):
        parent = location[index - 1] if index else None
        if parent in _DYNAMIC_MAPPING_LOCATION_FIELDS:
            sanitized.append(_REDACTED_LOCATION)
        elif isinstance(segment, int):
            sanitized.append(
                segment if parent in _SEQUENCE_LOCATION_FIELDS else _REDACTED_LOCATION
            )
        elif segment in _SAFE_ERROR_LOCATION_SEGMENTS:
            sanitized.append(segment)
        else:
            sanitized.append(_REDACTED_LOCATION)
    return tuple(sanitized)


def _sanitized_line_error(original: Mapping[str, Any]) -> dict[str, Any]:
    error_type = original["type"]
    sanitized: dict[str, Any] = {
        "type": error_type,
        "loc": _sanitized_error_location(original["loc"]),
        "input": None,
    }
    if error_type == "get_attribute_error":
        sanitized["ctx"] = {"error": _ATTRIBUTE_ACCESS_ERROR_MESSAGE}
        return sanitized

    context = original.get("ctx")
    if context is None:
        return sanitized

    if error_type == "value_error" and isinstance(context, dict):
        context_error = context.get("error")
        context_message = str(context_error)
        if context_message in _SAFE_VALUE_ERROR_MESSAGES:
            sanitized["ctx"] = {"error": ValueError(context_message)}
            return sanitized

    sanitized["type"] = "value_error"
    sanitized["ctx"] = {"error": ValueError(_GENERIC_VALIDATION_MESSAGE)}
    return sanitized


def _sanitize_validation_error(error: ValidationError) -> ValidationError:
    line_errors: list[dict[str, Any]] = []
    for original in error.errors(
        include_url=False,
        include_context=True,
        include_input=False,
    ):
        line_errors.append(_sanitized_line_error(original))
    return ValidationError.from_exception_data(
        error.title,
        cast(Any, line_errors),
        input_type="python",
        hide_input=True,
    )


class _SanitizedValidationModel(BaseModel):
    # The `del` statements below remove payload references from helper traceback
    # frames before raising; they do not attempt to erase caller-owned memory.
    def __init__(self, /, **data: Any) -> None:
        sanitized_error: ValidationError | None = None
        try:
            super().__init__(**data)
        except ValidationError as error:
            sanitized_error = _sanitize_validation_error(error)
        else:
            return
        del data
        assert sanitized_error is not None
        raise sanitized_error

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        extra: ExtraValues | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        sanitized_error: ValidationError | None = None
        enforced_extra: ExtraValues | None = (
            extra if extra in (None, "forbid") else "forbid"
        )
        try:
            return super().model_validate(
                obj,
                strict=strict,
                extra=enforced_extra,
                from_attributes=from_attributes,
                context=context,
                by_alias=by_alias,
                by_name=by_name,
            )
        except ValidationError as error:
            sanitized_error = _sanitize_validation_error(error)
        del obj, context
        assert sanitized_error is not None
        raise sanitized_error

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *,
        strict: bool | None = None,
        extra: ExtraValues | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        sanitized_error: ValidationError | None = None
        enforced_extra: ExtraValues | None = (
            extra if extra in (None, "forbid") else "forbid"
        )
        try:
            return super().model_validate_json(
                json_data,
                strict=strict,
                extra=enforced_extra,
                context=context,
                by_alias=by_alias,
                by_name=by_name,
            )
        except ValidationError as error:
            sanitized_error = _sanitize_validation_error(error)
        del json_data, context
        assert sanitized_error is not None
        raise sanitized_error

    @classmethod
    def model_validate_strings(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        extra: ExtraValues | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        sanitized_error: ValidationError | None = None
        enforced_extra: ExtraValues | None = (
            extra if extra in (None, "forbid") else "forbid"
        )
        try:
            return super().model_validate_strings(
                obj,
                strict=strict,
                extra=enforced_extra,
                context=context,
                by_alias=by_alias,
                by_name=by_name,
            )
        except ValidationError as error:
            sanitized_error = _sanitize_validation_error(error)
        del obj, context
        assert sanitized_error is not None
        raise sanitized_error


class LicenseGrant(_SanitizedValidationModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    purpose: str
    cache: Literal["none", "derived_only", "raw_allowed"]
    display: Literal["none", "internal_users", "public"]
    redistribution: Literal["forbidden", "derived_only", "allowed"]

    @field_validator("purpose")
    @classmethod
    def validate_purpose(cls, value: str) -> str:
        return _nonblank(value, "purpose")


class AuthorizedSourceGrant(_SanitizedValidationModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    enabled: bool
    adapter: str
    secret_ref: str
    datasets: tuple[str, ...]
    point_in_time: bool
    valid_from: datetime
    valid_until: datetime
    license: LicenseGrant

    @field_validator("secret_ref")
    @classmethod
    def validate_secret_ref(cls, value: str) -> str:
        return _secret_reference(value)

    @field_validator("valid_from", "valid_until")
    @classmethod
    def validate_aware_datetime(
        cls,
        value: datetime,
        info: ValidationInfo,
    ) -> datetime:
        assert info.field_name is not None
        return _aware(value, info.field_name)

    @model_validator(mode="after")
    def validate_validity_interval(self) -> Self:
        if _to_utc(self.valid_from, "valid_from") > _to_utc(
            self.valid_until, "valid_until"
        ):
            raise ValueError("valid_from must be at or before valid_until")
        return self


class SourceRegistry(_SanitizedValidationModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    sources: dict[str, AuthorizedSourceGrant]


class AuthorizationInput(_SanitizedValidationModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    source_id: str
    dataset: str
    purpose: str
    as_of: datetime
    checked_at: datetime
    require_point_in_time: bool
    user_sources: tuple[str, ...]
    user_datasets: tuple[str, ...]
    secret_ref: str
    secret_resolved: bool
    declared_license: LicenseGrant
    operation_status: Literal["available", "failed", "rate-limited"]
    reliable_recovery_available: bool

    @field_validator("source_id", "dataset", "purpose")
    @classmethod
    def validate_nonblank_fields(cls, value: str, info: ValidationInfo) -> str:
        assert info.field_name is not None
        return _nonblank(value, info.field_name)

    @field_validator("as_of", "checked_at")
    @classmethod
    def validate_aware_datetime(
        cls,
        value: datetime,
        info: ValidationInfo,
    ) -> datetime:
        assert info.field_name is not None
        return _aware(value, info.field_name)

    @field_validator("secret_ref")
    @classmethod
    def validate_secret_ref(cls, value: str) -> str:
        return _secret_reference(value)


class AuthorizationFinding(_SanitizedValidationModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    code: AuthorizationFindingCode
    message: str
    meaningful_after_external_repair: bool


class AuthorizationDecision(_SanitizedValidationModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    allowed: bool
    mode: Literal["authorized"] = "authorized"
    source_id: str
    dataset: str
    secret_ref: str
    findings: tuple[AuthorizationFinding, ...]


_FINDING_MESSAGES: dict[AuthorizationFindingCode, str] = {
    "SOURCE_MISSING": "The requested source is not present in the authorized registry.",
    "SOURCE_DISABLED": "The requested source is disabled in the authorized registry.",
    "GRANT_NOT_YET_VALID": "The source grant is not valid at the authorization check time.",
    "GRANT_EXPIRED": "The source grant has expired at the authorization check time.",
    "DATASET_NOT_GRANTED": "The requested dataset is not included in the source grant.",
    "USER_SOURCE_OUT_OF_SCOPE": "The requested source is outside the user's source scope.",
    "USER_DATASET_OUT_OF_SCOPE": "The requested dataset is outside the user's dataset scope.",
    "PIT_NOT_SUPPORTED": "The source does not support the required point-in-time boundary.",
    "SECRET_REF_MISMATCH": "The supplied secret reference does not match the source grant.",
    "SECRET_UNRESOLVED": "The supplied secret reference is not reported as resolved.",
    "PURPOSE_MISMATCH": "The requested purpose does not match the source grant.",
    "LICENSE_DECLARATION_MISMATCH": (
        "The declared license does not match the source grant."
    ),
    "OPERATION_FAILED": "The authorized source operation failed.",
    "OPERATION_RATE_LIMITED": "The authorized source operation was rate-limited.",
}


def evaluate_authorization(
    registry: SourceRegistry,
    request: AuthorizationInput,
) -> AuthorizationDecision:
    findings: list[AuthorizationFinding] = []

    def add(code: AuthorizationFindingCode) -> None:
        findings.append(
            AuthorizationFinding(
                code=code,
                message=_FINDING_MESSAGES[code],
                meaningful_after_external_repair=True,
            )
        )

    source = registry.sources.get(request.source_id)
    if source is None:
        add("SOURCE_MISSING")
    else:
        if not source.enabled:
            add("SOURCE_DISABLED")

        checked_at = _to_utc(request.checked_at, "checked_at")
        if checked_at < _to_utc(source.valid_from, "valid_from"):
            add("GRANT_NOT_YET_VALID")
        if checked_at > _to_utc(source.valid_until, "valid_until"):
            add("GRANT_EXPIRED")

        if request.dataset not in source.datasets:
            add("DATASET_NOT_GRANTED")

    if request.source_id not in request.user_sources:
        add("USER_SOURCE_OUT_OF_SCOPE")
    if request.dataset not in request.user_datasets:
        add("USER_DATASET_OUT_OF_SCOPE")

    if source is not None:
        if request.require_point_in_time and not source.point_in_time:
            add("PIT_NOT_SUPPORTED")
        if request.secret_ref != source.secret_ref:
            add("SECRET_REF_MISMATCH")

    if not request.secret_resolved:
        add("SECRET_UNRESOLVED")

    if source is not None:
        if request.purpose != source.license.purpose:
            add("PURPOSE_MISMATCH")
        if request.declared_license != source.license:
            add("LICENSE_DECLARATION_MISMATCH")

    if not request.reliable_recovery_available:
        if request.operation_status == "failed":
            add("OPERATION_FAILED")
        elif request.operation_status == "rate-limited":
            add("OPERATION_RATE_LIMITED")

    return AuthorizationDecision(
        allowed=not findings,
        source_id=request.source_id,
        dataset=request.dataset,
        secret_ref=request.secret_ref,
        findings=tuple(findings),
    )


InputFailureKind = Literal["parse", "validation"]


def _load_registry(
    path: Path,
) -> tuple[SourceRegistry | None, InputFailureKind | None]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (UnicodeError, yaml.YAMLError):
        return None, "parse"
    try:
        return SourceRegistry.model_validate(payload), None
    except ValidationError:
        return None, "validation"


def _load_request(
    path: Path,
) -> tuple[AuthorizationInput | None, InputFailureKind | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None, "parse"
    try:
        return AuthorizationInput.model_validate(payload), None
    except ValidationError:
        return None, "validation"


def evaluate_authorization_files(*, registry: Path, request: Path) -> str:
    parsed_registry, registry_failure = _load_registry(registry)
    if parsed_registry is None:
        assert registry_failure is not None
        raise ValueError(
            f"authorization registry {registry_failure} input is invalid"
        )

    parsed_request, request_failure = _load_request(request)
    if parsed_request is None:
        assert request_failure is not None
        raise ValueError(f"authorization request {request_failure} input is invalid")
    return evaluate_authorization(parsed_registry, parsed_request).model_dump_json()
