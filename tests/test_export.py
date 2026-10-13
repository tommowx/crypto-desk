import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import app


class ExportAllTests(unittest.TestCase):
    def test_archive_contains_portable_state_and_deep_checkpoint_without_token(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / 'deep_research' / 'run-1'
            run.mkdir(parents=True)
            (root / 'history_binance').mkdir()
            (root / 'portfolio.json').write_text('{"cash": 1000}', encoding='utf-8')
            (root / 'history_binance' / 'BTC.zip').write_bytes(b'historical data')
            (run / 'manifest.json').write_text('{}', encoding='utf-8')
            (run / 'report.json').write_text('{}', encoding='utf-8')
            db_path = run / 'checkpoint.sqlite3'
            db = sqlite3.connect(db_path)
            try:
                db.execute('CREATE TABLE sample (value TEXT)')
                db.execute("INSERT INTO sample VALUES ('saved work')")
                db.commit()
            finally:
                db.close()
            state = {'token': 'secret-session-token', 'version': '0.8.2', 'portfolio': {'cash': 1000}}
            with patch.object(app, 'DATA', root), patch.object(app, 'snapshot', return_value=state):
                payload = app.export_all()
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                names = set(archive.namelist())
                self.assertIn('Crypto_Desk/stato.json', names)
                self.assertIn('Crypto_Desk/portfolio.json', names)
                self.assertIn('Crypto_Desk/dati/history_binance/BTC.zip', names)
                checkpoint = 'Crypto_Desk/dati/deep_research/run-1/checkpoint.sqlite3'
                self.assertIn(checkpoint, names)
                exported_state = json.loads(archive.read('Crypto_Desk/stato.json'))
                self.assertNotIn('token', exported_state)
                self.assertNotIn(b'secret-session-token', payload)
                with tempfile.TemporaryDirectory() as exported:
                    saved = Path(exported) / 'checkpoint.sqlite3'
                    saved.write_bytes(archive.read(checkpoint))
                    db = sqlite3.connect(saved)
                    try:
                        self.assertEqual(db.execute('SELECT value FROM sample').fetchone()[0], 'saved work')
                    finally:
                        db.close()


if __name__ == '__main__':
    unittest.main()
