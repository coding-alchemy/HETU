"""Configuration shared by the factor form and its session runs."""

import json
from dataclasses import dataclass, replace
from datetime import date, datetime

from hetu_data.models import LibraryRef, SnapshotRef

from .catalog import get_factor
from .core import FactorRequest, TeachingSource, parameter_identity, require, resolve_request
from .cross_section import component_requests


@dataclass(frozen=True)
class FrozenTrial:
    source: TeachingSource | SnapshotRef | LibraryRef
    factor_id: str
    definition_version: str
    purpose: str
    subjects: tuple[str, ...]
    trade_date: date
    cutoff: datetime
    parameter_key: str

    @property
    def request(self) -> FactorRequest:
        return FactorRequest(
            self.factor_id, self.subjects, self.trade_date, self.cutoff, json.loads(self.parameter_key)
        )


def build_trial(source: TeachingSource | SnapshotRef | LibraryRef, request: FactorRequest) -> FrozenTrial:
    definition = get_factor(request.factor_id)
    require(request.cutoff.utcoffset() is not None, "cutoff", "信息截止须带时区")
    subjects = tuple(request.subjects)
    if "配对" in definition.subject_kind:
        require(
            len(subjects) == 2 and all(subjects) and subjects[0] != subjects[1],
            "subjects",
            "请选择不同的第一腿和第二腿",
        )
    else:
        require(
            bool(subjects) and all(isinstance(code, str) and code.strip() for code in subjects),
            "subjects",
            "请选择适用对象",
        )
    query = resolve_request(definition, request)
    parameters = query.parameters
    if query.factor_id == "robust_cross_section_z" and parameters["component"] == "traded_amount":
        if "component_parameters" in parameters or parameters["mode"] != definition.defaults["mode"]:
            # Apply the form's mode alias when freezing a new trial. Archived receipts replay their exact input.
            component = {
                **get_factor("traded_amount").defaults,
                "mode": parameters["mode"],
                **parameters.get("component_parameters", {}),
            }
            parameters.update(mode=component["mode"], component_parameters=component)
    component_requests(query)
    if source.root is not None:
        source = replace(source, root=source.root.expanduser().resolve())
    return FrozenTrial(
        source,
        query.factor_id,
        definition.version,
        definition.purpose,
        subjects,
        query.trade_date,
        query.cutoff,
        parameter_identity(query.parameters),
    )
