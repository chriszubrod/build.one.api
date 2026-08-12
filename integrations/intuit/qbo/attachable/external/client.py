# Python Standard Library Imports
import json
import logging
from typing import List, Optional

# Third-party Imports
import httpx

# Local Imports
from integrations.intuit.qbo.attachable.external.schemas import (
    QboAttachable,
    QboAttachableResponse,
)
from integrations.intuit.qbo.base.client import QboHttpClient
from integrations.intuit.qbo.base.errors import QboValidationError

logger = logging.getLogger(__name__)

# Before U-218e this client mapped all 5xx to non-retryable QboValidationError and
# every httpx.RequestError (incl. timeouts) to a bare non-retryable QboError. The
# shared QboHttpClient maps them to retryable QboServerError / QboServiceUnavailableError
# / QboTimeoutError / QboTransportError — transient failures now retry instead of
# hard-failing (correct behavior; changes wall-clock and metered call count). Pull
# scripts wrap attachable legs in log-only handlers so errors do not reach watermarks.


class QboAttachableClient:
    """
    Client for QBO Attachable endpoints. Composes `QboHttpClient` for transport.

    `download_attachable` deliberately stays outside the shared client seam — it
    GETs an absolute TempDownloadUri on a different host with no Authorization
    header and is not metered against the CorePlus cap.
    """

    def __init__(
        self,
        *,
        realm_id: str,
        http_client: Optional[QboHttpClient] = None,
        minor_version: Optional[int] = 65,
    ):
        self.realm_id = realm_id
        self._owns_http_client = http_client is None
        self._http_client = http_client or QboHttpClient(
            realm_id=realm_id,
            minor_version=minor_version,
        )

    def close(self) -> None:
        if self._owns_http_client:
            self._http_client.close()

    def __enter__(self) -> "QboAttachableClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def get_attachable(self, attachable_id: str) -> QboAttachable:
        """Get a single Attachable by ID."""
        data = self._http_client.get(
            f"attachable/{attachable_id}",
            operation_name="qbo.attachable.get",
        )
        response = QboAttachableResponse(**data)
        return response.attachable

    def query_attachables(
        self,
        start_position: int = 1,
        max_results: int = 1000,
    ) -> List[QboAttachable]:
        """
        Query attachables with pagination.

        Latent fix riding along with the re-route: the old client built
        ?minorversion= into the URL string AND passed params= — httpx replaces
        the query string when params= is given, so those calls ran WITHOUT
        minorversion. The shared client merges minorversion into params.
        """
        query_parts = ["SELECT * FROM Attachable"]
        query_parts.append(f"STARTPOSITION {start_position} MAXRESULTS {max_results}")
        query_string = " ".join(query_parts)

        logger.debug(f"Querying Attachables with query: {query_string}")
        data = self._http_client.get(
            "query",
            params={"query": query_string},
            operation_name="qbo.attachable.query",
        )

        query_response = data.get("QueryResponse", {}) if isinstance(data, dict) else {}
        attachables_data = query_response.get("Attachable", [])
        return [QboAttachable(**a) for a in attachables_data]

    def query_all_attachables(self) -> List[QboAttachable]:
        """Query all attachables with pagination."""
        all_attachables: List[QboAttachable] = []
        start_position = 1
        max_results = 1000

        while True:
            attachables = self.query_attachables(
                start_position=start_position,
                max_results=max_results,
            )
            if not attachables:
                break
            all_attachables.extend(attachables)
            if len(attachables) < max_results:
                break
            start_position += max_results

        logger.info(f"Retrieved {len(all_attachables)} attachables from QBO")
        return all_attachables

    def download_attachable(self, attachable: QboAttachable) -> Optional[bytes]:
        """
        Download file content via TempDownloadUri — outside the shared client seam.

        Hits an absolute foreign URL with no Authorization header; deliberately
        NOT metered against the CorePlus cap.
        """
        download_uri = attachable.temp_download_uri or attachable.file_access_uri
        if not download_uri:
            logger.warning(f"No download URI for attachable {attachable.id}")
            return None

        try:
            with httpx.Client(timeout=60.0) as download_client:
                response = download_client.get(download_uri)

                if response.status_code == 200:
                    logger.debug(
                        f"Downloaded attachable {attachable.id}: {len(response.content)} bytes"
                    )
                    return response.content
                logger.error(
                    f"Failed to download attachable {attachable.id}: {response.status_code}"
                )
                return None

        except httpx.RequestError as e:
            logger.error(f"Failed to download attachable {attachable.id}: {e}")
            return None

    def upload_attachable(
        self,
        file_content: bytes,
        filename: str,
        content_type: str,
        entity_type: str,
        entity_id: str,
        note: Optional[str] = None,
    ) -> QboAttachable:
        """
        Upload a file to QBO and link it to an entity via POST /upload.

        Routes through the shared client's multipart seam (tier C, requestid off).
        """
        attachable_metadata = {
            "AttachableRef": [
                {
                    "EntityRef": {
                        # QBO's EntityRef is {type: <entity NAME>, value: <entity ID>}
                        # — e.g. {"type": "Bill", "value": "123"}. The re-route briefly
                        # inverted these; a swap 400s or links the attachment to nothing,
                        # and no part-name/order assertion can see it. The wire-shape test
                        # decodes this JSON part precisely to pin it.
                        "type": entity_type,
                        "value": entity_id,
                    }
                }
            ],
            "FileName": filename,
            "ContentType": content_type,
        }
        if note:
            attachable_metadata["Note"] = note

        logger.debug(f"Uploading attachable '{filename}' to {entity_type} {entity_id}")

        # Part order is load-bearing for Intuit /upload: metadata before content.
        files = [
            (
                "file_metadata_01",
                (None, json.dumps(attachable_metadata), "application/json"),
            ),
            (
                # filename is load-bearing: httpx renders (None, ...) as a plain
                # field with no filename= in Content-Disposition, but (filename, ...)
                # renders a real file part. HEAD passed `filename` here and the
                # re-route dropped it; part-name/order assertions cannot see the
                # difference, so the wire-shape test asserts Content-Disposition.
                "file_content_01",
                (filename, file_content, content_type),
            ),
        ]

        data = self._http_client.post_multipart(
            "upload",
            files=files,
            operation_name="qbo.attachable.upload",
        )

        attachable_response = data.get("AttachableResponse", [])
        if attachable_response and len(attachable_response) > 0:
            attachable_data = attachable_response[0].get("Attachable")
            if attachable_data:
                attachable = QboAttachable(**attachable_data)
                logger.info(
                    f"Successfully uploaded attachable {attachable.id} "
                    f"for {entity_type} {entity_id}"
                )
                return attachable

        if "Attachable" in data:
            attachable = QboAttachable(**data["Attachable"])
            logger.info(
                f"Successfully uploaded attachable {attachable.id} for {entity_type} {entity_id}"
            )
            return attachable

        logger.error(f"Unexpected upload response format: {data}")
        raise QboValidationError("Unexpected upload response format")
