"""Comparable cross-sections, transformations, and declared weighted scores."""

from dataclasses import replace

import numpy as np
import pandas as pd

from .core import MissingFact, check_conflict, number, output, positive, require, resolve_request
from .pairs import least_squares
from .prices import price_momentum
from .sources import known_records
from .stock_risk import market_beta


def active_members(facts, request, group, *, day=None):
    day = day or str(request.trade_date)
    return [
        row
        for row in known_records(facts, "memberships", request, group)
        if row.get("valid_from", "") <= day <= (row.get("valid_to") or "9999") and row.get("member")
    ]


def scope(facts, request, group=None, *, day=None):
    group = group or request.parameters.get("group")
    day = day or str(request.trade_date)
    if group is None:
        return request.subjects, [], False
    members = active_members(facts, request, group, day=day)
    for row in members:
        check_conflict(row, "memberships", any_field=True)
    complete = any(
        row.get("complete") is True and row.get("start", "9999") <= day <= row.get("end", "")
        for row in known_records(facts, "membership_coverage", request, group)
    )
    require(members, "memberships", "缺所选日期的成员名单", subject=group, period=day)
    return tuple(dict.fromkeys(row["member"] for row in members)), members, complete


def percentile(values):
    require(len(values) > 0, "sample", "无可比读数")
    if len(values) == 1:
        return np.array([50.0])
    rank = pd.Series(values).rank(method="average").to_numpy()
    return (rank - 1) * 100 / (len(values) - 1)


def robust_z(values, epsilon):
    positive(epsilon, "epsilon")
    array = np.asarray(values, dtype=float)
    median = np.median(array)
    mad = np.median(abs(array - median))
    return (array - median) / (1.4826 * mad + epsilon)


def component_requests(request):
    from .catalog import get_factor

    if request.factor_id == "weighted_factor_score":
        components = request.parameters["components"]
        require(isinstance(components, dict) and components, "components", "需要事前声明合成分量")
    elif request.factor_id in ("robust_cross_section_z", "cross_section_clip", "neutralized_score", "momentum_rank"):
        name = "price_momentum" if request.factor_id == "momentum_rank" else request.parameters["component"]
        require(isinstance(name, str), "component", "需要因子标识")
        components = {name: {"parameters": request.parameters.get("component_parameters", {})}}
    else:
        return {}
    children = {}
    for name, settings in components.items():
        path = f"components.{name}"
        require(isinstance(settings, dict), path, "分量配置需要 JSON 对象")
        try:
            definition = get_factor(name)
        except (KeyError, TypeError):
            raise MissingFact(path, "未登记的截面分量") from None
        require(
            bool({"个股", "ETF"} & set(definition.subject_kind)) and definition.unit not in ("类别", "信号"),
            path,
            "分量须为个体可比较数值",
        )
        if request.factor_id == "weighted_factor_score":
            for field in ("weight", "direction"):
                try:
                    value = number(settings, field)
                except MissingFact as error:
                    raise MissingFact(f"{path}.{field}", str(error)) from error
                require(
                    value >= 0 if field == "weight" else value in (-1, 1),
                    f"{path}.{field}",
                    "权重要非负，方向只能取 +1 或 −1",
                )
            if "clip" in settings:
                limits = settings["clip"]
                require(
                    isinstance(limits, list)
                    and len(limits) == 2
                    and all(type(x) in (int, float) for x in limits)
                    and 0 <= limits[0] <= limits[1] <= 1,
                    f"{path}.clip",
                    "去极值需要两个顺序正确的分位",
                )
            require(
                settings.get("neutralize") in (None, "industry", "size", "both"), f"{path}.neutralize", "未知中性化维度"
            )
        accepted_settings = {"parameters", "weight", "direction", "clip", "neutralize", "source"}
        for key in settings.keys() - accepted_settings:
            raise MissingFact(f"{path}.{key}", "未识别的分量配置")
        nested = settings.get("parameters", {})
        require(isinstance(nested, dict), f"{path}.parameters", "子因子参数需要 JSON 对象")
        # 已批准的排名与合成旧请求以父窗口指定不跳期动量。明确的子参数优先。
        if name == "price_momentum" and not nested and "window" in request.parameters:
            nested = {"window": request.parameters["window"], "skip": 0}
        try:
            child = resolve_request(definition, replace(request, factor_id=name, parameters=nested))
        except MissingFact as error:
            raise MissingFact(f"{path}.parameters.{error.field}", str(error)) from error
        children[name] = definition, child
    return children


def _source_rows(facts, request, subjects, name):
    definition, child = component_requests(request)[name]
    try:
        report = definition.compute(facts, replace(child, subjects=subjects))
    except MissingFact as error:
        raise MissingFact(
            f"components.{name}.{error.field}",
            str(error),
            subject=error.subject,
            period=error.period,
            evidence=error.evidence,
            event=error.event,
        ) from error
    relevant_gaps = [gap for gap in report.gaps if gap not in facts.gaps]
    if relevant_gaps:
        gap = relevant_gaps[0]
        raise MissingFact(
            f"components.{name}.{gap['field']}",
            gap["reason"],
            subject=gap["subject"],
            period=gap["period"],
            evidence=gap.get("evidence"),
            event=gap.get("event"),
        )
    by_code = {row["subject"]: row for row in report.rows if isinstance(row["subject"], str)}
    require(
        len(report.rows) == len(subjects) == len(by_code) and set(by_code) == set(subjects),
        f"components.{name}",
        "分量需要每个对象一个读数",
    )
    require(
        all(type(row["value"]) in (float, int) and np.isfinite(row["value"]) for row in by_code.values())
        and len({row["unit"] for row in by_code.values()}) == 1,
        f"components.{name}",
        "截面分量不是同单位可比数值",
    )
    return [dict(by_code[code], parameters=child.parameters) for code in subjects]


def _neutralize(facts, request, rows, values, mode):
    require(mode in ("industry", "size", "both"), "neutralize", "未知中性化维度")
    details = {}
    vectors = [np.ones(len(rows))]
    sources = []
    if mode in ("industry", "both"):
        industries = []
        for row in rows:
            exposures = [
                r
                for r in facts.records("classifications", row["subject"])
                if r.get("valid_from", "9999") <= str(request.trade_date) <= (r.get("valid_to") or "9999")
            ]
            require(
                len(exposures) == 1 and exposures[0].get("industry"),
                "industry",
                "缺时点行业分类",
                subject=row["subject"],
            )
            industries.append(exposures[0]["industry"])
            sources.extend(exposures)
        for industry in sorted(set(industries))[1:]:
            vectors.append(np.array([value == industry for value in industries]))
        details["industries"] = industries
    if mode in ("size", "both"):
        from .market_activity import total_market_cap

        caps = total_market_cap(facts, replace(request, subjects=tuple(row["subject"] for row in rows)))
        require(len(caps.rows) == len(rows), "market_cap_cny", "缺时点总市值")
        log_caps = np.log([row["value"] for row in caps.rows])
        vectors.append(log_caps)
        details["log_caps"] = log_caps.tolist()
        for row in caps.rows:
            sources.extend(row["inputs"])
    _, residuals = least_squares(np.column_stack(vectors), np.asarray(values))
    return residuals, details, sources


def _clip(values, lower, upper):
    require(0 <= lower <= upper <= 1, "quantiles", "上下分位界需要按升序处于 0 至 1")
    lo, hi = np.quantile(values, (lower, upper), method="linear")
    return np.clip(values, lo, hi), dict(lower=float(lo), upper=float(hi))


def _single(facts, request, kind):
    subjects, members, complete = scope(facts, request)
    name = "price_momentum" if kind == "rank" else request.parameters["component"]
    rows = _source_rows(facts, request, subjects, name)
    values = [r["value"] for r in rows]
    detail = dict(
        denominator=len(values), scope="完整原池" if complete else "已证子集", single_observation=len(values) == 1
    )
    if kind == "rank":
        transformed, unit = percentile(values), "百分点"
    elif kind == "z":
        transformed, unit = robust_z(values, number(request.parameters, "epsilon")), "标准分"
    elif kind == "clip":
        transformed, limits = _clip(values, number(request.parameters, "lower"), number(request.parameters, "upper"))
        detail.update(limits)
        unit = rows[0]["unit"]
    else:
        transformed, exposures, extra = _neutralize(facts, request, rows, values, request.parameters["neutralize"])
        detail.update(exposures)
        members = [*members, *extra]
        unit = rows[0]["unit"]
    inputs = [*members, *(item for row in rows for item in row["inputs"])]
    result = [
        output(
            r["subject"],
            float(value),
            unit,
            dict(detail, raw=r["value"], component=name, parameters=r["parameters"]),
            inputs,
        )
        for r, value in zip(rows, transformed, strict=True)
    ]
    issues = [MissingFact("memberships", "仅能证明已证子集，原池分母未知")] if not complete else []
    return facts.result(request, result, issues)


def momentum_rank(facts, request):
    return _single(facts, request, "rank")


def robust_cross_section_z(facts, request):
    return _single(facts, request, "z")


def cross_section_clip(facts, request):
    return _single(facts, request, "clip")


def neutralized_score(facts, request):
    return _single(facts, request, "neutral")


def weighted_factor_score(facts, request):
    subjects, members, complete = scope(facts, request)
    components = request.parameters["components"]
    require(isinstance(components, dict) and components, "components", "需要事前声明合成分量")
    values = np.zeros(len(subjects))
    details = {subject: {} for subject in subjects}
    inputs = list(members)
    for name, settings in components.items():
        source = _source_rows(facts, request, subjects, name)
        inputs.extend(item for row in source for item in row["inputs"])
        raw = [row["value"] for row in source]
        applied = raw
        transformations = {}
        if "clip" in settings:
            limits = settings["clip"]
            applied, transformations["clip"] = _clip(applied, *limits)
        if mode := settings.get("neutralize"):
            applied, transformations["neutralize"], extra = _neutralize(facts, request, source, applied, mode)
            inputs.extend(extra)
        z = robust_z(applied, number(request.parameters, "epsilon"))
        weight, direction = number(settings, "weight"), number(settings, "direction")
        require(weight >= 0 and direction in (-1, 1), name, "权重非负，方向必须为 +1 或 −1")
        values += weight * direction * z
        for i, subject in enumerate(subjects):
            details[subject][name] = dict(
                raw=raw[i],
                z=float(z[i]),
                direction=direction,
                weight=weight,
                transformations=transformations,
                parameters=source[i]["parameters"],
                unit=source[i]["unit"],
                **({"weight_source": settings["source"]} if "source" in settings else {}),
            )
    issues = [MissingFact("memberships", "仅能证明已证子集，原池分母未知")] if not complete else []
    rows = [
        output(
            subject,
            float(value),
            "分数",
            dict(components=details[subject], denominator=len(subjects), complete=complete),
            inputs,
        )
        for subject, value in zip(subjects, values, strict=True)
    ]
    if benchmark := request.parameters.get("exposure_benchmark"):
        try:
            _benchmark_exposures(facts, request, rows, benchmark)
        except MissingFact as error:
            issues.append(error)
    return facts.result(request, rows, issues)


def _benchmark_exposures(facts, request, rows, benchmark):
    member_codes, members, complete = scope(facts, request, benchmark)
    require(complete, "memberships", "基准成员覆盖不完整", subject=benchmark)
    weights = [number(next(row for row in members if row["member"] == code), "weight") for code in member_codes]
    require(abs(sum(weights) - 1) <= 1e-8 and all(weight >= 0 for weight in weights), "weight", "基准权重合计须为 1")
    from .market_activity import total_market_cap

    codes = tuple(dict.fromkeys([*member_codes, *(row["subject"] for row in rows)]))
    combined = replace(request, subjects=codes)
    beta_request = replace(
        combined, parameters={**combined.parameters, "window": combined.parameters.get("beta_window", 60)}
    )
    beta_rows = market_beta(facts, beta_request).rows
    style_rows = price_momentum(facts, combined).rows
    cap_rows = total_market_cap(facts, combined).rows
    betas, styles, caps = (
        {row["subject"]: row["value"] for row in sampled} for sampled in (beta_rows, style_rows, cap_rows)
    )
    inputs = [*members, *(item for row in (*beta_rows, *style_rows, *cap_rows) for item in row["inputs"])]
    industries = {}
    for code in codes:
        classes = [
            row
            for row in facts.records("classifications", code)
            if row.get("valid_from", "9999") <= str(request.trade_date) <= (row.get("valid_to") or "9999")
        ]
        require(len(classes) == 1 and classes[0].get("industry"), "industry", "缺基准暴露所需行业", subject=code)
        industries[code] = classes[0]["industry"]
        inputs.extend(classes)
    require(all(set(codes) <= set(item) for item in (betas, styles, caps)), "exposure", "基准暴露所需事实缺失")
    groups = sorted(set(industries.values()))
    exposures = {
        code: {
            "industry": {group: int(group == industries[code]) for group in groups},
            "size": float(np.log(caps[code])),
            "beta": betas[code],
            "style": styles[code],
        }
        for code in codes
    }
    average = {
        key: sum(weight * exposures[code][key] for code, weight in zip(member_codes, weights, strict=True))
        for key in ("size", "beta", "style")
    }
    average["industry"] = {
        group: sum(
            weight * exposures[code]["industry"][group] for code, weight in zip(member_codes, weights, strict=True)
        )
        for group in groups
    }
    for row in rows:
        row["inputs"] = [*row["inputs"], *inputs]
        own = exposures[row["subject"]]
        difference = {key: own[key] - average[key] for key in ("size", "beta", "style")}
        difference["industry"] = {group: own["industry"][group] - average["industry"][group] for group in groups}
        row["components"]["benchmark_exposure"] = dict(
            benchmark=benchmark,
            weights=dict(zip(member_codes, weights, strict=True)),
            date=str(request.trade_date),
            own=own,
            reference=average,
            difference=difference,
        )
