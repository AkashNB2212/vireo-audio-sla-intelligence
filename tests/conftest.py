"""The supplied task pack is confidential and is not in the repository. Tests that need it are
skipped, with an explanation, when data/tickets.csv or data/agents.csv is missing."""
import pytest

from vireo_sla.data import PACK_MISSING_MESSAGE, missing_pack_files


def pytest_configure(config):
    config.addinivalue_line("markers", "pack: needs the supplied task pack in data/")


def pytest_collection_modifyitems(config, items):
    if not missing_pack_files():
        return
    skip = pytest.mark.skip(reason=PACK_MISSING_MESSAGE)
    for item in items:
        if "real" in getattr(item, "fixturenames", ()) or item.get_closest_marker("pack"):
            item.add_marker(skip)
