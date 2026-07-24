import gzip
import unittest
from unittest.mock import MagicMock, patch

from scripts.http_client import JsonHttpClient, decode_body


class HttpClientTests(unittest.TestCase):
    def test_decodes_gzip_payload(self):
        payload = b'{"ok":true}'

        self.assertEqual(decode_body(gzip.compress(payload), "gzip"), payload)

    def test_reuses_connection_within_a_thread(self):
        response = MagicMock()
        response.status = 200
        response.will_close = False
        response.getheaders.return_value = [("Content-Encoding", "gzip")]
        response.read.side_effect = [
            gzip.compress(b'{"value":1}'),
            gzip.compress(b'{"value":2}'),
        ]
        connection = MagicMock()
        connection.getresponse.return_value = response

        with patch(
            "scripts.http_client.http.client.HTTPSConnection",
            return_value=connection,
        ) as connection_type:
            client = JsonHttpClient()
            first = client.request_json("https://example.com/one")
            second = client.request_json("https://example.com/two")

        connection_type.assert_called_once()
        self.assertEqual(connection.request.call_count, 2)
        self.assertEqual(first, {"value": 1})
        self.assertEqual(second, {"value": 2})


if __name__ == "__main__":
    unittest.main()
