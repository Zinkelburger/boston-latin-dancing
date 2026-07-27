"""Dedup must not merge on generic dance words alone.

Nearly every event on this site has "salsa" or "bachata" in its name, so an
overlap of only those words is not evidence of the same event. Two real false
pairs from the 2026-07-27 queue are pinned here as regressions.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from event_store import dedup_confidence


class TestGenericWordsAreNotEvidence:
    def test_rooftop_party_not_merged_into_black_mamba_social(self):
        """Different city, different hours — shared only {salsa, bachata}."""
        rooftop = {
            "id": "EFD7FCB6", "name": "Salsa and bachata rooftop party",
            "startDate": "2026-08-09T18:00:00+00:00",
            "location": "Foxglove Terrace, 40 Western Ave, Allston, MA 02163",
            "lat": 42.3634, "lng": -71.1258,
        }
        mamba = {
            "id": "23FEA93F", "name": "Black Mamba's Salsa and Bachata Social",
            "startDate": "2026-08-09T23:00:00+00:00",
            "location": "Todos Dance & Fitness Studio, 677 Worcester St, Natick, MA 01760",
            "lat": 42.2895, "lng": -71.3620,
        }
        assert dedup_confidence(rooftop, mamba) is None

    def test_class_not_merged_into_unrelated_social(self):
        """Cambridge vs Milford — shared only {bachata} plus the token "w"."""
        tina = {
            "id": "nlf-events-intermediate-bachata-w-tina-2026-07-30-20-45",
            "name": "Intermediate Bachata w/ Tina",
            "startDate": "2026-07-31T00:45:00+00:00",
            "location": "Havana Club, 288 Green St, Cambridge, MA 02139",
            "lat": 42.3654, "lng": -71.1030,
        }
        fiesta = {
            "id": "fiesta-20260731-sol-de-mexico-milford",
            "name": "Salsa & Bachata Social w/ Fiesta Dance Co",
            "startDate": "2026-07-31T04:00:00+00:00",
            "location": "Sol de Mexico, 350 E Main St, Milford, MA 01757",
            "lat": 42.1398, "lng": -71.5162,
        }
        assert dedup_confidence(tina, fiesta) is None

    def test_short_token_cannot_be_the_distinguishing_word(self):
        """"w" and "co" identify nothing; they must not rescue an overlap."""
        a = {"id": "a", "name": "Salsa Night w Ana",
             "startDate": "2026-08-09T18:00:00+00:00",
             "location": "Somewhere, Boston, MA", "lat": 42.36, "lng": -71.06}
        b = {"id": "b", "name": "Salsa Party w Co",
             "startDate": "2026-08-09T20:00:00+00:00",
             "location": "Elsewhere, Natick, MA", "lat": 42.28, "lng": -71.36}
        assert dedup_confidence(a, b) is None


class TestRealMatchesStillDetected:
    def test_distinctive_shared_word_still_merges(self):
        """"Bonche" is distinctive, so same venue + same day is still certain."""
        a = {"id": "a", "name": "El Bonche Super Matinee",
             "startDate": "2026-08-08T20:00:00+00:00",
             "location": "Sunset Cantina, 916 Commonwealth Ave, Boston",
             "lat": 42.3508, "lng": -71.1165}
        b = {"id": "b", "name": "El Bonche Matinee ft Timba Messengers",
             "startDate": "2026-08-08T20:30:00+00:00",
             "location": "Sunset Cantina, 916 Commonwealth Ave, Boston",
             "lat": 42.3508, "lng": -71.1165}
        assert dedup_confidence(a, b) == "certain"

    def test_identical_names_unaffected(self):
        a = {"id": "a", "name": "Havana Bachata Thursdays",
             "startDate": "2026-07-31T00:45:00+00:00",
             "location": "Havana Club, 288 Green St, Cambridge, MA 02139",
             "lat": 42.3654, "lng": -71.1030}
        b = {"id": "b", "name": "Havana Bachata Thursdays",
             "startDate": "2026-07-31T00:45:00+00:00",
             "location": "288 Green St, Central Sq, Cambridge",
             "lat": 42.3654, "lng": -71.1030}
        assert dedup_confidence(a, b) == "certain"

    def test_shared_url_unaffected(self):
        a = {"id": "a", "name": "Whatever", "startDate": "2026-08-08T20:00:00+00:00",
             "url": "https://example.com/e/1"}
        b = {"id": "b", "name": "Totally Different",
             "startDate": "2026-08-08T21:00:00+00:00",
             "url": "https://example.com/e/1"}
        assert dedup_confidence(a, b) == "certain"

    def test_same_venue_same_day_still_reaches_review(self):
        """Generic names at one venue on one day still deserve a human look."""
        a = {"id": "a", "name": "Salsa Night",
             "startDate": "2026-08-09T22:00:00+00:00",
             "location": "Havana Club, 288 Green St, Cambridge, MA 02139",
             "lat": 42.3654, "lng": -71.1030}
        b = {"id": "b", "name": "Bachata Social",
             "startDate": "2026-08-09T23:00:00+00:00",
             "location": "Havana Club, 288 Green St, Cambridge, MA 02139",
             "lat": 42.3654, "lng": -71.1030}
        assert dedup_confidence(a, b) == "review"
