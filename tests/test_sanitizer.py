import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "mcp-server"))

from sanitizer import Sanitizer


class TestSanitizer(unittest.TestCase):

    def test_clean_openai_api_key(self):
        sample = "Here is my key: sk-proj-abc123XYZ45678901234567890abcdef and older sk-1234567890abcdef1234567890abcdef."
        cleaned = Sanitizer.clean(sample)
        self.assertNotIn("sk-proj-abc123XYZ45678901234567890abcdef", cleaned)
        self.assertNotIn("sk-1234567890abcdef1234567890abcdef", cleaned)
        self.assertIn("[REDACTED_API_KEY]", cleaned)

    def test_clean_github_tokens(self):
        sample = "Token is ghp_1234567890abcdefghijklmnopqrstuvwxyz and fine-grained github_pat_11ABCD1234567890abcdefghijklmnopqrstuvwxyz1234567890abcdefghij."
        cleaned = Sanitizer.clean(sample)
        self.assertNotIn("ghp_1234567890abcdefghijklmnopqrstuvwxyz", cleaned)
        self.assertNotIn("github_pat_11ABCD", cleaned)
        self.assertIn("[REDACTED_GITHUB_TOKEN]", cleaned)

    def test_clean_gitlab_and_slack_tokens(self):
        slack_token = "xoxb-" + "123456789012-1234567890123-abcdefghijklmnopqrstuvwx"
        sample = f"Gitlab glpat-1234567890abcdefghij and Slack {slack_token}"
        cleaned = Sanitizer.clean(sample)
        self.assertNotIn("glpat-1234567890abcdefghij", cleaned)
        self.assertNotIn(slack_token, cleaned)
        self.assertIn("[REDACTED_GITLAB_TOKEN]", cleaned)
        self.assertIn("[REDACTED_SLACK_TOKEN]", cleaned)

    def test_clean_jwt_token(self):
        jwt_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        sample = f"Authorization header with token: {jwt_token}"
        cleaned = Sanitizer.clean(sample)
        self.assertNotIn(jwt_token, cleaned)
        self.assertIn("[REDACTED_JWT]", cleaned)

    def test_clean_bearer_token(self):
        sample = "Authorization: Bearer secret_bearer_token_xyz_123456789"
        cleaned = Sanitizer.clean(sample)
        self.assertNotIn("secret_bearer_token_xyz_123456789", cleaned)
        self.assertIn("[REDACTED_BEARER]", cleaned)

    def test_clean_password_assignments(self):
        sample = 'database_pass: "MySecretP@ssw0rd!"\npassword = "another_secret"\npwd="super_hidden"'
        cleaned = Sanitizer.clean(sample)
        self.assertNotIn("MySecretP@ssw0rd!", cleaned)
        self.assertNotIn("another_secret", cleaned)
        self.assertNotIn("super_hidden", cleaned)
        self.assertIn("[REDACTED_PASSWORD]", cleaned)

    def test_clean_pii_credit_card(self):
        sample = "Card number: 4532-1234-5678-9012"
        cleaned = Sanitizer.clean(sample)
        self.assertNotIn("4532-1234-5678-9012", cleaned)
        self.assertIn("[REDACTED_PII]", cleaned)

    def test_clean_safe_text_preservation(self):
        sample = "# Architettura SQLite WAL\nIl database usa FTS5 con token porter e unicode61."
        cleaned = Sanitizer.clean(sample)
        self.assertEqual(sample, cleaned)

    def test_has_secrets(self):
        self.assertTrue(Sanitizer.has_secrets("sk-proj-1234567890abcdef1234567890abcdef"))
        self.assertTrue(Sanitizer.has_secrets("ghp_1234567890abcdefghijklmnopqrstuvwxyz"))
        self.assertFalse(Sanitizer.has_secrets("Testo completamente privo di segreti."))

    def test_edge_cases(self):
        self.assertEqual(Sanitizer.clean(""), "")
        self.assertEqual(Sanitizer.clean(None), "")


if __name__ == "__main__":
    unittest.main()
