import pytest
from jobs.prioritise import prioritise_cluster, VERSION


def test_people_at_risk_winning_tier_only_evidence():
    """People-term hit must grade people_at_risk and carry only winning-tier terms."""
    members = [
        {"title": None, "text": "two people trapped in a collapsed house"},
    ]
    stamp = prioritise_cluster({}, members)
    assert stamp["level"] == "people_at_risk"
    assert stamp["rank"] == 1
    # 'trapped' is a people term; 'collapsed' is a property term.
    # Evidence must contain 'trapped' (winning tier) but NOT 'collapsed' (lower tier).
    assert "trapped" in stamp["evidence"]
    assert "collapsed" not in stamp["evidence"]


def test_property_terms():
    members = [
        {"title": None, "text": "retaining wall damaged"},
    ]
    stamp = prioritise_cluster({}, members)
    assert stamp["level"] == "property"
    assert stamp["rank"] == 2
    assert "damaged" in stamp["evidence"]
    assert "retaining wall" in stamp["evidence"]
    # Evidence is sorted.
    assert stamp["evidence"] == sorted(stamp["evidence"])


def test_transport_terms():
    members = [
        {"title": None, "text": "road closed at the Terrace, use a detour"},
    ]
    stamp = prioritise_cluster({}, members)
    assert stamp["level"] == "transport"
    assert stamp["rank"] == 3
    assert "road closed" in stamp["evidence"]
    assert "detour" in stamp["evidence"]


def test_no_hits_monitor():
    members = [
        {"title": None, "text": "the river looks high this morning"},
    ]
    stamp = prioritise_cluster({}, members)
    assert stamp["level"] == "monitor"
    assert stamp["rank"] == 4
    assert stamp["evidence"] == []


def test_doctrine_ordering_one_people_beats_many_transport():
    """Many transport terms + one people term must still grade people_at_risk.

    This is a doctrine ordering, not a vote. It fails if someone rewrites the
    rule as a count or a score.
    """
    members = [
        {"title": None, "text": "road closed blocked impassable detour cordon closed to traffic"},
        {"title": None, "text": "someone is trapped on the second floor"},
    ]
    stamp = prioritise_cluster({}, members)
    assert stamp["level"] == "people_at_risk"
    assert stamp["rank"] == 1


def test_title_and_second_member_matched():
    """A term in the title is matched (blob covers title+text), and a term in
    a second member is matched (all members scanned)."""
    members = [
        {"title": "evacuation order for Newtown", "text": "nothing relevant here"},
        {"title": "other signal", "text": "the bridge is impassable"},
    ]
    stamp = prioritise_cluster({}, members)
    # 'evacuation' (people) from title of member 1, 'impassable' (transport) from
    # member 2 text. People outranks transport.
    assert stamp["level"] == "people_at_risk"
    assert stamp["rank"] == 1
    assert "evacuation" in stamp["evidence"]


def test_stamp_metadata():
    members = [
        {"title": None, "text": "two people trapped"},
    ]
    stamp = prioritise_cluster({}, members)
    assert stamp["version"] == VERSION
    assert stamp["note"] == "Keyword-derived triage hint, not a severity judgement."


def test_empty_members_monitor():
    stamp = prioritise_cluster({}, [])
    assert stamp["level"] == "monitor"
    assert stamp["rank"] == 4
    assert stamp["evidence"] == []
