"""
Upload parsing service for CSV and Excel files.

Handles:
- CSV file parsing
- Excel file parsing (.xlsx, .xls)
- Data preview generation
- Row/column analysis
"""

import csv
import io
import logging
from typing import Dict, List, Optional, Tuple
import openpyxl
import pandas as pd

logger = logging.getLogger(__name__)


class UploadParseError(Exception):
    """Raised when file parsing fails."""
    pass


class UploadParserService:
    """Service for parsing uploaded data files."""

    SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls"}
    MAX_PREVIEW_ROWS = 10
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

    def parse_upload(
        self,
        file_content: bytes,
        filename: str,
        content_type: str = None,
    ) -> Dict:
        """
        Parse an uploaded file and extract structured data.

        Args:
            file_content: Raw file bytes
            filename: Original filename
            content_type: MIME type (optional)

        Returns:
            Dict with parsed data:
            {
                "filename": str,
                "file_size": int,
                "parse_status": str,  # "success", "partial", "failed"
                "row_count": int,
                "column_names": List[str],
                "column_count": int,
                "preview_rows": List[List[Any]],  # First N rows
                "data_types": Dict[str, str],  # Column name -> inferred type
                "parse_error": Optional[str],
            }
        """
        file_size = len(file_content)

        if file_size > self.MAX_FILE_SIZE:
            raise UploadParseError(f"File too large: {file_size} bytes (max {self.MAX_FILE_SIZE})")

        # Determine file type
        extension = self._get_extension(filename)

        if extension not in self.SUPPORTED_EXTENSIONS:
            raise UploadParseError(f"Unsupported file type: {extension}")

        result = {
            "filename": filename,
            "file_size": file_size,
            "parse_status": "failed",
            "row_count": 0,
            "column_names": [],
            "column_count": 0,
            "preview_rows": [],
            "data_types": {},
            "parse_error": None,
        }

        try:
            if extension == ".csv":
                parsed = self._parse_csv(file_content)
            elif extension in {".xlsx", ".xls"}:
                parsed = self._parse_excel(file_content)
            else:
                raise UploadParseError(f"Unsupported extension: {extension}")

            result.update(parsed)
            result["parse_status"] = "success"

        except Exception as e:
            logger.exception(f"Failed to parse {filename}")
            result["parse_error"] = str(e)
            result["parse_status"] = "failed"

        return result

    def _parse_csv(self, file_content: bytes) -> Dict:
        """Parse CSV file content."""
        # Try to detect encoding
        text_content = self._decode_bytes(file_content)

        # Use pandas for robust CSV parsing
        try:
            df = pd.read_csv(io.StringIO(text_content))
        except Exception as e:
            # Fallback to Python csv module with dialect detection
            logger.warning(f"pandas failed, trying csv module: {e}")
            df = self._parse_csv_fallback(text_content)

        return self._dataframe_to_result(df)

    def _parse_excel(self, file_content: bytes) -> Dict:
        """Parse Excel file content."""
        # Use pandas to read Excel
        df = pd.read_excel(io.BytesIO(file_content), engine="openpyxl")
        return self._dataframe_to_result(df)

    def _parse_csv_fallback(self, text_content: str) -> pd.DataFrame:
        """Fallback CSV parser using Python csv module."""
        # Detect dialect
        sample = text_content[:1024]
        try:
            dialect = csv.Sniffer().sniff(sample)
        except Exception:
            dialect = csv.excel

        # Parse CSV
        reader = csv.reader(io.StringIO(text_content), dialect=dialect)
        rows = list(reader)

        if not rows:
            return pd.DataFrame()

        # First row as headers
        headers = rows[0]
        data_rows = rows[1:]

        return pd.DataFrame(data_rows, columns=headers)

    def _dataframe_to_result(self, df: pd.DataFrame) -> Dict:
        """Convert pandas DataFrame to parse result dict."""
        row_count = len(df)
        column_names = df.columns.tolist()
        column_count = len(column_names)

        # Get preview rows (convert to list of lists)
        preview_rows = []
        for idx in range(min(row_count, self.MAX_PREVIEW_ROWS)):
            row_data = df.iloc[idx].tolist()
            # Convert NaN to None
            row_data = [None if pd.isna(val) else val for val in row_data]
            preview_rows.append(row_data)

        # Infer data types
        data_types = {}
        for col in column_names:
            dtype = str(df[col].dtype)
            if dtype.startswith("int"):
                data_types[col] = "integer"
            elif dtype.startswith("float"):
                data_types[col] = "float"
            elif dtype == "object":
                data_types[col] = "string"
            elif dtype.startswith("datetime"):
                data_types[col] = "datetime"
            elif dtype == "bool":
                data_types[col] = "boolean"
            else:
                data_types[col] = "unknown"

        return {
            "row_count": row_count,
            "column_names": column_names,
            "column_count": column_count,
            "preview_rows": preview_rows,
            "data_types": data_types,
        }

    def _decode_bytes(self, file_content: bytes) -> str:
        """Decode bytes to string, trying common encodings."""
        encodings = ["utf-8", "latin-1", "cp1252", "iso-8859-1"]

        for encoding in encodings:
            try:
                return file_content.decode(encoding)
            except (UnicodeDecodeError, AttributeError):
                continue

        # Fallback: decode with utf-8, replacing errors
        return file_content.decode("utf-8", errors="replace")

    def _get_extension(self, filename: str) -> str:
        """Extract file extension from filename."""
        if "." not in filename:
            return ""
        return "." + filename.rsplit(".", 1)[-1].lower()
