import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from check_public_secrets import main, private_path, scan_content, scan_repository
from cloud_config import DEFAULT_API_HOST, api_host


class PublicSecurityTests(unittest.TestCase):
    def test_provider_key_and_password_values_never_appear_in_output(self):
        token = 'sk-' + 'A' * 40
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.init(root)
            (root / 'config.txt').write_text('api_key="' + token + '"\npassword="' + 'fixture-password' + '"')
            subprocess.run(['git', '-C', str(root), 'add', 'config.txt'], check=True)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(['--root', str(root), '--staged']), 1)
            self.assertIn('credential-literal', output.getvalue())
            self.assertNotIn(token, output.getvalue())
            self.assertNotIn('fixture-password', output.getvalue())

    def test_staged_bytes_are_checked_even_if_worktree_was_cleaned(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.init(root)
            path = root / 'config.txt'
            path.write_text('sk-' + 'B' * 40)
            subprocess.run(['git', '-C', str(root), 'add', 'config.txt'], check=True)
            path.write_text('safe example')
            self.assertEqual(scan_repository(root), [])
            self.assertTrue(scan_repository(root, staged=True))

    def test_deleted_credential_is_still_found_in_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.init(root)
            path = root / 'config.txt'; path.write_text('sk-' + 'C' * 40)
            self.commit(root)
            path.write_text('safe example'); self.commit(root)
            self.assertEqual(scan_repository(root), [])
            self.assertTrue(any(name.startswith('history/') for name, _, _ in scan_repository(root, history=True)))

    def test_private_artifacts_are_blocked_but_public_assets_are_allowed(self):
        for name in ('.env.local', 'runtime/events.sqlite3', 'backups/release.bundle',
                     'private.key', 'secrets.local.json', '.ssh/id_rsa', 'capture.mp4'):
            self.assertTrue(private_path(name), name)
        for name in ('.env.example', 'docs/assets/system-overview.gif', 'tests/test_backup_runtime.py'):
            self.assertFalse(private_path(name), name)

    def test_workspace_and_camera_identifiers_are_detected(self):
        endpoint = ('https://llm-' + 'example' + '.cn-beijing.maas.aliyuncs.com').encode()
        serial = ('CPC' + 'A' * 9).encode()
        self.assertTrue(scan_content(endpoint)); self.assertTrue(scan_content(serial))
        self.assertFalse(scan_content(endpoint, credentials_only=True))

    def test_placeholder_credentials_are_allowed(self):
        self.assertEqual(scan_content(b'API_KEY="YOUR_API_KEY"\npassword="YOUR_PASSWORD"'), [])

    def test_public_or_private_origin_is_loaded_from_environment(self):
        self.assertEqual(api_host({}), DEFAULT_API_HOST)
        self.assertEqual(api_host({'LUMINA_API_HOST':' https://vision.example/ '}), 'https://vision.example')

    def test_unsafe_or_miscopied_origin_is_rejected_without_echoing_it(self):
        for value in ('http://vision.example', 'https://user:' + 'password@vision.example',
                      'https://vision.example/compatible-mode/v1', 'https://vision.example?q=1',
                      'https://vision.example#fragment', 'https://vision.example:8443', ''):
            with self.subTest(value=value), self.assertRaises(ValueError) as raised:
                api_host({'LUMINA_API_HOST':value})
            self.assertNotIn(value or 'EMPTY', str(raised.exception))

    def init(self, root):
        subprocess.run(['git', '-C', str(root), 'init', '-q'], check=True)

    def commit(self, root):
        subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(root), '-c', 'user.name=Security Test',
                        '-c', 'user.email=test@example.com', 'commit', '-qm', 'fixture'], check=True)


if __name__ == '__main__':
    unittest.main()
