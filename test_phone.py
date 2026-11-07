"""Phone address checks without network access."""
import unittest
from unittest.mock import patch
import server


class PhoneChecks(unittest.TestCase):
    def test_local_and_ip_fallback(self):
        with patch.object(server.socket, 'gethostname', return_value='test-computer.example'), patch.object(server.socket, 'gethostbyname', return_value='10.0.0.2') as resolve:
            self.assertEqual(server.phone_address('10.0.0.2'), f'http://test-computer.local:{server.PORT}')
            resolve.assert_called_once_with('test-computer.local')
        with patch.object(server.socket, 'gethostbyname', side_effect=OSError):
            self.assertEqual(server.phone_address('10.0.0.2'), f'http://10.0.0.2:{server.PORT}')
