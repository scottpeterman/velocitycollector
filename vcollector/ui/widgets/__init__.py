"""VelocityCollector UI Widgets."""

from vcollector.ui.widgets.stat_cards import StatCard
from vcollector.ui.widgets.devices_view import DevicesView
from vcollector.ui.widgets.sites_view import SitesView
from vcollector.ui.widgets.platforms_view import PlatformsView
from vcollector.ui.widgets.jobs_view import JobsView
from vcollector.ui.widgets.credentials_view import CredentialsView
from vcollector.ui.widgets.history_view import HistoryView
from vcollector.ui.widgets.output_view import OutputView
from vcollector.ui.widgets.run_view import RunView
from vcollector.ui.widgets.vault_view import VaultView

__all__ = [
    "StatCard",
    "DevicesView",
    "SitesView",
    "PlatformsView",
    "JobsView",
    "CredentialsView",
    "HistoryView",
    "OutputView",
    "RunView",
    "VaultView",
]