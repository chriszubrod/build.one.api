#!/usr/bin/env python3
"""
Test script to verify all AI services are configured correctly.

Run from project root:
    python scripts/test_ai_services.py
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_azure_openai():
    """Test Azure OpenAI chat completion."""
    print("\n" + "=" * 60)
    print("Testing Azure OpenAI...")
    print("=" * 60)
    
    try:
        from integrations.azure.ai import AzureOpenAIClient
        
        client = AzureOpenAIClient()
        print(f"✓ Client initialized")
        print(f"  Endpoint: {client.endpoint}")
        print(f"  Deployment: {client.deployment_name}")
        
        # Simple test completion
        result = client.simple_completion(
            prompt="Say 'Hello, Build.One!' and nothing else.",
            temperature=0.0,
            max_tokens=20,
        )
        
        print(f"✓ Chat completion successful")
        print(f"  Response: {result}")
        return True
        
    except Exception as e:
        print(f"✗ Azure OpenAI test failed: {e}")
        return False


def test_azure_document_intelligence():
    """Test Azure Document Intelligence initialization."""
    print("\n" + "=" * 60)
    print("Testing Azure Document Intelligence...")
    print("=" * 60)
    
    try:
        from integrations.azure.ai import AzureDocumentIntelligence
        
        client = AzureDocumentIntelligence()
        print(f"✓ Client initialized")
        print(f"  Endpoint: {client.endpoint}")
        
        # Note: We don't run actual extraction without a document
        print(f"✓ Ready to extract documents (no test document provided)")
        return True
        
    except Exception as e:
        print(f"✗ Azure Document Intelligence test failed: {e}")
        return False


def test_azure_search():
    """Test Azure AI Search connection."""
    print("\n" + "=" * 60)
    print("Testing Azure AI Search...")
    print("=" * 60)
    
    try:
        from integrations.azure.ai import AzureSearchClient
        
        client = AzureSearchClient()
        print(f"✓ Client initialized")
        print(f"  Endpoint: {client.endpoint}")
        print(f"  Index: {client.index_name}")
        
        # Check if index exists (will return None if not)
        index = client.get_index()
        if index:
            print(f"✓ Index '{client.index_name}' exists")
        else:
            print(f"✓ Index '{client.index_name}' does not exist yet (will be created when needed)")
        
        return True
        
    except Exception as e:
        print(f"✗ Azure AI Search test failed: {e}")
        return False


def test_local_embeddings():
    """Test local embedding generation."""
    print("\n" + "=" * 60)
    print("Testing Local Embeddings (Sentence Transformers)...")
    print("=" * 60)
    
    try:
        from shared.ai import get_embedding_service
        
        service = get_embedding_service()
        print(f"✓ Service initialized")
        print(f"  Model: {service.model_name}")
        
        # Generate a test embedding
        test_text = "This is a test sentence for embedding generation."
        embedding = service.generate_embedding(test_text)
        
        print(f"✓ Embedding generated")
        print(f"  Dimension: {len(embedding)}")
        print(f"  First 5 values: {embedding[:5]}")
        
        # Test batch embedding
        batch_texts = ["First sentence", "Second sentence", "Third sentence"]
        batch_embeddings = service.generate_embeddings_batch(batch_texts)
        
        print(f"✓ Batch embedding successful")
        print(f"  Batch size: {len(batch_embeddings)}")
        
        # Test similarity
        similarity = service.compute_similarity(batch_embeddings[0], batch_embeddings[1])
        print(f"✓ Similarity computation working")
        print(f"  Similarity between sentences 1 & 2: {similarity:.4f}")
        
        return True
        
    except ImportError as e:
        print(f"✗ Import error: {e}")
        print("  Run: pip install sentence-transformers")
        return False
    except Exception as e:
        print(f"✗ Local embeddings test failed: {e}")
        return False


def main():
    """Run all tests."""
    print("\n" + "#" * 60)
    print("# AI Services Test Suite")
    print("#" * 60)
    
    results = {
        "Azure OpenAI": test_azure_openai(),
        "Azure Document Intelligence": test_azure_document_intelligence(),
        "Azure AI Search": test_azure_search(),
        "Local Embeddings": test_local_embeddings(),
    }
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    all_passed = True
    for service, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {service}: {status}")
        if not passed:
            all_passed = False
    
    print()
    if all_passed:
        print("All tests passed! AI services are ready to use.")
    else:
        print("Some tests failed. Check the errors above.")
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
