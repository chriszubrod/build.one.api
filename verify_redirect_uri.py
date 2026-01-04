#!/usr/bin/env python3
"""
Script to verify the exact redirect URI format for Intuit OAuth.
Run this to get the exact redirect URI that should be in Intuit Developer Portal.
"""

redirect_uri = "https://buildone-esgaducjg4d3eucf.eastus-01.azurewebsites.net/api/v1/intuit/qbo/auth/request/callback"

print("=" * 80)
print("INTUIT REDIRECT URI VERIFICATION")
print("=" * 80)
print()
print("Copy this EXACT redirect URI to Intuit Developer Portal:")
print()
print(redirect_uri)
print()
print("=" * 80)
print("VERIFICATION DETAILS:")
print("=" * 80)
print(f"Length: {len(redirect_uri)} characters")
print(f"Protocol: {redirect_uri.split('://')[0]}")
print(f"Domain: {redirect_uri.split('://')[1].split('/')[0]}")
print(f"Path: /{'/'.join(redirect_uri.split('://')[1].split('/')[1:])}")
print(f"Has trailing slash: {redirect_uri.endswith('/')}")
print(f"Has leading space: {redirect_uri.startswith(' ')}")
print(f"Has trailing space: {redirect_uri.endswith(' ')}")
print()
print("=" * 80)
print("CHARACTER-BY-CHARACTER BREAKDOWN:")
print("=" * 80)
for i, char in enumerate(redirect_uri):
    if char == ' ':
        print(f"Position {i}: SPACE (this would cause issues!)")
    elif ord(char) > 127:
        print(f"Position {i}: {char} (non-ASCII character)")
    else:
        print(f"Position {i}: '{char}'")
print()
print("=" * 80)
print("URL-ENCODED VERSION (what gets sent in query string):")
print("=" * 80)
from urllib.parse import urlencode
encoded = urlencode({"redirect_uri": redirect_uri})
print(encoded.split("=")[1])
print()

