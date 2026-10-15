import json
from copy import deepcopy
from pathlib import Path

import pytest

from sumo_data import SumoApiClient


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sumo-api-202505-day1.json"


class SnapshotSumoApiClient:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def fetch_torikumi(self, basho_id, division, day):
        if basho_id != self.snapshot["basho_id"] or day != self.snapshot["day"]:
            raise AssertionError("The saved API snapshot contains only basho day 1")
        return deepcopy(self.snapshot["torikumi"][division])

    def fetch_head_to_head(self, rikishi_id, opponent_id):
        key = f"{rikishi_id}:{opponent_id}"
        reverse_key = f"{opponent_id}:{rikishi_id}"
        h2h = self.snapshot["h2h"].get(key, self.snapshot["h2h"].get(reverse_key))
        if h2h is None:
            return []
        return deepcopy(h2h["matches"])

    def fetch_rikishi(self, rikishi_id):
        profile = self.snapshot["rikishi"].get(str(rikishi_id))
        if profile is None:
            raise AssertionError(f"Rikishi {rikishi_id} is not in the saved API snapshot")
        return deepcopy(profile)


def pytest_addoption(parser):
    parser.addoption(
        "--live-api",
        action="store_true",
        default=False,
        help="Use sumo-api.com instead of the saved local API snapshot",
    )


@pytest.fixture
def sumo_api_client(request):
    if request.config.getoption("--live-api"):
        return SumoApiClient()
    with FIXTURE_PATH.open("r", encoding="utf-8") as fixture_file:
        return SnapshotSumoApiClient(json.load(fixture_file))
