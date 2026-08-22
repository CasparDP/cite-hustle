"""Pin profile contents so --stages validation covers every stage."""

from cite_hustle import pipeline as pl


def test_new_stages_in_profiles():
    for stage in ("requests", "institutional"):
        assert stage in pl.PROFILES["monthly"]
        assert stage in pl.PROFILES["incremental"]
    assert pl.PROFILES["incremental"][0] == "requests"  # user requests run first
    m = pl.PROFILES["monthly"]
    assert m.index("institutional") > m.index("fallbacks")  # browser path runs last
