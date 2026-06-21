"""Dualign — GUI 模块"""

from dualign.gui.window import DualignWindow
from dualign.gui.dialogs import (
    ConfigDialog,
    BlockEditDialog,
    FileListPanel,
)
from dualign.gui.review import ReviewController
from dualign.gui.filter import FilterPanel
from dualign.gui.base_table import (
    HighlightDelegate,
    score_to_color,
    type_cl,
    marker_cl,
    anomaly_cl,
    priority_anomaly_type,
    TYPE_CL_11,
    TYPE_CL_10_01,
    TYPE_CL_NON11,
    TEXT_CL_NORMAL,
    TEXT_CL_DELETED,
    TEXT_CL_CONTEXT,
)

from dualign.gui.panels import SnapIndicator, DockPanelHelper
from dualign.gui.log_panel import LogPanel

__all__ = [
    "DualignWindow",
    "ConfigDialog",
    "BlockEditDialog",
    "FileListPanel",
    "ReviewController",
    "FilterPanel",
    "SnapIndicator",
    "DockPanelHelper",
    "LogPanel",
    "HighlightDelegate",
    "score_to_color",
    "type_cl",
    "marker_cl",
    "anomaly_cl",
    "priority_anomaly_type",
    "TYPE_CL_11",
    "TYPE_CL_10_01",
    "TYPE_CL_NON11",
    "TEXT_CL_NORMAL",
    "TEXT_CL_DELETED",
    "TEXT_CL_CONTEXT",
]
