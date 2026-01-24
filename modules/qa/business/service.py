# Python Standard Library Imports
import logging
from typing import Optional, List, Dict, Any

# Third-party Imports

# Local Imports
from modules.search.business.service import get_search_service, SearchService
from integrations.azure.ai import AzureOpenAIClient
from integrations.azure.ai.openai_client import AzureOpenAIError

logger = logging.getLogger(__name__)


# System prompt for the Q&A assistant
QA_SYSTEM_PROMPT = """You are a helpful assistant that answers questions about documents in a construction management system.

You will be provided with:
1. A user's question
2. Relevant document excerpts from the search results

Your job is to:
1. Answer the question based ONLY on the provided document excerpts
2. Be specific and cite which documents your answer comes from
3. If the documents don't contain enough information to answer, say so clearly
4. Keep answers concise but complete

Format guidelines:
- Use bullet points for lists
- Include document names when referencing specific information
- If mentioning amounts, include the currency symbol
- Be direct and professional"""


class QAService:
    """
    Service for answering natural language questions using document search and GPT-4o-mini.
    
    Flow:
    1. User asks a question in natural language
    2. Search for relevant documents using hybrid search
    3. Build context from search results
    4. Use GPT-4o-mini to generate an answer based on the context
    """

    def __init__(
        self,
        search_service: Optional[SearchService] = None,
        openai_client: Optional[AzureOpenAIClient] = None,
    ):
        """Initialize the QAService."""
        self._search_service = search_service
        self._openai_client = openai_client

    @property
    def search_service(self) -> SearchService:
        """Lazy load search service."""
        if self._search_service is None:
            self._search_service = get_search_service()
        return self._search_service

    @property
    def openai_client(self) -> AzureOpenAIClient:
        """Lazy load OpenAI client."""
        if self._openai_client is None:
            self._openai_client = AzureOpenAIClient()
        return self._openai_client

    def ask(
        self,
        question: str,
        category: Optional[str] = None,
        max_documents: int = 5,
        search_mode: str = "hybrid",
    ) -> Dict[str, Any]:
        """
        Answer a natural language question about documents.

        Args:
            question: The user's question in natural language.
            category: Optional category filter for search.
            max_documents: Maximum number of documents to retrieve for context.
            search_mode: Search mode to use ('keyword', 'semantic', 'hybrid').

        Returns:
            Dict containing:
            - answer: The generated answer
            - sources: List of source documents used
            - question: The original question
        """
        logger.info(f"Q&A request: {question[:100]}...")

        # Step 1: Search for relevant documents
        logger.info(f"Searching for relevant documents (mode: {search_mode})")
        if search_mode == "keyword":
            search_results = self.search_service.search(
                query=question,
                category=category,
                top=max_documents,
            )
        elif search_mode == "semantic":
            search_results = self.search_service.semantic_search(
                query=question,
                category=category,
                top=max_documents,
            )
        else:  # hybrid (default)
            search_results = self.search_service.hybrid_search(
                query=question,
                category=category,
                top=max_documents,
            )

        logger.info(f"Found {len(search_results)} relevant documents")

        # Step 2: Build context from search results
        context = self._build_context(search_results)

        if not context:
            return {
                "answer": "I couldn't find any relevant documents to answer your question. Please try rephrasing or check if relevant documents have been uploaded.",
                "sources": [],
                "question": question,
            }

        # Step 3: Generate answer using GPT-4o-mini
        logger.info("Generating answer with GPT-4o-mini")
        try:
            answer = self._generate_answer(question, context)
        except AzureOpenAIError as e:
            logger.error(f"Failed to generate answer: {e}")
            return {
                "answer": f"I found relevant documents but encountered an error generating the answer: {str(e)}",
                "sources": self._format_sources(search_results),
                "question": question,
            }

        return {
            "answer": answer,
            "sources": self._format_sources(search_results),
            "question": question,
        }

    def _build_context(self, search_results: List[Dict[str, Any]]) -> str:
        """
        Build a context string from search results for the LLM.

        Args:
            search_results: List of search result documents.

        Returns:
            Formatted context string.
        """
        if not search_results:
            return ""

        context_parts = []
        for i, doc in enumerate(search_results, 1):
            filename = doc.get("original_filename") or doc.get("filename") or "Unknown"
            preview = doc.get("content_preview", "")
            category = doc.get("category", "")

            doc_context = f"--- Document {i}: {filename}"
            if category:
                doc_context += f" (Category: {category})"
            doc_context += f" ---\n{preview}"

            context_parts.append(doc_context)

        return "\n\n".join(context_parts)

    def _format_sources(self, search_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Format search results as source citations.

        Args:
            search_results: List of search result documents.

        Returns:
            List of source dicts with filename, public_id, and score.
        """
        sources = []
        for doc in search_results:
            sources.append({
                "filename": doc.get("original_filename") or doc.get("filename"),
                "public_id": doc.get("public_id"),
                "category": doc.get("category"),
                "score": doc.get("score"),
            })
        return sources

    def _generate_answer(self, question: str, context: str) -> str:
        """
        Generate an answer using GPT-4o-mini.

        Args:
            question: The user's question.
            context: The document context.

        Returns:
            The generated answer.
        """
        user_prompt = f"""Based on the following document excerpts, please answer this question:

QUESTION: {question}

DOCUMENT EXCERPTS:
{context}

Please provide a clear, concise answer based on the information in these documents."""

        messages = [
            {"role": "system", "content": QA_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        result = self.openai_client.chat_completion(
            messages=messages,
            temperature=0.3,  # Lower temperature for more focused answers
            max_tokens=1000,
        )

        return result.get("content", "")

    def analyze_question(self, question: str) -> Dict[str, Any]:
        """
        Analyze a question to extract intent and entities.
        
        Useful for understanding what the user is asking about before searching.

        Args:
            question: The user's question.

        Returns:
            Dict containing parsed intent, entities, and suggested filters.
        """
        analysis_prompt = """Analyze the following question about construction documents and extract:
1. intent: What the user wants to know (e.g., "find_documents", "summarize", "compare", "calculate")
2. entities: Key entities mentioned (vendor names, project names, dates, amounts)
3. filters: Suggested search filters (category, date_range, etc.)
4. search_query: An optimized search query for finding relevant documents

Return as JSON."""

        messages = [
            {"role": "system", "content": analysis_prompt},
            {"role": "user", "content": question},
        ]

        try:
            result = self.openai_client.chat_completion_with_json(
                messages=messages,
                temperature=0.1,
                max_tokens=500,
            )
            return result
        except AzureOpenAIError as e:
            logger.error(f"Failed to analyze question: {e}")
            return {
                "intent": "unknown",
                "entities": [],
                "filters": {},
                "search_query": question,
            }


# Singleton instance
_qa_service: Optional[QAService] = None


def get_qa_service() -> QAService:
    """Get or create the singleton QAService instance."""
    global _qa_service
    if _qa_service is None:
        _qa_service = QAService()
    return _qa_service
