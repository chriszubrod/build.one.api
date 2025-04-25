import bleach
import json
import logging
import re
import secrets
from datetime import datetime, timedelta
from typing import Optional

def is_valid_email(email):
    """
    Checks if an email is valid. Returns True if valid, False otherwise.
    """
    email_regex = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
    return re.match(email_regex, email) is not None


def read_profile_secrets(url):
    with open(url, 'r', encoding='utf-8') as f:
        profile_secrets = json.loads(f.read())
    return profile_secrets


def write_profile_secrets(url, secrets):
    with open(url, 'w', encoding='utf-8') as wf:
        json.dump(secrets, wf, ensure_ascii=False, indent=4)


def clean_text_for_db(text: str) -> str:
    """
    Cleans text for database insertion.
    """
    if not text:
        return ""

    # First pass: Basic bleach clean to remove any HTML/malicious content
    cleaned = bleach.clean(text, tags=[], strip=True)  # Remove all HTML tags

    # Replace common problematic characters
    replacements = {
        # Newlines/Whitespace
        '\n': ' ',
        '\r': ' ',
        '\t': ' ',
        '\f': ' ',  # form feed
        '\v': ' ',  # vertical tab

        # Zero-width spaces and other invisible characters
        '\u200b': '',  # zero-width space
        '\u200c': '',  # zero-width non-joiner
        '\u200d': '',  # zero-width joiner
        '\ufeff': '',  # zero-width no-break space

        # Control characters
        '\x00': '',   # null character
        '\x0b': ' ',  # vertical tab
        '\x0c': ' ',  # form feed
        '\x1c': '',   # file separator
        '\x1d': '',   # group separator
        '\x1e': '',   # record separator
        '\x1f': '',   # unit separator

        # Quotes and apostrophes (removed duplicates)
        '\u201c': '"',  # left double quotation mark
        '\u201d': '"',  # right double quotation mark
        '\u2018': "'",  # left single quotation mark
        '\u2019': "'",  # right single quotation mark
        '`': "",       # backtick to nothing
        '~': "",       # tilde to nothing

        # Other special characters
        '\u2013': '-',  # en dash to hyphen
        '\u2014': '-',  # em dash to hyphen
        '\u2026': '...',  # horizontal ellipsis
        '\u2028': ' ',    # line separator
        '\u2029': ' ',    # paragraph separator

        # Common symbols that might cause issues
        '©': '(c)',
        '®': '(r)',
        '™': '(tm)',
        '€': 'EUR',
        '£': 'GBP',
        '¥': 'JPY',

        # Escape single quotes for SQL
        "'": ""
    }

    # Apply all replacements
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)

    # Remove any remaining control characters
    cleaned = ''.join(char for char in cleaned if char.isprintable() or char.isspace())

    # Normalize whitespace (collapse multiple spaces into one)
    cleaned = ' '.join(cleaned.split())

    # Remove any non-ASCII characters (optional, uncomment if needed)
    cleaned = re.sub(r'[^\x00-\x7F]+', '', cleaned)

    # Safety checks for text length
    MAX_TEXT_LENGTH = 1_000_000  # 1 million characters
    if len(cleaned) > MAX_TEXT_LENGTH:
        cleaned = cleaned[:MAX_TEXT_LENGTH] + "... (truncated)"

    return cleaned
