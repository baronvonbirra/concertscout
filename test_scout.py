import unittest
from unittest.mock import patch, MagicMock
import scout
import json

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

if __name__ == '__main__':
    unittest.main()
