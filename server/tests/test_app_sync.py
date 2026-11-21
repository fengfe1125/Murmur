import io
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from murmur.app_store import AppStore, Conflict, NotFound
from murmur.app_sync import HistorySync


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AppStore(Path(self.tmp.name) / "app.db")
        self.sync = HistorySync(self.store)
        self.users = []
        for key in ("one", "two"):
            identity = self.store.redeem_invite(
                code=self.store.create_invite(),
                key_id=key,
                public_key=None,
                receipt=None,
                counter=0,
                environment="development",
            )
            self.users.append(identity.user_id)
        self.user = self.users[0]
        self.record = {
            "id": "stable",
            "text": "hello",
            "author": "you",
            "sent_at": "2026-09-21T12:00:00Z",
        }

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_opt_in_duplicate_conflict_and_account_isolation(self):
        with self.assertRaises(Conflict):
            self.sync.upload(self.user, "chat", 0, [self.record])
        self.sync.settings(self.user, True)
        first = self.sync.upload(self.user, "chat", 0, [self.record])
        self.assertEqual(self.sync.upload(self.user, "chat", 0, [self.record]), first)
        with self.assertRaises(Conflict):
            self.sync.upload(self.user, "chat", 0, [dict(self.record, text="changed")])
        self.sync.settings(self.users[1], True)
        self.assertEqual(self.sync.pull(self.users[1], "chat")["records"], [])
        self.sync.settings(self.user, False)
        with self.assertRaises(Conflict):
            self.sync.pull(self.user, "chat")
        self.sync.settings(self.user, True)
        self.assertEqual(len(self.sync.pull(self.user, "chat")["records"]), 1)

    def test_delete_barrier_and_replayed_delete_preserves_new_records(self):
        self.sync.settings(self.user, True)
        self.sync.upload(self.user, "chat", 0, [self.record])
        self.sync.upload(self.user, "archive", 0, [self.record])
        self.sync.clear(self.user, "chat", 0)
        with self.assertRaises(Conflict):
            self.sync.upload(self.user, "chat", 0, [self.record])
        self.sync.upload(self.user, "chat", 1, [dict(self.record, id="new")])
        self.sync.clear(self.user, "chat", 0)
        self.assertEqual(self.sync.pull(self.user, "chat")["records"][0]["record"]["id"], "new")
        self.assertEqual(len(self.sync.pull(self.user, "archive")["records"]), 1)

    def test_incremental_pagination_and_preview_removes_exif(self):
        self.sync.settings(self.user, True)
        for n in range(3):
            self.sync.upload(self.user, "chat", 0, [dict(self.record, id=str(n))])
        first = self.sync.pull(self.user, "chat", limit=2)
        self.assertTrue(first["has_more"])
        self.assertEqual(
            len(self.sync.pull(self.user, "chat", cursor=first["cursor"])["records"]), 1
        )
        picture = Image.new("RGB", (30, 20))
        exif = Image.Exif()
        exif[270] = "private location"
        data = io.BytesIO()
        picture.save(data, format="JPEG", exif=exif)
        self.sync.upload_preview(self.user, "chat", 0, "0", data.getvalue())
        result = self.sync.preview(self.user, "chat", "0")
        self.assertEqual(dict(Image.open(io.BytesIO(result)).getexif()), {})
        self.sync.settings(self.users[1], True)
        with self.assertRaises(NotFound):
            self.sync.preview(self.users[1], "chat", "0")
        self.sync.settings(self.user, False)
        with self.assertRaises(Conflict):
            self.sync.preview(self.user, "chat", "0")
        self.sync.settings(self.user, True)
        self.store.erase_user(self.user)
        self.assertEqual(
            self.store.conn.execute("SELECT COUNT(*) FROM app_sync_records").fetchone()[0], 0
        )

    def test_rejects_private_metadata_and_drafts(self):
        self.sync.settings(self.user, True)
        for field in ("latitude", "credential", "image_path", "delivery"):
            with self.assertRaises(ValueError):
                self.sync.upload(self.user, "chat", 0, [dict(self.record, **{field: "private"})])


if __name__ == "__main__":
    unittest.main()
