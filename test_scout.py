import unittest
from unittest.mock import patch, MagicMock
import scout
import json
from datetime import datetime

class TestScoutV2(unittest.TestCase):

    @patch('scout.requests.post')
    def test_get_spotify_token(self, mock_post):
        mock_res = MagicMock()
        mock_res.json.return_value = {
            "access_token": "mock_token",
            "expires_in": 3600
        }
        mock_post.return_value = mock_res

        # Reset cache
        scout._spotify_token_cache = {"token": None, "expires_at": 0}

        with patch('scout.SPOTIFY_CLIENT_ID', 'test_id'), patch('scout.SPOTIFY_CLIENT_SECRET', 'test_secret'):
            token = scout.get_spotify_token()

        self.assertEqual(token, "mock_token")

    def test_extract_playlist_id(self):
        url = "https://open.spotify.com/playlist/4AGyN4LWzSqQXK1laupEaI?si=abc"
        self.assertEqual(scout.extract_playlist_id(url), "4AGyN4LWzSqQXK1laupEaI")

        uri = "spotify:playlist:4AGyN4LWzSqQXK1laupEaI"
        self.assertEqual(scout.extract_playlist_id(uri), "4AGyN4LWzSqQXK1laupEaI")

    @patch('scout.requests.post')
    def test_resolve_instagram_via_search(self, mock_post):
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.text = """
        <html>
            <a href="https://www.instagram.com/hawxxmusic/reels/">Instagram</a>
        </html>
        """
        mock_post.return_value = mock_res

        url = scout.resolve_instagram_via_search("HAWXX")
        self.assertEqual(url, "https://www.instagram.com/hawxxmusic/")

    @patch('scout.requests.get')
    def test_scrape_lastfm_artist_events(self, mock_get):
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.text = """
        <html>
            <div class="events-list-item">
                <a href="/event/12345">Gira 2026</a>
                <time datetime="2026-10-15T20:00:00"></time>
                <span class="venue-location">Wurlitzer Ballroom, Madrid, Spain</span>
            </div>
        </html>
        """
        mock_get.return_value = mock_res

        events = scout.scrape_lastfm_artist_events("HAWXX")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_name"], "Gira 2026")
        self.assertEqual(events[0]["city"], "Madrid")
        self.assertEqual(events[0]["venue"], "Wurlitzer Ballroom")
        self.assertEqual(events[0]["event_date"], "2026-10-15")

    @patch('scout.requests.post')
    def test_check_instagram_tour_keywords(self, mock_post):
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.text = """
        <table>
            <tr>
                <td class="result-snippet">HAWXX touring in Spain! Madrid show announced.</td>
            </tr>
        </table>
        """
        mock_post.return_value = mock_res

        flagged, words = scout.check_instagram_tour_keywords("HAWXX", "https://instagram.com/hawxx")
        self.assertTrue(flagged)
        self.assertIn("spain", words)
        self.assertIn("madrid", words)

    @patch('scout.supabase')
    def test_sweep_past_concerts(self, mock_supabase):
        mock_delete = MagicMock()
        mock_lt = MagicMock()
        mock_execute = MagicMock()

        mock_supabase.table.return_value = mock_delete
        mock_delete.delete.return_value = mock_lt
        mock_lt.lt.return_value = mock_execute
        mock_execute.execute.return_value = MagicMock(data=[{"id": 1}, {"id": 2}])

        # Call function
        scout.sweep_past_concerts()

        # Assertions
        mock_supabase.table.assert_called_once_with("concerts")
        mock_delete.delete.assert_called_once()
        mock_lt.lt.assert_called_once()

    @patch('scout.requests.get')
    def test_robust_request_success_202_and_circuit_breaker(self, mock_get):
        scout.LASTFM_BLOCKED = False
        scout.DDG_BLOCKED = False
        try:
            # 1. Test 202 is treated as success
            mock_res_202 = MagicMock()
            mock_res_202.status_code = 202
            mock_get.return_value = mock_res_202

            res = scout.robust_request("GET", "https://lite.duckduckgo.com/lite/", max_retries=1)
            self.assertIsNotNone(res)
            self.assertEqual(res.status_code, 202)
            self.assertFalse(scout.DDG_BLOCKED)

            # 2. Test 406 triggers circuit breaker immediately and returns None
            mock_res_406 = MagicMock()
            mock_res_406.status_code = 406
            mock_get.return_value = mock_res_406

            res = scout.robust_request("GET", "https://www.last.fm/music/HAWXX", max_retries=1)
            self.assertIsNone(res)
            self.assertTrue(scout.LASTFM_BLOCKED)

            # 3. Test that subsequent request is skipped due to active circuit breaker
            res_skipped = scout.robust_request("GET", "https://www.last.fm/music/Alarm!+Alarm!", max_retries=1)
            self.assertIsNone(res_skipped)
        finally:
            # Reset flags to not affect other tests
            scout.LASTFM_BLOCKED = False
            scout.DDG_BLOCKED = False

    @patch('scout.requests.get')
    def test_get_monthly_listeners_parsing(self, mock_get):
        scout._monthly_listeners_cache = {}

        # Test 1: "167.3K monthly listeners"
        mock_res1 = MagicMock()
        mock_res1.status_code = 200
        mock_res1.text = '<html><meta property="og:description" content="Artist · 167.3K monthly listeners."></html>'

        # Test 2: "17.7M monthly listeners"
        mock_res2 = MagicMock()
        mock_res2.status_code = 200
        mock_res2.text = '<html><meta name="description" content="Artist · 17.7M monthly listeners."></html>'

        # Test 3: "25,431 monthly listeners"
        mock_res3 = MagicMock()
        mock_res3.status_code = 200
        mock_res3.text = '<html><meta property="og:description" content="Artist · 25,431 monthly listeners."></html>'

        mock_get.side_effect = [mock_res1, mock_res2, mock_res3]

        self.assertEqual(scout.get_monthly_listeners("artist1"), 167300)
        self.assertEqual(scout.get_monthly_listeners("artist2"), 17700000)
        self.assertEqual(scout.get_monthly_listeners("artist3"), 25431)

        # Test cache hits
        self.assertEqual(scout.get_monthly_listeners("artist1"), 167300)
        self.assertEqual(mock_get.call_count, 3) # no new request for cached artist1

    @patch('scout.get_spotify_token')
    @patch('scout.requests.get')
    def test_get_monthly_listeners_fallback_to_followers(self, mock_get, mock_get_token):
        scout._monthly_listeners_cache = {}
        mock_get_token.return_value = "mock_token"

        # Mock 1: Scrape request returns empty HTML (fails to parse listeners)
        mock_res_scrape = MagicMock()
        mock_res_scrape.status_code = 200
        mock_res_scrape.text = "<html></html>"

        # Mock 2: API request to get followers returns 200 with followers count
        mock_res_api = MagicMock()
        mock_res_api.status_code = 200
        mock_res_api.json.return_value = {
            "followers": {"total": 45000}
        }

        mock_get.side_effect = [mock_res_scrape, mock_res_api]

        listeners = scout.get_monthly_listeners("artist_fallback")
        self.assertEqual(listeners, 45000)

    @patch('scout.requests.post')
    @patch.dict('os.environ', {'SPOTIFY_REFRESH_TOKEN': 'test_refresh_token'})
    @patch('scout.SPOTIFY_CLIENT_ID', 'test_id')
    @patch('scout.SPOTIFY_CLIENT_SECRET', 'test_secret')
    def test_get_spotify_write_token(self, mock_post):
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {"access_token": "mock_write_token"}
        mock_post.return_value = mock_res

        token = scout.get_spotify_write_token()
        self.assertEqual(token, "mock_write_token")

    @patch('scout.supabase')
    def test_select_weekly_playlist_tracks(self, mock_supabase):
        # Setup mock database responses
        mock_select = MagicMock()
        mock_range = MagicMock()
        mock_execute = MagicMock()
        mock_execute.data = [{"track_id": "duplicate_track_1"}]

        mock_gte = MagicMock()
        mock_recent_execute = MagicMock()
        mock_recent_execute.data = [{"artist_id": "recent_artist_id"}]

        # Use side_effect to route based on select column to prevent overwriting mocks
        def select_side_effect(column):
            if column == "track_id":
                return mock_range
            elif column == "artist_id":
                return mock_gte
            return MagicMock()
        mock_select.select.side_effect = select_side_effect

        mock_supabase.table.return_value = mock_select
        mock_range.range.return_value = mock_execute
        mock_execute.execute.return_value = mock_execute

        mock_gte.gte.return_value = mock_recent_execute
        mock_recent_execute.execute.return_value = mock_recent_execute

        # Candidates to select from
        candidates = [
            # Major candidate (>100k listeners)
            {"track_id": "t1", "track_name": "Major Song", "artist_id": "a1", "artist_name": "Artist 1", "tier": "Major", "monthly_listeners": 150000, "release_date": "2026-02-01"},
            # Mid candidates (10k-100k listeners)
            {"track_id": "t2", "track_name": "Mid Song 1", "artist_id": "a2", "artist_name": "Artist 2", "tier": "Mid", "monthly_listeners": 50000, "release_date": "2026-02-01"},
            {"track_id": "t3", "track_name": "Mid Song 2", "artist_id": "a3", "artist_name": "Artist 3", "tier": "Mid", "monthly_listeners": 40000, "release_date": "2026-02-01"},
            # Indie candidates (1k-10k listeners)
            {"track_id": "t4", "track_name": "Indie Song 1", "artist_id": "a4", "artist_name": "Artist 4", "tier": "Indie", "monthly_listeners": 5000, "release_date": "2026-02-01"},
            {"track_id": "t5", "track_name": "Indie Song 2", "artist_id": "a5", "artist_name": "Artist 5", "tier": "Indie", "monthly_listeners": 6000, "release_date": "2026-02-01"},
            {"track_id": "t6", "track_name": "Indie Song 3", "artist_id": "a6", "artist_name": "Artist 6", "tier": "Indie", "monthly_listeners": 7000, "release_date": "2026-02-01"},
            {"track_id": "t7", "track_name": "Indie Song 4", "artist_id": "a7", "artist_name": "Artist 7", "tier": "Indie", "monthly_listeners": 8000, "release_date": "2026-02-01"},
            {"track_id": "t8", "track_name": "Indie Song 5", "artist_id": "a8", "artist_name": "Artist 8", "tier": "Indie", "monthly_listeners": 9000, "release_date": "2026-02-01"},
            # Emerging candidates (<1k listeners)
            {"track_id": "t9", "track_name": "Emerging Song 1", "artist_id": "a9", "artist_name": "Artist 9", "tier": "Emerging", "monthly_listeners": 500, "release_date": "2026-02-01"},
            {"track_id": "t10", "track_name": "Emerging Song 2", "artist_id": "a10", "artist_name": "Artist 10", "tier": "Emerging", "monthly_listeners": 600, "release_date": "2026-02-01"},
            # Duplicate / Restricted ones
            {"track_id": "duplicate_track_1", "track_name": "Dup Song", "artist_id": "a11", "artist_name": "Artist 11", "tier": "Major", "monthly_listeners": 200000, "release_date": "2026-02-01"},
            {"track_id": "t12", "track_name": "Recent Artist Song", "artist_id": "recent_artist_id", "artist_name": "Recent", "tier": "Major", "monthly_listeners": 300000, "release_date": "2026-02-01"}
        ]

        selected = scout.select_weekly_playlist_tracks(candidates)

        # Verify exactly 10 selected
        self.assertEqual(len(selected), 10)

        # Verify duplicate and recent artist were NOT selected
        selected_ids = {s["track_id"] for s in selected}
        self.assertNotIn("duplicate_track_1", selected_ids)
        self.assertNotIn("t12", selected_ids)

        # Verify exact tier distribution
        tiers_selected = [s["tier"] for s in selected]
        self.assertEqual(tiers_selected.count("Major"), 1)
        self.assertEqual(tiers_selected.count("Mid"), 2)
        self.assertEqual(tiers_selected.count("Indie"), 5)
        self.assertEqual(tiers_selected.count("Emerging"), 2)

    @patch('scout.get_spotify_write_token')
    @patch('scout.discover_punk_candidates')
    @patch('scout.select_weekly_playlist_tracks')
    @patch('scout.requests.get')
    @patch('scout.requests.delete')
    @patch('scout.requests.post')
    @patch('scout.supabase')
    def test_generate_monday_playlist_e2e(self, mock_supabase, mock_post, mock_delete, mock_get_req, mock_select_tracks, mock_discover, mock_get_token):
        mock_get_token.return_value = "write_token"

        # Mock discovered candidates & select_weekly_playlist_tracks response
        mock_discover.return_value = []
        selected_mock = [
            {"track_id": f"track_{i}", "track_name": f"Song {i}", "artist_id": f"art_{i}", "artist_name": f"Band {i}", "tier": "Indie", "monthly_listeners": 2000, "release_date": "2026-02-01"}
            for i in range(10)
        ]
        mock_select_tracks.return_value = selected_mock

        # Mock GET playlist items (includes one track to prune >84 days)
        mock_res_get = MagicMock()
        mock_res_get.status_code = 200
        mock_res_get.json.return_value = {
            "items": [
                {
                    "added_at": "2025-10-01T12:00:00Z", # Older than 84 days from 2026
                    "track": {"uri": "spotify:track:prune_1", "name": "Old Song", "artists": [{"name": "Old Band"}]}
                },
                {
                    "added_at": datetime.now().isoformat()[:19] + "Z", # Fresh track
                    "track": {"uri": "spotify:track:fresh_1", "name": "Fresh Song", "artists": [{"name": "Fresh Band"}]}
                }
            ]
        }
        mock_get_req.return_value = mock_res_get

        # Mock DELETE and POST requests
        mock_res_del = MagicMock()
        mock_res_del.status_code = 200
        mock_delete.return_value = mock_res_del

        mock_res_post = MagicMock()
        mock_res_post.status_code = 201
        mock_post.return_value = mock_res_post

        # Mock database select & inserts for history/artist integration
        mock_table = MagicMock()
        mock_supabase.table.return_value = mock_table

        # Mock the existing artists select page
        mock_select = MagicMock()
        mock_range = MagicMock()
        mock_execute = MagicMock()
        mock_execute.data = [{"name": "Band 0"}] # Band 0 already exists in DB, others are new
        mock_table.select.return_value = mock_range
        mock_range.range.return_value = mock_execute
        mock_execute.execute.return_value = mock_execute

        # Call function
        scout.generate_monday_playlist()

        # Assertions
        mock_get_token.assert_called_once()
        mock_discover.assert_called_once_with("write_token")
        mock_select_tracks.assert_called_once()

        # Check pruning was triggered with old song uri
        mock_delete.assert_called_once()
        self.assertIn("prune_1", mock_delete.call_args[1]["json"]["tracks"][0]["uri"])

        # Check new tracks were posted to position 0
        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args[1]["json"]["position"], 0)
        self.assertEqual(len(mock_post.call_args[1]["json"]["uris"]), 10)

        # Check history & integration database operations were executed
        self.assertTrue(mock_table.insert.called)

if __name__ == '__main__':
    unittest.main()
