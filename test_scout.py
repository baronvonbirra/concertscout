import unittest
from unittest.mock import patch, MagicMock
import scout

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
                "venue": {"country": "France", "city": "Paris", "name": "Zenith"},
                "datetime": "2023-12-02T20:00:00",
                "url": "http://tickets.fr"
            }
        ]
        mock_get.return_value = mock_response

        with patch('scout.BANDSINTOWN_APP_ID', 'test_id'):
            events = scout.fetch_bandsintown_events("Bad Religion")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['city'], "Madrid")
        self.assertEqual(events[0]['artist'], "Bad Religion")

    @patch('scout.requests.get')
    def test_get_similar_punk_artists(self, mock_get):
        # Mock Last.fm responses
        def side_effect(url, params=None):
            mock_res = MagicMock()
            if params.get('method') == 'artist.getsimilar':
                mock_res.json.return_value = {
                    "similarartists": {
                        "artist": [{"name": "NOFX"}, {"name": "Taylor Swift"}]
                    }
                }
            elif params.get('method') == 'artist.gettoptags':
                if params.get('artist') == 'NOFX':
                    mock_res.json.return_value = {
                        "toptags": {"tag": [{"name": "punk"}, {"name": "hardcore"}]}
                    }
                else:
                    mock_res.json.return_value = {
                        "toptags": {"tag": [{"name": "pop"}]}
                    }
            return mock_res

        mock_get.side_effect = side_effect

        with patch('scout.LASTFM_API_KEY', 'test_key'):
            similar = scout.get_similar_punk_artists("Bad Religion")

        self.assertIn("NOFX", similar)
        self.assertNotIn("Taylor Swift", similar)

if __name__ == '__main__':
    unittest.main()
