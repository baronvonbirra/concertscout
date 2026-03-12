import unittest
from unittest.mock import patch, MagicMock
import sys

# Mock supabase BEFORE importing scout to avoid initialization error
mock_supabase_lib = MagicMock()
sys.modules['supabase'] = mock_supabase_lib

import scout
import json

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

if __name__ == '__main__':
    unittest.main()
