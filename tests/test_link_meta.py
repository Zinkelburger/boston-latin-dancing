"""Reading what a link says.

The two things worth pinning down: that Meta hosts are asked as the og-scraper
(they answer nothing useful otherwise), and that Facebook's preview sentence
parses correctly — it is the only machine-readable date a Facebook event page
will give us, and every event linked to Facebook is verified from it.
"""

from datetime import datetime, timezone

import link_meta as lm

FB_EVENT = "https://www.facebook.com/events/1299596699048505/"
IG = "https://www.instagram.com/noise.boston/"
ORG = "https://www.listerevents.com/event-details/battle-of-the-beats"


# ── who we ask as ─────────────────────────────────────────────────────

def test_facebook_is_asked_as_the_og_scraper():
    # A browser UA from a datacenter IP gets a blanket 400 from Facebook
    # whether or not the event exists.
    assert lm.ua_for(FB_EVENT) == lm.META_UA


def test_instagram_is_asked_as_the_og_scraper():
    assert lm.ua_for(IG) == lm.META_UA


def test_everyone_else_gets_a_browser_ua():
    assert lm.ua_for(ORG) == lm.BROWSER_UA


def test_meta_host_detection_covers_bare_and_www_forms():
    assert lm.is_meta_host("https://facebook.com/events/1/")
    assert lm.is_meta_host("https://www.instagram.com/x/")
    assert not lm.is_meta_host("https://notfacebook.example.com/")


# ── Facebook's preview sentence ───────────────────────────────────────

def test_parses_the_standard_preview():
    d = lm.facebook_event_details(
        "Event in Cambridge, MA by Liz Lister on Saturday, August 15 2026")
    assert d["date"] == "2026-08-15"
    assert d["location"] == "Cambridge, MA"
    assert d["organizer"] == "Liz Lister"
    assert d["weekday"] == "Saturday"


def test_year_running_straight_into_the_next_sentence():
    # Facebook emits "…May 23 20265 posts in the discussion." with no
    # separator. Reading four digits and stopping is the whole trick; a
    # greedier match yields the year 20265 and a date that cannot exist.
    d = lm.facebook_event_details(
        "Dance event in Boston, MA by Clara y Al and 4 others on "
        "Saturday, May 23 20265 posts in the discussion.")
    assert d["date"] == "2026-05-23"


def test_dance_event_prefix_is_accepted():
    d = lm.facebook_event_details(
        "Dance event in Cambridge, MA by Zouk BOS and Philip Blatner on Tuesday, May 26 2026")
    assert d["date"] == "2026-05-26"
    assert d["organizer"] == "Zouk BOS and Philip Blatner"


def test_preview_without_a_location():
    d = lm.facebook_event_details(
        "Event by Luis Talavera Díaz on Sunday, May 31 2026")
    assert d["date"] == "2026-05-31"
    assert d["location"] is None
    assert d["organizer"] == "Luis Talavera Díaz"


def test_trailing_interest_counts_are_ignored():
    d = lm.facebook_event_details(
        "Event in Natick, MA by Black Mamba Entertainment Ltd on Sunday, August 9 2026 "
        "with 209 people interested and 37 people going. 16 posts in the discussion.")
    assert d["date"] == "2026-08-09"
    assert d["organizer"] == "Black Mamba Entertainment Ltd"


def test_an_instagram_profile_description_is_not_an_event():
    assert lm.facebook_event_details(
        "9,252 Followers, 7,900 Following, 172 Posts - See Instagram photos and videos "
        "from Noise | Latin luxury elevated (@noise.boston)") is None


def test_empty_description_is_not_an_event():
    assert lm.facebook_event_details("") is None
    assert lm.facebook_event_details(None) is None


def test_impossible_date_is_rejected_rather_than_guessed():
    assert lm.facebook_event_details(
        "Event in Boston, MA by Someone on Saturday, February 31 2026") is None


def test_unknown_month_is_rejected():
    assert lm.facebook_event_details(
        "Event in Boston, MA by Someone on Saturday, Smarch 12 2026") is None


# ── extraction ────────────────────────────────────────────────────────

PAGE = """
<html><head>
<title>Raw &amp; Title</title>
<meta property="og:title" content="Battle of the Beats 2026: BOSTON"/>
<meta property="og:description" content="Event in Cambridge, MA by Liz Lister on Saturday, August 15 2026"/>
<link rel="canonical" href="https://example.com/canonical"/>
<script type="application/ld+json">
{"@type":"Event","name":"BOTB","startDate":"2026-08-15T20:30:00-04:00"}
</script>
</head></html>
"""


def test_extract_pulls_every_descriptor():
    meta = lm.extract(PAGE)
    assert meta["og_title"] == "Battle of the Beats 2026: BOSTON"
    assert meta["canonical"] == "https://example.com/canonical"
    assert meta["jsonld_events"][0]["startDate"] == "2026-08-15T20:30:00-04:00"


def test_extract_unescapes_html_entities():
    # Facebook double-escapes freely — "Black Mamba&#039;s" must read back as
    # an apostrophe or every title comparison fails.
    meta = lm.extract('<meta property="og:title" content="Black Mamba&#039;s &quot;Night&quot;"/>')
    assert meta["og_title"] == 'Black Mamba\'s "Night"'


def test_extract_falls_back_to_plain_description():
    meta = lm.extract('<meta name="description" content="plain one"/>')
    assert meta["og_description"] == "plain one"


def test_extract_survives_broken_jsonld():
    meta = lm.extract('<script type="application/ld+json">{not json</script>')
    assert meta["jsonld_events"] == []


def test_extract_reads_events_out_of_a_graph():
    page = ('<script type="application/ld+json">'
            '{"@graph":[{"@type":"Event","name":"In A Graph"}]}</script>')
    assert lm.extract(page)["jsonld_events"][0]["name"] == "In A Graph"


def test_extract_of_an_empty_page_is_all_empty():
    meta = lm.extract("")
    assert meta["og_title"] == "" and meta["jsonld_events"] == []


# ── dates that are not dates ──────────────────────────────────────────

NOW = datetime(2026, 8, 13, 19, 47, 0, tzinfo=timezone.utc)  # 15:47 in Boston


def test_render_clock_is_recognised():
    # boston.gov writes the moment it rendered the page into startDate; the
    # seconds advance between fetches. Believing it would rewrite a correct
    # date to today's, because a date mismatch is acted on as "source wins".
    assert lm.looks_like_render_timestamp("2026-08-13T15:36:39", NOW)


def test_naive_stamps_are_read_as_boston_time():
    # The same value read as UTC sits four hours from now and slips through.
    assert lm.looks_like_render_timestamp("2026-08-13T15:47:00", NOW)


def test_a_real_event_tonight_is_not_a_render_clock():
    # The false positive that matters: guessing across every UTC offset would
    # condemn tonight's genuine 8pm event whenever we ran near the hour.
    assert not lm.looks_like_render_timestamp("2026-08-13T20:00:00", NOW)


def test_a_dated_event_with_an_offset_is_left_alone():
    assert not lm.looks_like_render_timestamp("2026-08-15T20:30:00-04:00", NOW)


def test_far_off_dates_are_left_alone():
    assert not lm.looks_like_render_timestamp("2026-08-20T15:47:00", NOW)
    assert not lm.looks_like_render_timestamp("2026-08-06T15:47:00", NOW)


def test_missing_or_unparseable_dates_are_not_render_clocks():
    assert not lm.looks_like_render_timestamp(None, NOW)
    assert not lm.looks_like_render_timestamp("", NOW)
    assert not lm.looks_like_render_timestamp("next Tuesday", NOW)


# ── location flattening ───────────────────────────────────────────────

def test_jsonld_location_joins_name_and_address():
    ld = {"location": {"name": "Gran Peñol", "address": {
        "streetAddress": "151 Central Ave", "addressLocality": "Lynn",
        "addressRegion": "MA", "postalCode": "01901"}}}
    assert lm.jsonld_location(ld) == "Gran Peñol, 151 Central Ave, Lynn, MA, 01901"


def test_jsonld_location_accepts_a_bare_string():
    assert lm.jsonld_location({"location": "Havana Club"}) == "Havana Club"


def test_jsonld_location_of_nothing_is_none():
    assert lm.jsonld_location({}) is None
    assert lm.jsonld_location({"location": {}}) is None
