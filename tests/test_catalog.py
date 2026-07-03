import pytest

from agwise_data import catalog
from agwise_data.stac import to_stac_collection


def test_bundled_entries_load():
    sources = catalog.list_sources()
    assert "chirps" in sources
    assert "agera5" in sources


def test_default_source_resolution():
    assert catalog.source_for("PRCP") == "chirps"
    assert catalog.source_for("TMAX") == "agera5"
    # override: rainfall from AgERA5 instead of CHIRPS
    assert catalog.source_for("PRCP", "agera5") == "agera5"
    with pytest.raises(ValueError):
        catalog.source_for("TMAX", "chirps")  # chirps has no TMAX


def test_agera5_variable_specs_match_cds_names():
    spec = catalog.variable_spec("agera5", "TMAX")
    assert spec["source_name"] == "2m_temperature"
    assert spec["statistic"] == "24_hour_maximum"
    assert spec["conversion"] == "k_to_degc"
    srad = catalog.variable_spec("agera5", "SRAD")
    assert srad["conversion"] == "jm2_to_mjm2"


def test_chirps_url_template():
    entry = catalog.get_entry("chirps")
    access = catalog.primary_access(entry, "https")
    # two https blocks exist (yearly + by_month); the primary one must win
    assert access.get("role") == "primary"
    url = access["urls"]["global"]
    assert "{year}" in url and url.endswith(".nc")


def test_stac_serialization():
    coll = to_stac_collection("chirps")
    assert coll["type"] == "Collection"
    assert coll["id"] == "chirps"
    assert coll["license"]
    assert coll["extent"]["spatial"]["bbox"]
    assert coll["extent"]["temporal"]["interval"][0][0] == "1981-01-01T00:00:00Z"
    assert "AGRO.PRCP" in coll["summaries"]["variables"]
