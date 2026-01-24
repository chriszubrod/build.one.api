#!/usr/bin/env python3
"""
Setup script for Azure AI Search index.

Creates the documents-index with:
- Text fields for full-text search
- Vector field for semantic search (384 dimensions for all-MiniLM-L6-v2)
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integrations.azure.ai import AzureSearchClient
from integrations.azure.ai.search_client import create_vector_search_config, AzureSearchError


# Index field definitions
DOCUMENTS_INDEX_FIELDS = [
    # Key field - uses attachment public_id
    {
        "name": "id",
        "type": "Edm.String",
        "key": True,
        "searchable": False,
        "filterable": True,
        "sortable": False,
    },
    # Attachment metadata
    {
        "name": "attachment_id",
        "type": "Edm.Int64",
        "searchable": False,
        "filterable": True,
        "sortable": True,
    },
    {
        "name": "filename",
        "type": "Edm.String",
        "searchable": True,
        "filterable": True,
        "sortable": True,
    },
    {
        "name": "original_filename",
        "type": "Edm.String",
        "searchable": True,
        "filterable": False,
        "sortable": False,
    },
    {
        "name": "content_type",
        "type": "Edm.String",
        "searchable": False,
        "filterable": True,
        "sortable": False,
    },
    {
        "name": "category",
        "type": "Edm.String",
        "searchable": True,
        "filterable": True,
        "sortable": True,
        "facetable": True,
    },
    {
        "name": "description",
        "type": "Edm.String",
        "searchable": True,
        "filterable": False,
        "sortable": False,
    },
    # Extracted content - main searchable field
    {
        "name": "content",
        "type": "Edm.String",
        "searchable": True,
        "filterable": False,
        "sortable": False,
        "analyzer": "en.microsoft",  # English language analyzer
    },
    # Content preview (first 500 chars for display)
    {
        "name": "content_preview",
        "type": "Edm.String",
        "searchable": False,
        "filterable": False,
        "sortable": False,
    },
    # Vector field for semantic search
    {
        "name": "content_vector",
        "type": "Collection(Edm.Single)",
        "searchable": True,
        "filterable": False,
        "sortable": False,
        "dimensions": 384,  # all-MiniLM-L6-v2 produces 384-dimensional vectors
        "vectorSearchProfile": "vector-profile",
    },
    # Timestamps
    {
        "name": "created_datetime",
        "type": "Edm.DateTimeOffset",
        "searchable": False,
        "filterable": True,
        "sortable": True,
    },
    {
        "name": "extracted_datetime",
        "type": "Edm.DateTimeOffset",
        "searchable": False,
        "filterable": True,
        "sortable": True,
    },
    {
        "name": "indexed_datetime",
        "type": "Edm.DateTimeOffset",
        "searchable": False,
        "filterable": True,
        "sortable": True,
    },
]


def main():
    print("=" * 60)
    print("Azure AI Search Index Setup")
    print("=" * 60)
    print()

    # Initialize client
    client = AzureSearchClient()
    print(f"Index name: {client.index_name}")
    print(f"Endpoint: {client.endpoint}")
    print()

    # Check if index already exists
    print("1. Checking for existing index...")
    existing = client.get_index()
    if existing:
        print(f"   Index '{client.index_name}' already exists")
        print("   To recreate, delete the index first:")
        print(f"   - Via Azure Portal, or")
        print(f"   - Run: client.delete_index()")
        print()
        
        # Show existing fields
        print("   Existing fields:")
        for field in existing.get("fields", [])[:10]:
            print(f"   - {field['name']}: {field['type']}")
        if len(existing.get("fields", [])) > 10:
            print(f"   ... and {len(existing.get('fields', [])) - 10} more")
        return 0

    # Create vector search configuration
    print("2. Creating vector search configuration...")
    vector_config = create_vector_search_config(
        algorithm_name="hnsw-algorithm",
        profile_name="vector-profile",
        metric="cosine",
        m=4,
        ef_construction=400,
        ef_search=500,
    )
    print("   HNSW algorithm configured (cosine similarity)")

    # Create index
    print("\n3. Creating search index...")
    try:
        result = client.create_index(
            fields=DOCUMENTS_INDEX_FIELDS,
            vector_search_config=vector_config,
        )
        print(f"   Index '{client.index_name}' created successfully!")
        print()
        print("   Fields created:")
        for field in result.get("fields", []):
            field_type = field['type']
            if field.get('dimensions'):
                field_type += f" ({field['dimensions']} dims)"
            print(f"   - {field['name']}: {field_type}")
    except AzureSearchError as e:
        print(f"   FAILED: {e}")
        return 1

    print()
    print("=" * 60)
    print("Index setup complete!")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
