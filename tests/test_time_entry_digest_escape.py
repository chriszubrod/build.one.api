from entities.time_entry.business.digest_service import TimeEntryDigestService


def test_firstname_escaped_in_digest_body():
    body = TimeEntryDigestService._build_html_body(
        worker={'firstname': 'A & <b>Bob', 'entries': []},
        work_date='2026-06-15',
    )
    assert 'A &amp; &lt;b&gt;Bob' in body
    assert 'A & <b>Bob' not in body
