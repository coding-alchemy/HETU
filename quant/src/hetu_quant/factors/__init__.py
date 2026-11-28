"""Public entry point for reproducible factor calculations."""

from dataclasses import replace

from .catalog import FactorDefinition, get_factor, list_factors
from .core import FactorFacts, FactorRequest, FactorResult, MissingFact, TeachingSource, close_cutoff, resolve_request
from .cross_section import component_requests
from .sources import read_facts

__all__ = [
    "FactorDefinition",
    "FactorFacts",
    "FactorRequest",
    "FactorResult",
    "TeachingSource",
    "close_cutoff",
    "compute_factor",
    "list_factors",
]


def compute_factor(store, source, request):
    definition = get_factor(request.factor_id)
    if request.cutoff.utcoffset() is None:
        raise ValueError("信息截止须带时区")
    facts = FactorFacts({}, {"id": getattr(source, "case_id", getattr(source, "snapshot_id", ""))})
    try:
        request = resolve_request(definition, request)
        component_requests(request)
        facts = read_facts(store, source, definition, request)
        if not request.subjects:
            raise MissingFact("subjects", "请选择适用对象")
        result = definition.compute(facts, request)
    except MissingFact as error:
        result = facts.result(request, [], [error])
    result.version = definition.version
    result._source_ref = replace(source, root=source.root.expanduser().resolve()) if source.root is not None else source
    return result
