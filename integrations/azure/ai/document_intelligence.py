# Python Standard Library Imports
import logging
import time
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

# Third-party Imports
import httpx

# Local Imports
import config

logger = logging.getLogger(__name__)


class AzureDocumentIntelligenceError(Exception):
    """Base exception for Azure Document Intelligence operations."""
    pass


@dataclass
class ExtractedTable:
    """Represents an extracted table from a document."""
    row_count: int
    column_count: int
    cells: List[Dict[str, Any]]

    def to_list(self) -> List[List[str]]:
        """Convert table to a 2D list of cell contents."""
        if not self.cells:
            return []

        # Initialize grid
        grid = [["" for _ in range(self.column_count)] for _ in range(self.row_count)]

        # Fill in cells
        for cell in self.cells:
            row_idx = cell.get("rowIndex", 0)
            col_idx = cell.get("columnIndex", 0)
            content = cell.get("content", "")
            if 0 <= row_idx < self.row_count and 0 <= col_idx < self.column_count:
                grid[row_idx][col_idx] = content

        return grid


@dataclass
class ExtractionResult:
    """Result of document extraction."""
    content: str  # Full extracted text
    pages: List[Dict[str, Any]]  # Page-level information
    tables: List[ExtractedTable]  # Extracted tables
    paragraphs: List[str]  # Individual paragraphs
    key_value_pairs: List[Dict[str, str]]  # Key-value pairs found
    raw_response: Dict[str, Any]  # Full API response for advanced use


class AzureDocumentIntelligence:
    """
    Azure AI Document Intelligence client using raw HTTP REST API.
    Uses the Layout model for generic document extraction.
    """

    API_VERSION = "2024-02-29-preview"
    POLL_INTERVAL = 1.0  # seconds between status checks
    MAX_POLL_ATTEMPTS = 120  # max ~2 minutes of polling

    def __init__(self):
        """Initialize Azure Document Intelligence client."""
        settings = config.Settings()
        self.endpoint = settings.azure_document_intelligence_endpoint
        self.api_key = settings.azure_document_intelligence_key

        if not self.endpoint:
            raise ValueError("Azure Document Intelligence endpoint is required")
        if not self.api_key:
            raise ValueError("Azure Document Intelligence key is required")

        # Ensure endpoint doesn't have trailing slash
        self.endpoint = self.endpoint.rstrip("/")

    def _get_headers(self, content_type: Optional[str] = None) -> dict:
        """Get standard headers for Document Intelligence requests."""
        headers = {
            "Ocp-Apim-Subscription-Key": self.api_key,
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _build_url(self, path: str) -> str:
        """Build the full URL for an API endpoint."""
        return f"{self.endpoint}/documentintelligence/{path}?api-version={self.API_VERSION}"

    def extract_document(
        self,
        file_content: bytes,
        content_type: str = "application/pdf",
    ) -> ExtractionResult:
        """
        Extract text, tables, and structure from a document using the Layout model.

        Args:
            file_content: Document content as bytes (PDF, image, etc.)
            content_type: MIME type of the document (application/pdf, image/jpeg, etc.)

        Returns:
            ExtractionResult containing extracted text, tables, and structure.

        Raises:
            AzureDocumentIntelligenceError: If extraction fails.
        """
        try:
            # Start the analysis
            operation_location = self._start_analysis(file_content, content_type)

            # Poll for completion
            result = self._poll_for_result(operation_location)

            # Parse the result
            return self._parse_result(result)

        except AzureDocumentIntelligenceError:
            raise
        except Exception as e:
            logger.error(f"Document Intelligence error: {e}")
            raise AzureDocumentIntelligenceError(f"Document extraction failed: {str(e)}")

    def extract_document_from_url(self, document_url: str) -> ExtractionResult:
        """
        Extract text, tables, and structure from a document URL.

        Args:
            document_url: Public URL to the document.

        Returns:
            ExtractionResult containing extracted text, tables, and structure.

        Raises:
            AzureDocumentIntelligenceError: If extraction fails.
        """
        try:
            # Start the analysis with URL
            operation_location = self._start_analysis_from_url(document_url)

            # Poll for completion
            result = self._poll_for_result(operation_location)

            # Parse the result
            return self._parse_result(result)

        except AzureDocumentIntelligenceError:
            raise
        except Exception as e:
            logger.error(f"Document Intelligence error: {e}")
            raise AzureDocumentIntelligenceError(f"Document extraction failed: {str(e)}")

    def _start_analysis(self, file_content: bytes, content_type: str) -> str:
        """
        Start document analysis and return the operation location for polling.
        """
        url = self._build_url("documentModels/prebuilt-layout:analyze")
        headers = self._get_headers(content_type)

        logger.debug(f"Starting document analysis, content size: {len(file_content)} bytes")

        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, headers=headers, content=file_content)

            if response.status_code != 202:
                error_text = response.text if hasattr(response, "text") else str(response.status_code)
                logger.error(f"Document Intelligence start failed: {response.status_code} - {error_text}")
                raise AzureDocumentIntelligenceError(
                    f"Failed to start analysis: {response.status_code}"
                )

            operation_location = response.headers.get("Operation-Location")
            if not operation_location:
                raise AzureDocumentIntelligenceError("No operation location in response")

            logger.debug(f"Analysis started, operation: {operation_location}")
            return operation_location

    def _start_analysis_from_url(self, document_url: str) -> str:
        """
        Start document analysis from URL and return the operation location for polling.
        """
        url = self._build_url("documentModels/prebuilt-layout:analyze")
        headers = self._get_headers("application/json")

        payload = {"urlSource": document_url}

        logger.debug(f"Starting document analysis from URL: {document_url}")

        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, headers=headers, json=payload)

            if response.status_code != 202:
                error_text = response.text if hasattr(response, "text") else str(response.status_code)
                logger.error(f"Document Intelligence start failed: {response.status_code} - {error_text}")
                raise AzureDocumentIntelligenceError(
                    f"Failed to start analysis: {response.status_code}"
                )

            operation_location = response.headers.get("Operation-Location")
            if not operation_location:
                raise AzureDocumentIntelligenceError("No operation location in response")

            logger.debug(f"Analysis started, operation: {operation_location}")
            return operation_location

    def _poll_for_result(self, operation_location: str) -> Dict[str, Any]:
        """
        Poll the operation location until analysis is complete.
        """
        headers = self._get_headers()
        attempts = 0

        with httpx.Client(timeout=30.0) as client:
            while attempts < self.MAX_POLL_ATTEMPTS:
                response = client.get(operation_location, headers=headers)
                response.raise_for_status()

                result = response.json()
                status = result.get("status", "")

                if status == "succeeded":
                    logger.debug("Document analysis completed successfully")
                    return result
                elif status == "failed":
                    error = result.get("error", {})
                    error_msg = error.get("message", "Unknown error")
                    logger.error(f"Document analysis failed: {error_msg}")
                    raise AzureDocumentIntelligenceError(f"Analysis failed: {error_msg}")
                elif status in ("notStarted", "running"):
                    attempts += 1
                    time.sleep(self.POLL_INTERVAL)
                else:
                    logger.warning(f"Unknown status: {status}")
                    attempts += 1
                    time.sleep(self.POLL_INTERVAL)

        raise AzureDocumentIntelligenceError("Analysis timed out")

    def _parse_result(self, result: Dict[str, Any]) -> ExtractionResult:
        """
        Parse the analysis result into a structured ExtractionResult.
        """
        analyze_result = result.get("analyzeResult", {})

        # Extract full content
        content = analyze_result.get("content", "")

        # Extract pages
        pages = []
        for page in analyze_result.get("pages", []):
            pages.append({
                "page_number": page.get("pageNumber", 0),
                "width": page.get("width", 0),
                "height": page.get("height", 0),
                "unit": page.get("unit", "pixel"),
                "words_count": len(page.get("words", [])),
                "lines_count": len(page.get("lines", [])),
            })

        # Extract tables
        tables = []
        for table in analyze_result.get("tables", []):
            extracted_table = ExtractedTable(
                row_count=table.get("rowCount", 0),
                column_count=table.get("columnCount", 0),
                cells=table.get("cells", []),
            )
            tables.append(extracted_table)

        # Extract paragraphs
        paragraphs = []
        for paragraph in analyze_result.get("paragraphs", []):
            content_text = paragraph.get("content", "")
            if content_text.strip():
                paragraphs.append(content_text)

        # Extract key-value pairs (if present)
        key_value_pairs = []
        for kv in analyze_result.get("keyValuePairs", []):
            key_content = kv.get("key", {}).get("content", "")
            value_content = kv.get("value", {}).get("content", "") if kv.get("value") else ""
            if key_content:
                key_value_pairs.append({
                    "key": key_content,
                    "value": value_content,
                })

        return ExtractionResult(
            content=content,
            pages=pages,
            tables=tables,
            paragraphs=paragraphs,
            key_value_pairs=key_value_pairs,
            raw_response=result,
        )
