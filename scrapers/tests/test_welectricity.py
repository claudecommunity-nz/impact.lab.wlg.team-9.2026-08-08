import os
import pytest
import requests
from sources.welectricity import parse_outage, collect, _fetch

SAMPLE_UNPLANNED_OPEN = {
    "id": "2753097",
    "type": "unplanned",
    "status": "INPRG",
    "timeOfFault": "2026-08-01 08:05:00",
    "suburbsText": "Johnsonville",
    "lastUpdatedCustomersAffected": "144",
    "lastUpdatedComments": "Feeder trip affecting local area",
    "link": "https://www.welectricity.co.nz/outages/2753097",
    "location": {"lat": -41.2301396, "lng": 174.80541326},
    "areas": [{"street": "Dominion Park Street", "suburb": "Johnsonville", "city": "Wellington", "region": "Wellington", "latitude": -41.2305632, "longitude": 174.8072561}],
    "timeBasedStatus": "Investigating",
    "lastUpdatedEta": "10:30"
}

def test_parse_outage_unplanned_open():
    sig = parse_outage(SAMPLE_UNPLANNED_OPEN)
    assert sig is not None
    assert sig["source"]["type"] == "welectricity"
    assert sig["source"]["name"] == "Wellington Electricity"
    assert sig["source"]["collector"] == "welectricity"
    assert sig["source"]["local"] is True
    assert sig["external_id"] == "2753097"
    assert sig["url"] == "https://www.welectricity.co.nz/outages/2753097"
    assert sig["text"].startswith("Power outage — Johnsonville")
    assert "144 customers affected" in sig["text"]
    assert sig["location_hint"]["lat"] == -41.2301396
    assert sig["location_hint"]["lon"] == 174.80541326
    assert sig["location_hint"]["confidence"] == 0.95
    assert sig["location_hint"]["method"] == "source-provided"
    assert sig["published_at"].year == 2026
    assert sig["published_at"].tzinfo is not None

def test_parse_outage_closed_and_env_toggle(monkeypatch):
    closed = dict(SAMPLE_UNPLANNED_OPEN, status="Closed")
    
    # Default: closed outages returns None
    monkeypatch.delenv("WELECTRICITY_INCLUDE_CLOSED", raising=False)
    assert parse_outage(closed) is None

    # Enabled: closed outages returns signal with 'restored' in text
    monkeypatch.setenv("WELECTRICITY_INCLUDE_CLOSED", "1")
    sig = parse_outage(closed)
    assert sig is not None
    assert "restored" in sig["text"].lower()

def test_parse_outage_planned():
    planned = dict(SAMPLE_UNPLANNED_OPEN, type="planned")
    assert parse_outage(planned) is None

def test_parse_outage_missing_location():
    no_loc = dict(SAMPLE_UNPLANNED_OPEN, location=None)
    sig = parse_outage(no_loc)
    assert sig is not None
    assert "location_hint" not in sig or sig["location_hint"] is None

def test_collect_welectricity(monkeypatch):
    def mock_fetch():
        return {
            "unplannedOutages": [SAMPLE_UNPLANNED_OPEN],
            "plannedOutages": []
        }
    
    monkeypatch.setattr("sources.welectricity._fetch", mock_fetch)
    signals = collect()
    assert len(signals) == 1
    assert signals[0]["external_id"] == "2753097"

    def mock_fetch_error():
        raise requests.RequestException("Network error")

    monkeypatch.setattr("sources.welectricity._fetch", mock_fetch_error)
    signals_error = collect()
    assert signals_error == []
