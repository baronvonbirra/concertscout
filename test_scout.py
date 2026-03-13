import unittest
from unittest.mock import patch, MagicMock
import scout
import json
from bs4 import BeautifulSoup

class TestScout(unittest.TestCase):

    @patch('scout.requests.get')
    def test_fetch_bandsintown_events(self, mock_get):
        # Mock Bandsintown response
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "venue": {"country": "Spain", "city": "Madrid", "name": "Wizink Center"},
                "datetime": "2023-12-01T20:00:00",
                "url": "http://tickets.com"
            },
            {
                "venue": {"country": "France", "city": "Biarritz", "name": "Atabal"},
                "datetime": "2023-12-02T20:00:00",
                "url": "http://tickets.fr"
            },
            {
                "venue": {"country": "UK", "city": "London", "name": "Brixton Academy"},
                "datetime": "2023-12-03T20:00:00",
                "url": "http://tickets.uk"
            }
        ]
        mock_get.return_value = mock_response

        with patch('scout.BANDSINTOWN_APP_ID', 'test_id'):
            events = scout.fetch_bandsintown_events("Bad Religion")

        self.assertEqual(len(events), 2) # Madrid (Spain) and Biarritz (Proximity)
        self.assertEqual(events[0]['city'], "Madrid")
        self.assertFalse(events[0]['is_proximity'])
        self.assertEqual(events[1]['city'], "Biarritz")
        self.assertTrue(events[1]['is_proximity'])

    @patch('scout.fetch_bandsintown_events')
    def test_fetch_all_sources(self, mock_bit):
        mock_bit.return_value = [
            {"artist": "NOFX", "city": "Madrid", "date": "2023-12-10", "source": "Bandsintown"}
        ]

        events = scout.fetch_all_sources("NOFX")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['city'], "Madrid")

    @patch('scout.requests.get')
    @patch('scout.supabase')
    def test_linktree_sniffer_change(self, mock_supabase, mock_get):
        artist = {
            "id": 1, "name": "Refused", "priority_level": "high",
            "linktree_url": "https://linktr.ee/refused", "last_linktree_snapshot": '["Old Link"]'
        }

        mock_response = MagicMock()
        mock_response.text = '<html><a data-testid="LinkButton">New Link</a></html>'
        mock_get.return_value = mock_response

        # Mock time.sleep to speed up tests
        with patch('scout.time.sleep'):
            scout.linktree_sniffer(artist)

        mock_supabase.table().update.assert_called()
        call_args = mock_supabase.table().update.call_args[0][0]
        self.assertIn("New Link", call_args['last_linktree_snapshot'])

    @patch('scout.requests.get')
    @patch('scout.supabase')
    @patch('scout.LASTFM_API_KEY', 'test_key')
    def test_get_similar_punk_artists_cache(self, mock_supabase, mock_get):
        # Mock cached data
        mock_supabase.table().select().eq().gt().execute.return_value = MagicMock(data=[
            {"similar_artist_name": "Pennywise"}
        ])

        similar = scout.get_similar_punk_artists(1, "Bad Religion")
        self.assertEqual(similar, ["Pennywise"])
        mock_get.assert_not_called()

    @patch('scout.requests.get')
    @patch('scout.time.sleep')
    def test_scrape_songkick_city_pagination(self, mock_sleep, mock_get):
        # Mocking 2 pages of results
        # Page 1 has 2 events in 2026
        # Page 2 has 1 event in 2026 and 1 in 2027

        html_page1 = """
        <html>
            <li class="event-listings-element">
                <p class="artists"><strong>Artist 1</strong></p>
                <time datetime="2026-05-01T20:00:00"></time>
                <a class="venue-link">Venue 1</a>
                <a class="event-link" href="/concerts/1"></a>
            </li>
            <li class="event-listings-element">
                <p class="artists"><strong>Artist 2</strong></p>
                <time datetime="2026-06-01T20:00:00"></time>
                <a class="venue-link">Venue 2</a>
                <a class="event-link" href="/concerts/2"></a>
            </li>
        </html>
        """

        html_page2 = """
        <html>
            <li class="event-listings-element">
                <p class="artists"><strong>Artist 3</strong></p>
                <time datetime="2026-12-31T20:00:00"></time>
                <a class="venue-link">Venue 3</a>
                <a class="event-link" href="/concerts/3"></a>
            </li>
            <li class="event-listings-element">
                <p class="artists"><strong>Artist 4</strong></p>
                <time datetime="2027-01-01T20:00:00"></time>
                <a class="venue-link">Venue 4</a>
                <a class="event-link" href="/concerts/4"></a>
            </li>
        </html>
        """

        mock_resp1 = MagicMock()
        mock_resp1.text = html_page1
        mock_resp1.status_code = 200

        mock_resp2 = MagicMock()
        mock_resp2.text = html_page2
        mock_resp2.status_code = 200

        mock_get.side_effect = [mock_resp1, mock_resp2]

        events = scout.scrape_songkick_city("123", "Spain", "Madrid")

        # Should have 3 events (2 from page 1, 1 from page 2 before hitting 2027)
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]['artist'], "Artist 1")
        self.assertEqual(events[1]['artist'], "Artist 2")
        self.assertEqual(events[2]['artist'], "Artist 3")
        self.assertEqual(mock_get.call_count, 2)

if __name__ == '__main__':
    unittest.main()
