import tempfile
import unittest
from pathlib import Path

from app.core.credentials import BrokerCredentials, CredentialStore, mask_secret
from app.core.database import Database
from app.services.kis_client import KISClient


class BrokerCredentialTests(unittest.TestCase):
    def test_mask_secret_keeps_edges_only(self):
        self.assertEqual(mask_secret("abcdef123456"), "abcd...3456")
        self.assertEqual(mask_secret("short"), "***")

    def test_credentials_are_encrypted_at_rest_and_loaded(self):
        with tempfile.TemporaryDirectory() as td:
            db = Database(Path(td) / "autotrade.db")
            store = CredentialStore(db, key_path=Path(td) / "secret.key")
            creds = BrokerCredentials(
                appkey="appkey-123456",
                appsecret="secret-abcdef",
                account_no="12345678-01",
                base_url="https://example.test",
                is_paper=False,
            )

            store.save(creds)

            raw = db.get_engine_state("broker_credentials")
            self.assertIsNotNone(raw)
            self.assertNotIn("secret-abcdef", raw or "")
            self.assertNotIn("appkey-123456", raw or "")

            loaded = store.load()
            self.assertEqual(loaded.appkey, "appkey-123456")
            self.assertEqual(loaded.appsecret, "secret-abcdef")
            self.assertEqual(loaded.account_no, "12345678-01")
            self.assertFalse(loaded.is_paper)

    def test_kis_client_prefers_injected_credentials(self):
        creds = BrokerCredentials(
            appkey="stored-appkey",
            appsecret="stored-secret",
            account_no="87654321-01",
            base_url="https://stored.example",
            is_paper=True,
        )

        client = KISClient(dry_run=True, credentials=creds)

        self.assertEqual(client.appkey, "stored-appkey")
        self.assertEqual(client.appsecret, "stored-secret")
        self.assertEqual(client.account_no, "87654321-01")
        self.assertEqual(client.base_url, "https://stored.example")


if __name__ == "__main__":
    unittest.main()
