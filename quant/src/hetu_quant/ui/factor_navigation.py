"""Navigation between a fixed source, a factor draft and a submitted result."""

import json
from datetime import date, datetime

import pandas as pd
import streamlit as st
from hetu_data.browse import separate_grant_day
from hetu_data.library import selected_snapshot_assets
from hetu_data.store import TABLE_SCHEMAS
from hetu_data.ui.data_page import render_input_browser

from hetu_quant.data import DataStore
from hetu_quant.factors.core import CN, _unit_currency
from hetu_quant.factors.sources import (
    _attested,
    _known,
    _proof_fields,
    _resolve_group,
    _table_products,
    paid_registration_scope,
    product_scope,
)
from hetu_quant.factors.workbench_inputs import visible_evidence


def _scopes(store, selection):
    tables = (selection.table,)
    if selection.table == "daily_bars":
        tables += ("calendar", "security_status", "securities", "security_events")
    for table in tables:
        for owner, ref, manifest in selected_snapshot_assets(store, selection.source, table):
            for product in sorted(_table_products(manifest, table)):
                business_axis = table == "knowledge_evidence" or separate_grant_day(table, selection.axis)
                scope = product_scope(
                    owner,
                    ref,
                    manifest,
                    table=table,
                    product_id=product,
                    purpose=selection.purpose,
                    subjects=selection.subjects,
                    start=date.min if business_axis else selection.start,
                    end=date.max if business_axis else selection.end,
                    fields=TABLE_SCHEMAS[table]["columns"],
                )
                if selection.table == "daily_bars":
                    scope["history"] = product_scope(
                        owner, ref, manifest, table=table, product_id=product, purpose=selection.purpose,
                        subjects=selection.subjects, start=date.min, end=selection.end,
                        fields=TABLE_SCHEMAS[table]["columns"],
                    )
                if selection.product_id and (table != selection.table or product == selection.product_id):
                    scope["product_range"] = product_scope(
                        owner, ref, manifest, table=table, product_id=product, purpose=selection.purpose,
                        subjects=selection.subjects, start=date.min, end=date.max,
                        fields=TABLE_SCHEMAS[table]["columns"],
                    )
                yield dict(
                    scope, root=str(owner.root.resolve()), snapshot_id=ref.snapshot_id, table=table, product_id=product
                )


def _describe_amounts(store, selection, rows, source_refs):
    assets = {
        (str(owner.root.resolve()), ref.snapshot_id): (owner, ref, manifest)
        for owner, ref, manifest in selected_snapshot_assets(store, selection.source, "knowledge_evidence")
    }
    keys = ("成交额币种", "成交额单位", "成交额依据")
    descriptions = {}
    cutoff = datetime.now(CN)
    for asset, frame in rows.loc[rows["amount_cny"].notna()].groupby(["source_root", "snapshot_id"]):
        if asset not in assets:
            continue
        owner, ref, manifest = assets[asset]
        record_keys = tuple(f"{row.code}|{row.date}|amount_cny" for row in frame.itertuples())
        proofs = _proof_fields(
            owner, ref, manifest, "daily_bars", ("amount_cny",), purpose=selection.purpose, record_keys=record_keys
        )
        for index, row in frame.iterrows():
            observed = dict(row.to_dict(), source_ref=source_refs.at[index])
            candidates = _attested([observed], ("amount_cny",), proofs, None)
            coordinate = row["code"], row["date"], "amount_cny"
            proof = _resolve_group(
                [p for p in candidates.get(coordinate, ()) if _known(p["available_at"], cutoff)],
                quote_field="amount_cny",
            )
            if not proof:
                continue
            if proof.get("_conflicts"):
                descriptions[index] = ("待核对", "待核对", "原件冲突")
            elif proof.get("matched"):
                unit = proof.get("unit")
                currency = proof.get("currency") or _unit_currency(unit)
                if unit in ("元", "人民币元", "万元", "亿元") and not currency:
                    currency = "CNY"
                reference = proof["evidence_ref"]
                if pd.isna(row["source_ref"]) and reference == observed["source_ref"]:
                    reference = "已核对获准金额凭据"
                descriptions[index] = (currency or "未知", unit or "未知", reference)
    values = [
        descriptions.get(i, ("未知", "未知", "缺少匹配的金额原件"))
        if pd.notna(rows.at[i, "amount_cny"]) else ("", "", "")
        for i in rows.index
    ]
    return pd.DataFrame(values, columns=keys, index=rows.index)


def _switch(page):
    st.session_state["next_page"] = page


def open_factor_evidence(attempt_id: str, *, row_index=None, gap_index=None) -> None:
    if (row_index is None) == (gap_index is None):
        raise ValueError("证据定位需要恰好一个行或缺口索引")
    st.session_state["factor_evidence_location"] = (attempt_id, row_index, gap_index)
    _switch("数据")


def _return_to_draft():
    st.session_state.pop("factor_evidence_location", None)
    st.session_state["factor_restore_draft"] = True
    _switch("因子库")


def render_factor_data(store):
    state = st.session_state
    if location := state.get("factor_evidence_location"):
        attempt_id, row_index, gap_index = location
        attempt = state.get("factor_attempts", {}).get(attempt_id)
        st.subheader("本次试算的输入")
        st.button("返回试算配置", key="factor_return_to_draft", on_click=_return_to_draft)
        if attempt is None or attempt.result is None:
            st.warning("当前会话无法定位该次计算。")
            return True
        st.caption(
            f"{attempt.trial.factor_id} · 请求日 {attempt.trial.trade_date} · "
            f"信息截止 {attempt.trial.cutoff} · {attempt.trial.source}"
        )
        try:
            detail = visible_evidence(store, attempt.trial, attempt.result, row_index=row_index, gap_index=gap_index)
            download = visible_evidence(
                store, attempt.trial, attempt.result, row_index=row_index, gap_index=gap_index, operation="download"
            )
        except (ValueError, OSError, PermissionError):
            st.warning("当前无法核对该证据的位置或读取许可。")
        else:
            if detail.get("item") is not None:
                st.json(detail["item"], expanded=True)
            if detail["reason"]:
                st.info(detail["reason"])
            if download["permitted"]:
                st.download_button(
                    "下载该项获准证据",
                    json.dumps(download["item"], ensure_ascii=False, default=str),
                    file_name=f"{attempt_id}-evidence.json",
                    mime="application/json",
                )
            elif download["reason"]:
                st.caption(download["reason"])
        return True
    incoming = render_input_browser(
        store,
        describe_amounts=lambda selection, rows, refs: _describe_amounts(store, selection, rows, refs),
        registration_scope=paid_registration_scope,
        scopes_for=lambda selection: _scopes(store, selection),
        lookup_for=lambda source, day, name: object_candidates(
            DataStore(store.root), source, day, "factor_calculation", name
        ),
    )
    if incoming:
        state["factor_browse_incoming"] = incoming
        state["factor_restore_draft"] = True
        state["next_page"] = "因子库"
        st.rerun()
    return False


def object_candidates(store, source, day, purpose, needle):
    from hetu_data.browse import BrowseSelection, lookup_objects

    scopes = []
    for table in ("securities", "historical_securities", "memberships", "boards"):
        for owner, ref, manifest in selected_snapshot_assets(store, source, table):
            for product in sorted(_table_products(manifest, table)):
                scope = product_scope(
                    owner,
                    ref,
                    manifest,
                    table=table,
                    product_id=product,
                    purpose=purpose,
                    subjects=(),
                    start=day,
                    end=day,
                    fields=TABLE_SCHEMAS[table]["columns"],
                )
                scopes.append(
                    dict(
                        scope,
                        root=str(owner.root.resolve()),
                        snapshot_id=ref.snapshot_id,
                        table=table,
                        product_id=product,
                    )
                )
    selection = BrowseSelection(source, purpose, "object_lookup", (), (), day, day)
    return lookup_objects(store, selection, scopes=scopes, needle=needle)
