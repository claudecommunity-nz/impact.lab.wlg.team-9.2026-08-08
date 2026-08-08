import pytest
from app.main import signal_feature, cluster_feature

def test_signal_feature_with_admiralty():
    doc = {
        "signal_id": "sig-100",
        "text": "Flood near waterfront",
        "geo": {"type": "Point", "coordinates": [174.77, -41.28]},
        "source": {"name": "RNZ National", "type": "rss"},
        "enrichment": {
            "admiralty": {
                "grade": "B3",
                "source_reliability": "B",
                "info_credibility": 3,
                "meaning": "Usually reliable · Possibly true",
            }
        },
    }
    feature = signal_feature(doc)
    props = feature["properties"]
    assert props["admiralty_grade"] == "B3"
    assert props["admiralty_reliability"] == "B"
    assert props["admiralty_credibility"] == 3
    assert props["admiralty_meaning"] == "Usually reliable · Possibly true"

def test_signal_feature_without_admiralty():
    doc = {
        "signal_id": "sig-101",
        "text": "Landslide on hill",
        "geo": {"type": "Point", "coordinates": [174.77, -41.28]},
        "source": {"name": "Mastodon", "type": "mastodon"},
    }
    feature = signal_feature(doc)
    props = feature["properties"]
    assert props["admiralty_grade"] is None
    assert props["admiralty_reliability"] is None
    assert props["admiralty_credibility"] is None
    assert props["admiralty_meaning"] is None

def test_cluster_feature_with_admiralty():
    doc = {
        "cluster_id": "clust-1",
        "geo": {"type": "Point", "coordinates": [174.77, -41.28]},
        "issue_type": "flood",
        "source_count": 2,
        "signal_count": 2,
        "sources": ["RNZ", "GeoNet"],
        "corroboration": "two_sources",
        "admiralty": {
            "grade": "A3",
            "source_reliability": "A",
            "info_credibility": 3,
            "meaning": "Completely reliable · Possibly true",
        },
    }
    feature = cluster_feature(doc)
    props = feature["properties"]
    assert props["admiralty_grade"] == "A3"
    assert props["admiralty_reliability"] == "A"
    assert props["admiralty_credibility"] == 3
    assert props["admiralty_meaning"] == "Completely reliable · Possibly true"

def test_cluster_feature_without_admiralty():
    doc = {
        "cluster_id": "clust-2",
        "geo": {"type": "Point", "coordinates": [174.77, -41.28]},
        "issue_type": "landslide",
        "source_count": 1,
        "signal_count": 1,
        "sources": ["Mastodon"],
    }
    feature = cluster_feature(doc)
    props = feature["properties"]
    assert props["admiralty_grade"] is None
    assert props["admiralty_reliability"] is None
    assert props["admiralty_credibility"] is None
    assert props["admiralty_meaning"] is None


def test_cluster_feature_verification_status_passthrough():
    """cluster_feature passes verification_status and verified_note from the doc."""
    doc = {
        "cluster_id": "clust-v1",
        "geo": {"type": "Point", "coordinates": [174.77, -41.28]},
        "issue_type": "flood",
        "source_count": 2,
        "signal_count": 3,
        "sources": ["RNZ", "GeoNet"],
        "verification_status": "field_verified",
        "verified_note": "checked at 14:02",
    }
    feature = cluster_feature(doc)
    props = feature["properties"]
    assert props["verification_status"] == "field_verified"
    assert props["verified_note"] == "checked at 14:02"


def test_cluster_feature_without_verification_defaults():
    """cluster_feature defaults verification_status to 'unverified' and verified_note to None."""
    doc = {
        "cluster_id": "clust-v2",
        "geo": {"type": "Point", "coordinates": [174.77, -41.28]},
        "issue_type": "flood",
        "source_count": 1,
        "signal_count": 1,
        "sources": ["Mastodon"],
    }
    feature = cluster_feature(doc)
    props = feature["properties"]
    assert props["verification_status"] == "unverified"
    assert props["verified_note"] is None
