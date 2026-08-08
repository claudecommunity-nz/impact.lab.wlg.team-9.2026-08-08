import pytest
import requests
from sources.nzta import parse_event, collect, _fetch

SAMPLE_NZTA_FEATURE = {
    "type": "Feature",
    "properties": {
        "id": 488095,
        "EventType": "Area Warning",
        "EventDescription": "Road Works",
        "LocationArea": "SH 58 Haywards, between Flightys Road and Moonshine Road",
        "Status": "Active",
        "IsPlanned": False,
        "Impact": "Delays",
        "ExpectedResolutionText": "2026-08-10",
        "LastEdited": "2026-08-08 09:46:04",
        "regions": [16]
    },
    "geometry": {
        "type": "MultiLineString",
        "coordinates": [
            [[174.9, -41.1], [174.91, -41.11]]
        ]
    }
}

def test_parse_event_active_wellington():
    sig = parse_event(SAMPLE_NZTA_FEATURE)
    assert sig is not None
    assert sig["external_id"] == "488095"
    assert sig["source"]["type"] == "nzta"
    assert sig["source"]["name"] == "Waka Kotahi NZTA (Journeys)"
    assert sig["source"]["collector"] == "nzta"
    assert sig["source"]["local"] is True
    assert "SH 58 Haywards" in sig["text"]
    assert "Impact: Delays." in sig["text"]
    assert sig["location_hint"]["lon"] == 174.9
    assert sig["location_hint"]["lat"] == -41.1
    assert sig["location_hint"]["confidence"] == 0.9
    assert sig["location_hint"]["method"] == "source-provided"
    assert sig["published_at"].year == 2026
    assert sig["published_at"].tzinfo is not None

def test_parse_event_filters():
    # Wrong region
    other_region = dict(SAMPLE_NZTA_FEATURE)
    other_region["properties"] = dict(SAMPLE_NZTA_FEATURE["properties"], regions=[2])
    assert parse_event(other_region) is None

    # Inactive
    inactive = dict(SAMPLE_NZTA_FEATURE)
    inactive["properties"] = dict(SAMPLE_NZTA_FEATURE["properties"], Status="Inactive")
    assert parse_event(inactive) is None

def test_parse_event_planned():
    planned = dict(SAMPLE_NZTA_FEATURE)
    planned["properties"] = dict(SAMPLE_NZTA_FEATURE["properties"], IsPlanned=True)
    sig = parse_event(planned)
    assert sig is not None
    assert sig["text"].startswith("Planned ")

def test_parse_event_point_geometry():
    point_feat = dict(SAMPLE_NZTA_FEATURE)
    point_feat["geometry"] = {
        "type": "Point",
        "coordinates": [174.8, -41.2]
    }
    sig = parse_event(point_feat)
    assert sig is not None
    assert sig["location_hint"]["lon"] == 174.8
    assert sig["location_hint"]["lat"] == -41.2

def test_collect_nzta(monkeypatch):
    def mock_fetch():
        return {
            "type": "FeatureCollection",
            "features": [SAMPLE_NZTA_FEATURE]
        }
    monkeypatch.setattr("sources.nzta._fetch", mock_fetch)
    signals = collect()
    assert len(signals) == 1
    assert signals[0]["external_id"] == "488095"

    def mock_fetch_error():
        raise requests.RequestException("Network error")
    monkeypatch.setattr("sources.nzta._fetch", mock_fetch_error)
    signals_err = collect()
    assert signals_err == []
