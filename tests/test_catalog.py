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


# ---------------------------------------------------------------------------
# Per-variable source override: a source can be a plain id (all vars) or a
# {variable: source} mapping so one call mixes sources.


def test_source_for_accepts_per_variable_mapping():
    from agwise_data.catalog import source_for, static_source_for

    # mapping: listed var uses the mapped source, others keep their default
    assert source_for("PRCP", {"PRCP": "chirps_v3"}) == "chirps_v3"
    assert source_for("TMAX", {"PRCP": "chirps_v3"}) == "agera5"   # default
    # key written any way (short / namespaced / lower) still matches
    assert source_for("PRCP", {"AGRO.PRCP": "chirps_v3"}) == "chirps_v3"
    assert source_for("PRCP", {"prcp": "chirps_v3"}) == "chirps_v3"
    # plain string still forces for the variable
    assert source_for("PRCP", "chirps_v3") == "chirps_v3"
    # static path honours the mapping too
    assert static_source_for("CLAY", {"CLAY": "isda"}) == "isda"
    assert static_source_for("SOC", {"CLAY": "isda"}) == "soilgrids"


def test_source_for_mapping_still_validates_unsupported():
    import pytest

    from agwise_data.catalog import source_for

    # forcing a var onto a source that lacks it is still a clear error
    with pytest.raises(ValueError, match="does not provide"):
        source_for("TMAX", {"TMAX": "chirps_v3"})


def test_cli_parse_source_mapping():
    from agwise_data.cli import _parse_source

    assert _parse_source("agera5") == "agera5"
    assert _parse_source("PRCP=chirps_v3") == {"PRCP": "chirps_v3"}
    assert _parse_source("PRCP=chirps_v3,TMAX=agera5") == {
        "PRCP": "chirps_v3", "TMAX": "agera5"
    }
