import pytest
from datetime import datetime, timezone
from jobs.admiralty import (
    source_reliability,
    grade_signal,
    grade_cluster,
    VERSION,
    RELIABILITY_MEANING,
    CREDIBILITY_MEANING,
)

def test_geonet_no_corroborate():
    doc = {
        "signal_id": "sig-1",
        "source": {"type": "geonet", "name": "GeoNet (GNS Science)"},
    }
    stamp = grade_signal(doc)
    assert stamp["grade"] == "A3"
    assert stamp["source_reliability"] == "A"
    assert stamp["info_credibility"] == 3

def test_mastodon_no_corroborate():
    doc = {
        "signal_id": "sig-2",
        "source": {"type": "mastodon", "name": "Mastodon · mastodon.nz"},
    }
    stamp = grade_signal(doc)
    assert stamp["grade"] == "F6"
    assert stamp["source_reliability"] == "F"
    assert stamp["info_credibility"] == 6

def test_mastodon_multi_source():
    doc = {
        "signal_id": "sig-3",
        "source": {"type": "mastodon", "name": "Mastodon · mastodon.nz"},
        "enrichment": {"corroborate": {"corroboration": "multi_source"}},
    }
    stamp = grade_signal(doc)
    assert stamp["grade"] == "F2"
    assert stamp["source_reliability"] == "F"
    assert stamp["info_credibility"] == 2

def test_rss_metservice():
    doc = {
        "signal_id": "sig-4",
        "source": {"type": "rss", "name": "MetService Severe Weather Warnings"},
    }
    rel, _ = source_reliability(doc["source"])
    assert rel == "A"

def test_rss_rnz_two_sources():
    doc = {
        "signal_id": "sig-5",
        "source": {"type": "rss", "name": "RNZ National"},
        "enrichment": {"corroborate": {"corroboration": "two_sources"}},
    }
    stamp = grade_signal(doc)
    assert stamp["grade"] == "B3"
    assert stamp["source_reliability"] == "B"
    assert stamp["info_credibility"] == 3

def test_rss_random_blog():
    doc = {
        "signal_id": "sig-6",
        "source": {"type": "rss", "name": "Some Random Blog"},
    }
    rel, _ = source_reliability(doc["source"])
    assert rel == "F"

def test_grade_cluster():
    cluster = {
        "cluster_id": "c-1",
        "corroboration": "multi_source",
    }
    members = [
        {"signal_id": "s1", "source": {"type": "geonet", "name": "GeoNet (GNS Science)"}},
        {"signal_id": "s2", "source": {"type": "mastodon", "name": "Mastodon · mastodon.nz"}},
    ]
    stamp = grade_cluster(cluster, members)
    assert stamp["grade"] == "A2"
    assert stamp["source_reliability"] == "A"
    assert stamp["info_credibility"] == 2

def test_exhaustive_credibility_guard():
    sources = [
        {"type": "geonet", "name": "GeoNet (GNS Science)"},
        {"type": "mastodon", "name": "Mastodon · mastodon.nz"},
        {"type": "rss", "name": "MetService Severe Weather Warnings"},
        {"type": "rss", "name": "RNZ National"},
        {"type": "rss", "name": "Some Random Blog"},
    ]
    corrob_states = [None, "single_source", "two_sources", "multi_source"]

    for src in sources:
        for corrob in corrob_states:
            doc = {"source": src}
            if corrob is not None:
                doc["enrichment"] = {"corroborate": {"corroboration": corrob}}
            stamp = grade_signal(doc)
            assert stamp["info_credibility"] in {2, 3, 6}, f"Failed for {src}, {corrob}: got {stamp['info_credibility']}"
            assert stamp["info_credibility"] not in {1, 4, 5}

def test_stamp_metadata():
    doc = {
        "signal_id": "sig-1",
        "source": {"type": "geonet", "name": "GeoNet"},
    }
    stamp = grade_signal(doc)
    assert stamp["method"] == VERSION
    assert stamp["version"] == "admiralty-v1"
    assert isinstance(stamp["rationale"], list)
    assert len(stamp["rationale"]) > 0

def test_welectricity_source_reliability_is_A():
    rel, _ = source_reliability({"type": "welectricity", "name": "Wellington Electricity"})
    assert rel == "A"

def test_nzta_source_reliability_is_A():
    rel, _ = source_reliability({"type": "nzta", "name": "Waka Kotahi NZTA (Journeys)"})
    assert rel == "A"

def test_police_rss_source_reliability_is_A():
    rel, _ = source_reliability({"type": "rss", "name": "NZ Police — Wellington District"})
    assert rel == "A"

def test_transpower_rss_source_reliability_is_A():
    rel, _ = source_reliability({"type": "rss", "name": "Transpower"})
    assert rel == "A"

def test_welectricity_signal_grading_A3():
    doc = {
        "signal_id": "sig-welec-1",
        "source": {"type": "welectricity", "name": "Wellington Electricity"},
    }
    stamp = grade_signal(doc)
    assert stamp["grade"] == "A3"
    assert stamp["source_reliability"] == "A"
    assert stamp["info_credibility"] == 3
