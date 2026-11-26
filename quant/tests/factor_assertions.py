"""Test-side evidence. A success receipt is written only after assertions return."""

from contextlib import contextmanager

from hetu_quant.factors.catalog import get_factor
from hetu_quant.factors.core import resolve_request
from hetu_quant.factors.selftest import record_selftest


@contextmanager
def checked(root, node, factor_id, case_id, expected, *, parameters=None, source="离线合成", replay=None, **metadata):
    definition = get_factor(factor_id)
    parameters = {**definition.defaults, **(parameters or {})}
    actual = {}
    error = None
    try:
        yield actual
    except Exception as exception:
        error = exception
        raise
    finally:
        if root is not None:
            if replay is not None:
                query, document = replay
                metadata.update(
                    tested_parameters=parameters,
                    execution_parameters=resolve_request(definition, query).parameters,
                    replay=dict(
                        trade_date=str(query.trade_date),
                        cutoff=query.cutoff.isoformat(),
                        subjects=query.subjects,
                        tables=document["tables"],
                    ),
                )
            record_selftest(
                root,
                factor_id,
                definition.version,
                parameters,
                case_id,
                node=node,
                expected=expected,
                actual=actual,
                source=source,
                **metadata,
                status="passed" if error is None else "failed",
                failure=None if error is None else str(error),
            )
