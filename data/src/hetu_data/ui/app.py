"""显式数据根上的独立网页，不自动启动 worker。"""

import argparse
import uuid
from pathlib import Path

import streamlit as st

from hetu_data.catalog import DataTaskStore
from hetu_data.jobs import submit_update_plan
from hetu_data.models import SnapshotRef
from hetu_data.store import DataStore
from hetu_data.ui.data_page import render_acquisition, render_archive, render_data_tasks, render_maintenance


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    store = DataStore(args.root)
    tasks = DataTaskStore(args.root)
    st.title("HETU 数据")
    st.caption("仅查询本地已发布数据；提交任务后需显式运行 worker。")
    snapshots = [sid for sid, _ in store._published_manifests()]
    if snapshots:
        selected = st.selectbox("档案快照", snapshots, key="archive_snapshot")
        render_archive(store, SnapshotRef(selected, args.root))
    else:
        st.info("尚无已发布快照。")

    def submit(plan):
        job = submit_update_plan(tasks, plan, uuid.uuid4().hex)
        st.success(f"已提交任务：{job.job_id}")

    render_acquisition(store, submit)
    render_maintenance(store, submit)
    render_data_tasks(tasks)


if __name__ == "__main__":
    main()
