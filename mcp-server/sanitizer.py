"""Sanitizer module for zero-leak security in AI-BRAIN."""

from __future__ import annotations

import re
from typing import Any

# Pre-compiled regex patterns for secret detection and masking
SECRET_PATTERNS = [
    # 1. Private Keys (RSA, EC, OPENSSH, etc.)
    (
        re.compile(r"-----BEGIN [A-Z\s]+PRIVATE KEY-----[\s\S]*?-----END [A-Z\s]+PRIVATE KEY-----"),
        "[REDACTED_PRIVATE_KEY]",
    ),
    # 2. JWT Tokens (header.payload.signature)
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{10,}\b"),
        "[REDACTED_JWT]",
    ),
    # 3. OpenAI & Anthropic API Keys (sk-proj-..., sk-ant-..., sk-...)
    (
        re.compile(r"\bsk-(?:proj|ant)?[a-zA-Z0-9_\-]{20,}\b"),
        "[REDACTED_API_KEY]",
    ),
    # 4. GitHub Tokens (Personal, Fine-grained, OAuth, App)
    (
        re.compile(r"\bgithub_pat_[a-zA-Z0-9_]{40,}\b"),
        "[REDACTED_GITHUB_TOKEN]",
    ),
    (
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{30,}\b"),
        "[REDACTED_GITHUB_TOKEN]",
    ),
    # 5. GitLab Tokens (glpat-...)
    (
        re.compile(r"\bglpat-[a-zA-Z0-9_\-]{20,}\b"),
        "[REDACTED_GITLAB_TOKEN]",
    ),
    # 6. Slack Tokens (xoxb-, xoxp-, etc.)
    (
        re.compile(r"\bxox[baprs]-[a-zA-Z0-9\-]{20,}\b"),
        "[REDACTED_SLACK_TOKEN]",
    ),
    # 7. Google API Keys (AIzaSy...)
    (
        re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
        "[REDACTED_API_KEY]",
    ),
    # 8. Bearer Authorization Tokens
    (
        re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9\-_.~+/=]{15,}\b"),
        r"\g<1>[REDACTED_BEARER]",
    ),
    # 9. Password assignments in configs/env (e.g., password="...", pwd: '...')
    (
        re.compile(
            r"(?i)\b((?:password|passwd|pwd|database_pass|db_pass|secret_key|api_secret)\s*[:=]\s*[\"'])([^\"'\r\n]+)([\"'])"
        ),
        r"\g<1>[REDACTED_PASSWORD]\g<3>",
    ),
    (
        re.compile(
            r"(?i)\b((?:password|passwd|pwd)\s*[:=]\s*)(?!\[REDACTED)[^\s,;\"'\r\n]+"
        ),
        r"\g<1>[REDACTED_PASSWORD]",
    ),
    # 10. Passwords in URLs (e.g., postgresql://user:pass@host:5432)
    (
        re.compile(r"(?i)(https?|postgresql|postgres|mysql|mongodb)://([^:\s/@]+):([^@\s/]+)@"),
        r"\g<1>://\g<2>:[REDACTED_PASSWORD]@",
    ),
    # 11. PII - Credit Card Numbers (13 to 16 digits formatted or raw)
    (
        re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b"),
        "[REDACTED_PII]",
    ),
]


class Sanitizer:
    """Deterministic sanitizer for secrets, API tokens, passwords and PII."""

    @classmethod
    def clean(cls, text: Any) -> str:
        """Sanitizes text by replacing any discovered secrets with redaction markers.

        Args:
            text: Input string (or any object convertible to string).

        Returns:
            Sanitized text with zero sensitive tokens.
        """
        if text is None:
            return ""

        content = str(text)
        if not content:
            return ""

        for pattern, replacement in SECRET_PATTERNS:
            content = pattern.sub(replacement, content)

        return content

    @classmethod
    def has_secrets(cls, text: Any) -> bool:
        """Checks whether the given text contains any detectable secrets.

        Args:
            text: Input text to verify.

        Returns:
            True if one or more secrets were detected, False otherwise.
        """
        if text is None:
            return False
        content = str(text)
        if not content:
            return False

        for pattern, _ in SECRET_PATTERNS:
            if pattern.search(content):
                return True
        return False
