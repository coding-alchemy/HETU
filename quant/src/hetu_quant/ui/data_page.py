"""共享数据网页组件，保留原调用合同。"""

from hetu_data.ui.data_page import (
    render_archive,
    render_library,
    render_maintenance,
    render_maintenance_job,
    submit_data_plan,
)

__all__ = ["render_archive", "render_library", "render_maintenance", "render_maintenance_job", "submit_data_plan"]
