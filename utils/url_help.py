"""Helper functions for Intuit integrations."""

def build_uri(urls, query, query_name, realm_id):
    """Build uri for Intuit query."""
    uri = ""
    base = ""
    version = ""
    url = ""

    for row in urls:
        name = getattr(row, 'Name')
        slug = getattr(row, 'Slug')
        if name == 'base':
            base = slug
        elif name == 'minorversion':
            version = slug
        elif name == query_name:
            url = slug
    uri = base + url.format(realm_id, query, version)
    return uri
