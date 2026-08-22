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

        scout.sweep_past_concerts()

        mock_supabase.table.assert_called_once_with("concerts")
        mock_delete.delete.assert_called_once()
        mock_lt.lt.assert_called_once()

    @patch('scout.supabase')
    def test_select_weekly_playlist_tracks_phase2_rules(self, mock_supabase):
        # Mock band_registry
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_execute = MagicMock()

        mock_execute.data = [
            {"band_name": "Used Recently Band", "last_used_in_playlist": "W30", "ever_featured_in_top50": False},
            {"band_name": "Old Band", "last_used_in_playlist": "W15", "ever_featured_in_top50": True}
        ]
        mock_supabase.table.return_value = mock_table
        mock_table.select.return_value = mock_execute
        mock_execute.execute.return_value = mock_execute

        candidates = [
            {"track_id": "t1", "track_name": "Song 1", "artist_id": "a1", "artist_name": "Used Recently Band", "monthly_listeners": 5000, "release_date": "2026-02-01"},
            {"track_id": "t2", "track_name": "Song 2", "artist_id": "a2", "artist_name": "Fresh Band 1", "monthly_listeners": 12000, "release_date": "2026-02-01"},
            {"track_id": "t3", "track_name": "Song 3", "artist_id": "a3", "artist_name": "Old Band", "monthly_listeners": 45000, "release_date": "2026-02-01"},
        ]

        # W33 - W30 = 3 <= 10 -> "Used Recently Band" excluded
        # "Old Band" last used W15 (33-15 = 18 > 10) -> included
        selected = scout.select_weekly_playlist_tracks(candidates, current_week="W33")

        selected_names = [s["artist_name"] for s in selected]
        self.assertNotIn("Used Recently Band", selected_names)
        self.assertIn("Fresh Band 1", selected_names)
        self.assertIn("Old Band", selected_names)

    @patch('scout.supabase')
    @patch('scout.scrape_lastfm_artist_events')
    def test_track_lastfm_concerts_phase3(self, mock_scrape, mock_supabase):
        # Mock database returns for band sources
        mock_table = MagicMock()
        mock_execute_ws = MagicMock(data=[{"band_name": "Punk Act 1"}])
        mock_execute_br = MagicMock(data=[{"band_name": "Punk Act 2"}])
        mock_execute_art = MagicMock(data=[{"name": "Punk Act 3"}])

        def table_side_effect(name):
            m = MagicMock()
            if name == "weekly_submissions":
                m.select.return_value.execute.return_value = mock_execute_ws
            elif name == "band_registry":
                m.select.return_value.execute.return_value = mock_execute_br
            elif name == "artists":
                m.select.return_value.execute.return_value = mock_execute_art
            elif name == "tour_events":
                m.upsert.return_value.execute.return_value = MagicMock()
            return m

        mock_supabase.table.side_effect = table_side_effect

        future_date = (datetime.now().date()).isoformat()
        mock_scrape.return_value = [
            {"event_name": "Gig", "city": "Madrid", "venue": "Wurlitzer", "event_date": future_date, "ticket_url": "http://last.fm/event", "country": "Spain"}
        ]

        scout.track_lastfm_concerts()

        self.assertTrue(mock_scrape.called)

    def test_calculate_momentum_score(self):
        # wow=20%, mom=30%, tot=100% -> (20*0.5) + (30*0.3) + (10*0.2) = 10 + 9 + 2 = 21
        score = scout.calculate_momentum_score(20, 30, 100)
        self.assertEqual(score, 21)

        # Capped at 100
        score_high = scout.calculate_momentum_score(300, 200, 1000)
        self.assertEqual(score_high, 100)

        # Floor at 0
        score_low = scout.calculate_momentum_score(-50, -50, -100)
        self.assertEqual(score_low, 0)

    def test_determine_trajectory(self):
        snaps_explosive = [
            {"listener_count": 150000, "recorded_date": "2026-02-28"},
            {"listener_count": 130000, "recorded_date": "2026-02-21"},
            {"listener_count": 120000, "recorded_date": "2026-02-14"},
            {"listener_count": 110000, "recorded_date": "2026-02-07"},
            {"listener_count": 100000, "recorded_date": "2026-01-31"}
        ]
        # 150000 > 100000 * 1.3 (130000) -> explosive
        self.assertEqual(scout.determine_trajectory(snaps_explosive), "explosive")

        snaps_steady = [
            {"listener_count": 110000, "recorded_date": "2026-02-28"},
            {"listener_count": 108000, "recorded_date": "2026-02-21"},
            {"listener_count": 105000, "recorded_date": "2026-02-14"},
            {"listener_count": 102000, "recorded_date": "2026-02-07"},
            {"listener_count": 100000, "recorded_date": "2026-01-31"}
        ]
        self.assertEqual(scout.determine_trajectory(snaps_steady), "steady")

        snaps_declining = [
            {"listener_count": 50000, "recorded_date": "2026-02-28"},
            {"listener_count": 60000, "recorded_date": "2026-02-21"},
            {"listener_count": 70000, "recorded_date": "2026-02-14"},
            {"listener_count": 80000, "recorded_date": "2026-02-07"},
            {"listener_count": 100000, "recorded_date": "2026-01-31"}
        ]
        self.assertEqual(scout.determine_trajectory(snaps_declining), "declining")

    @patch('scout.supabase')
    def test_recalculate_analytics_summary_share_tracking_and_lift(self, mock_supabase):
        mock_table = MagicMock()

        # Mock weekly_submissions data
        mock_ws_exec = MagicMock(data=[
            {"band_name": "Ellen May", "week": "W8", "shared": True, "created_at": "2026-02-01T00:00:00"},
            {"band_name": "Ellen May", "week": "W10", "shared": True, "created_at": "2026-02-15T00:00:00"},
            {"band_name": "Unshared Band", "week": "W8", "shared": False, "created_at": "2026-02-01T00:00:00"}
        ])

        # Mock playlist_history data
        mock_ph_exec = MagicMock(data=[])

        # Mock listener snapshots
        mock_snap_exec = MagicMock(data=[
            # Ellen May W8 (10,000) -> W9 (12,000): Lift = +20%
            {"band_name": "Ellen May", "listener_count": 10000, "snapshot_week": "W8", "recorded_date": "2026-02-01"},
            {"band_name": "Ellen May", "listener_count": 12000, "snapshot_week": "W9", "recorded_date": "2026-02-08"},
            # Ellen May W10 (15,000) -> W11 (18,000): Lift = +20%
            {"band_name": "Ellen May", "listener_count": 15000, "snapshot_week": "W10", "recorded_date": "2026-02-15"},
            {"band_name": "Ellen May", "listener_count": 18000, "snapshot_week": "W11", "recorded_date": "2026-02-22"},
            # Unshared Band
            {"band_name": "Unshared Band", "listener_count": 5000, "snapshot_week": "W8", "recorded_date": "2026-02-01"},
            {"band_name": "Unshared Band", "listener_count": 5200, "snapshot_week": "W9", "recorded_date": "2026-02-08"}
        ])

        upserted_records = []

        def table_side_effect(name):
            m = MagicMock()
            if name == "weekly_submissions":
                m.select.return_value.execute.return_value = mock_ws_exec
            elif name == "playlist_history":
                m.select.return_value.execute.return_value = mock_ph_exec
            elif name == "band_listener_snapshot":
                m.select.return_value.range.return_value.order.return_value.execute.return_value = mock_snap_exec
            elif name == "band_analytics_summary":
                def mock_upsert(record, on_conflict=None):
                    upserted_records.append(record)
                    return MagicMock()
                m.upsert.side_effect = mock_upsert
            return m

        mock_supabase.table.side_effect = table_side_effect

        scout.recalculate_analytics_summary()

        # Find Ellen May summary
        ellen_record = next((r for r in upserted_records if r["band_name"] == "Ellen May"), None)
        self.assertIsNotNone(ellen_record)
        self.assertEqual(ellen_record["total_shares"], 2)
        self.assertTrue(ellen_record["was_shared"])
        self.assertEqual(ellen_record["last_shared_week"], "W10")
        self.assertEqual(ellen_record["listener_count_at_share"], 15000)
        self.assertEqual(ellen_record["listener_count_1week_after_share"], 18000)
        self.assertEqual(ellen_record["share_lift_pct"], 20.00)
        self.assertEqual(ellen_record["share_lift_absolute"], 3000)
        self.assertEqual(ellen_record["avg_growth_after_share_pct"], 20.00)

        # Find Unshared Band summary
        unshared_record = next((r for r in upserted_records if r["band_name"] == "Unshared Band"), None)
        self.assertIsNotNone(unshared_record)
        self.assertEqual(unshared_record["total_shares"], 0)
        self.assertFalse(unshared_record["was_shared"])
        self.assertIsNone(unshared_record["last_shared_week"])

    @patch('scout.supabase')
    def test_recalculate_analytics_summary_with_none_values(self, mock_supabase):
        mock_table = MagicMock()
        mock_ws_exec = MagicMock(data=[])
        mock_ph_exec = MagicMock(data=[])
        mock_snap_exec = MagicMock(data=[
            {"band_name": "Null Band", "listener_count": None, "snapshot_week": "W1", "recorded_date": "2026-02-01"},
            {"band_name": "Null Band", "listener_count": None, "snapshot_week": "W2", "recorded_date": "2026-02-08"}
        ])

        upserted_records = []

        def table_side_effect(name):
            m = MagicMock()
            if name == "weekly_submissions":
                m.select.return_value.execute.return_value = mock_ws_exec
            elif name == "playlist_history":
                m.select.return_value.execute.return_value = mock_ph_exec
            elif name == "band_listener_snapshot":
                m.select.return_value.range.return_value.order.return_value.execute.return_value = mock_snap_exec
            elif name == "band_analytics_summary":
                def mock_upsert(record, on_conflict=None):
                    upserted_records.append(record)
                    return MagicMock()
                m.upsert.side_effect = mock_upsert
            return m

        mock_supabase.table.side_effect = table_side_effect

        # Should execute without throwing TypeError
        scout.recalculate_analytics_summary()
        self.assertEqual(len(upserted_records), 1)
        self.assertEqual(upserted_records[0]["latest_listener_count"], 0)

    @patch('scout.supabase')
    @patch('scout.get_monthly_listeners')
    def test_take_band_listener_snapshots_upsert(self, mock_listeners, mock_supabase):
        mock_listeners.return_value = 5000

        mock_ws_exec = MagicMock(data=[{"band_name": "Swayze"}])
        mock_br_exec = MagicMock(data=[])
        mock_ph_exec = MagicMock(data=[])
        mock_art_exec = MagicMock(data=[])

        upsert_calls = []

        def table_side_effect(name):
            m = MagicMock()
            if name == "weekly_submissions":
                m.select.return_value.execute.return_value = mock_ws_exec
            elif name == "band_registry":
                m.select.return_value.execute.return_value = mock_br_exec
            elif name == "playlist_history":
                m.select.return_value.execute.return_value = mock_ph_exec
            elif name == "artists":
                m.select.return_value.eq.return_value.execute.return_value = mock_art_exec
            elif name == "band_listener_snapshot":
                def mock_upsert(payload, on_conflict=None):
                    upsert_calls.append((payload, on_conflict))
                    return MagicMock()
                m.upsert.side_effect = mock_upsert
            return m

        mock_supabase.table.side_effect = table_side_effect

        with patch('scout.search_spotify_artist', return_value=('spot_123', 100)):
            scout.take_band_listener_snapshots()

        self.assertEqual(len(upsert_calls), 1)
        payload, on_conflict = upsert_calls[0]
        self.assertEqual(payload["band_name"], "Swayze")
        self.assertEqual(on_conflict, "band_name,recorded_date")

    @patch('scout.requests.get')
    def test_get_monthly_listeners_bot_user_agent(self, mock_get):
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.text = '<html><head><meta property="og:description" content="Artist · 121.1K monthly listeners."/></head></html>'
        mock_get.return_value = mock_res

        scout._monthly_listeners_cache.clear()
        count = scout.get_monthly_listeners("test_artist_id_123")
        self.assertEqual(count, 121100)

    @patch('scout.supabase')
    def test_select_weekly_playlist_tracks_relaxation(self, mock_supabase):
        # Test that when candidate pool has >= 10 candidates and strict 10-week check excludes too many,
        # the relaxation ladder lowers exclusion criteria to select 10 tracks.
        mock_table = MagicMock()
        mock_execute = MagicMock()

        today_week_num = datetime.now().isocalendar()[1]
        last_week_str = f"W{today_week_num - 2}"

        # All 12 bands were used 2 weeks ago (excluded by 10-week rule)
        mock_execute.data = [
            {"band_name": f"Band {i}", "last_used_in_playlist": last_week_str, "ever_featured_in_top50": False}
            for i in range(1, 13)
        ]
        mock_supabase.table.return_value = mock_table
        mock_table.select.return_value = mock_execute
        mock_execute.execute.return_value = mock_execute

        candidates = [
            {
                "track_id": f"t{i}",
                "track_name": f"Song {i}",
                "artist_id": f"a{i}",
                "artist_name": f"Band {i}",
                "monthly_listeners": 5000,
                "release_date": (datetime.now().date()).isoformat()
            }
            for i in range(1, 13)
        ]

        selected = scout.select_weekly_playlist_tracks(candidates, current_week=f"W{today_week_num}")
        self.assertEqual(len(selected), 10)

    @patch('scout.get_monthly_listeners', return_value=5000)
    @patch('scout.requests.get')
    def test_discover_punk_candidates_search(self, mock_get, mock_listeners):
        mock_res = MagicMock()
        mock_res.status_code = 200
        today_str = datetime.now().date().isoformat()
        mock_res.json.return_value = {
            "tracks": {
                "items": [
                    {
                        "id": "tr_101",
                        "name": "Punk Anthem",
                        "album": {"name": "Punk Album", "release_date": today_str},
                        "artists": [{"id": "art_101", "name": "The Distillers"}]
                    }
                ]
            },
            "albums": {"items": []}
        }
        mock_get.return_value = mock_res

        candidates = scout.discover_punk_candidates("mock_token")
        self.assertTrue(len(candidates) >= 1)
        self.assertEqual(candidates[0]["track_id"], "tr_101")
        self.assertEqual(candidates[0]["artist_name"], "The Distillers")

    @patch('scout.supabase')
    @patch('scout.requests.post')
    @patch('scout.requests.get')
    @patch('scout.discover_punk_candidates')
    @patch('scout.get_spotify_write_token')
    def test_generate_monday_playlist_progressive_window_expansion(self, mock_write_token, mock_discover, mock_get, mock_post, mock_supabase):
        mock_write_token.return_value = "mock_write_token"

        # Mock playlist tracks GET
        mock_get_res = MagicMock()
        mock_get_res.status_code = 200
        mock_get_res.json.return_value = {"items": []}
        mock_get.return_value = mock_get_res

        # Mock post for adding tracks
        mock_post_res = MagicMock()
        mock_post_res.status_code = 201
        mock_post.return_value = mock_post_res

        # Mock band registry DB call
        mock_table = MagicMock()
        mock_supabase.table.return_value = mock_table
        mock_table.select.return_value.execute.return_value = MagicMock(data=[])

        today_str = datetime.now().date().isoformat()

        # Window 7 days returns 3 tracks, window 14 returns 10 tracks
        candidates_7d = [
            {"track_id": f"t{i}", "track_name": f"Song {i}", "artist_id": f"a{i}", "artist_name": f"Band {i}", "monthly_listeners": 5000, "release_date": today_str, "tier": "Indie"}
            for i in range(1, 4)
        ]
        candidates_14d = [
            {"track_id": f"t{i}", "track_name": f"Song {i}", "artist_id": f"a{i}", "artist_name": f"Band {i}", "monthly_listeners": 5000, "release_date": today_str, "tier": "Indie"}
            for i in range(1, 11)
        ]

        def discover_side_effect(token, window_days=7, existing_candidates=None):
            if window_days == 7:
                return candidates_7d
            return candidates_14d

        mock_discover.side_effect = discover_side_effect

        scout.generate_monday_playlist()

        # Check discover_punk_candidates was called twice (for 7 days then 14 days)
        self.assertEqual(mock_discover.call_count, 2)
        mock_discover.assert_any_call("mock_write_token", window_days=7, existing_candidates=[])
        mock_discover.assert_any_call("mock_write_token", window_days=14, existing_candidates=candidates_7d)

if __name__ == '__main__':
    unittest.main()
