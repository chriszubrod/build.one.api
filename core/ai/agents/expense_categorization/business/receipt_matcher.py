# Python Standard Library Imports
import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class ReceiptMatcherService:
    """
    Matches receipts to uncategorized expense transactions.

    Two matching strategies:
    1. QBO Attachables — purchases with attached receipts in QuickBooks Online
    2. Inbox emails — receipt/expense emails matched by vendor, amount, and date
    """

    def match_qbo_attachables_to_uncategorized(
        self,
        lines: List[dict],
        realm_id: Optional[str] = None,
    ) -> List[dict]:
        """
        For purchases that already have QBO Attachables, sync them and return
        match results. These are high-confidence matches since QBO already
        links the receipt to the purchase.

        Args:
            lines: Uncategorized lines from get_lines_needing_update()
            realm_id: QBO realm ID for syncing attachables

        Returns:
            List of match dicts compatible with ReceiptMatchItem schema
        """
        matches = []

        # Filter to lines that have attachments
        lines_with_attachments = [l for l in lines if l.get("has_attachment")]

        if not lines_with_attachments or not realm_id:
            return matches

        from integrations.intuit.qbo.attachable.business.service import QboAttachableService
        from integrations.intuit.qbo.purchase.business.service import QboPurchaseService

        attachable_service = QboAttachableService()
        qbo_service = QboPurchaseService()

        # Group by purchase to avoid duplicate lookups
        seen_purchases: Dict[int, list] = {}
        for line in lines_with_attachments:
            purchase_id = line.get("qbo_purchase_id")
            if purchase_id:
                seen_purchases.setdefault(purchase_id, []).append(line)

        for purchase_id, purchase_lines in seen_purchases.items():
            try:
                # Get the QBO purchase to find its qbo_id
                purchase = qbo_service.read_by_id(purchase_id)
                if not purchase or not purchase.qbo_id:
                    continue

                # Look up already-synced attachables for this purchase
                attachables = attachable_service.read_by_entity_ref(
                    entity_ref_type="Purchase",
                    entity_ref_value=purchase.qbo_id,
                    realm_id=realm_id,
                )

                if not attachables:
                    # Try syncing from QBO
                    try:
                        attachables = attachable_service.sync_attachables_for_purchase(
                            realm_id=realm_id,
                            purchase_qbo_id=purchase.qbo_id,
                            sync_to_modules=True,
                        )
                    except Exception as e:
                        logger.warning(
                            "Failed to sync attachables for purchase %s: %s",
                            purchase.qbo_id, e,
                        )
                        continue

                if not attachables:
                    continue

                # Match each line to the first attachable (QBO links at purchase level)
                for line in purchase_lines:
                    att = attachables[0]
                    matches.append({
                        "qbo_purchase_line_id": line.get("qbo_purchase_line_id"),
                        "match_type": "qbo_attachable",
                        "attachment_id": att.id,
                        "filename": att.file_name,
                        "confidence": 0.95,
                        "match_signals": {
                            "source": "qbo_attachable",
                            "qbo_attachable_id": att.qbo_id,
                            "purchase_qbo_id": purchase.qbo_id,
                        },
                    })

            except Exception as e:
                logger.warning(
                    "Error matching attachables for purchase %d: %s",
                    purchase_id, e,
                )

        return matches

    def match_inbox_to_uncategorized(
        self,
        lines: List[dict],
    ) -> List[dict]:
        """
        Match recent inbox emails (classified as expense/receipt) to
        uncategorized transaction lines by vendor name, amount, and date.

        Args:
            lines: Uncategorized lines from get_lines_needing_update()

        Returns:
            List of match dicts compatible with ReceiptMatchItem schema
        """
        if not lines:
            return []

        matches = []

        # Load recent expense/receipt classified inbox records
        inbox_records = self._get_recent_expense_inbox_records()
        if not inbox_records:
            return matches

        for line in lines:
            vendor_name = (line.get("entity_ref_name") or "").strip()
            amount = line.get("line_amount")
            txn_date_str = line.get("txn_date")
            line_id = line.get("qbo_purchase_line_id")

            if not vendor_name:
                continue

            best_match = None
            best_confidence = 0.0

            for record in inbox_records:
                signals = {}
                score = 0.0

                # --- Vendor name matching ---
                vendor_score = self._match_vendor(
                    vendor_name=vendor_name,
                    from_name=record.from_name,
                    from_email=record.from_email,
                    subject=record.subject,
                )
                if vendor_score < 0.3:
                    continue  # No vendor match, skip
                signals["vendor_score"] = round(vendor_score, 2)
                score += vendor_score * 0.50  # 50% weight

                # --- Amount matching ---
                amount_score = self._match_amount(
                    line_amount=amount,
                    subject=record.subject,
                )
                if amount_score is not None:
                    signals["amount_score"] = round(amount_score, 2)
                    score += amount_score * 0.30  # 30% weight
                else:
                    # No amount found in email — reduce max confidence
                    score += 0.05  # Small baseline

                # --- Date proximity ---
                date_score = self._match_date(
                    txn_date_str=txn_date_str,
                    record_date_str=record.created_datetime,
                )
                if date_score is not None:
                    signals["date_score"] = round(date_score, 2)
                    score += date_score * 0.20  # 20% weight

                if score > best_confidence:
                    best_confidence = score
                    best_match = {
                        "qbo_purchase_line_id": line_id,
                        "match_type": "inbox_email",
                        "message_id": record.message_id,
                        "subject": record.subject,
                        "confidence": round(score, 2),
                        "match_signals": signals,
                    }

            if best_match and best_confidence >= 0.40:
                matches.append(best_match)

        return matches

    def _get_recent_expense_inbox_records(self) -> list:
        """Load recent InboxRecords classified as expense or receipt."""
        try:
            from entities.inbox.persistence.repo import InboxRecordRepository
            from shared.database import get_connection

            repo = InboxRecordRepository()

            # Query recent records classified as expense/receipt
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT TOP (100) * FROM dbo.InboxRecord "
                    "WHERE ClassificationType IN ('expense', 'receipt') "
                    "AND Status IN ('new', 'pending_review') "
                    "ORDER BY CreatedDatetime DESC",
                )
                rows = cursor.fetchall()
                return [repo._from_db(row) for row in rows if row]
        except Exception as e:
            logger.warning("Failed to load expense inbox records: %s", e)
            return []

    def _match_vendor(
        self,
        vendor_name: str,
        from_name: Optional[str],
        from_email: Optional[str],
        subject: Optional[str],
    ) -> float:
        """
        Fuzzy match vendor name against email sender info.
        Uses Jaccard token similarity and containment scoring.

        Returns: match score 0.0-1.0
        """
        vendor_tokens = self._tokenize(vendor_name)
        if not vendor_tokens:
            return 0.0

        best_score = 0.0

        # Match against from_name
        if from_name:
            score = self._token_similarity(vendor_tokens, self._tokenize(from_name))
            best_score = max(best_score, score)

        # Match against email domain (e.g., "homedepot" from "noreply@homedepot.com")
        if from_email:
            domain = self._extract_domain_name(from_email)
            if domain:
                domain_tokens = self._tokenize(domain)
                score = self._token_similarity(vendor_tokens, domain_tokens)
                best_score = max(best_score, score)

        # Match against subject line
        if subject:
            subject_tokens = self._tokenize(subject)
            # Containment: what fraction of vendor tokens appear in subject
            if vendor_tokens and subject_tokens:
                intersection = vendor_tokens & subject_tokens
                containment = len(intersection) / len(vendor_tokens)
                best_score = max(best_score, containment * 0.85)

        return best_score

    def _match_amount(
        self,
        line_amount: Optional[float],
        subject: Optional[str],
    ) -> Optional[float]:
        """
        Try to extract an amount from the email subject and compare with the
        transaction amount. Returns match score or None if no amount found.
        """
        if line_amount is None or not subject:
            return None

        # Extract amounts from subject (patterns like $123.45, 123.45, etc.)
        amount_pattern = r'\$?\s*(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\b'
        found_amounts = re.findall(amount_pattern, subject)

        if not found_amounts:
            return None

        best_score = 0.0
        for raw_amount in found_amounts:
            try:
                parsed = float(raw_amount.replace(",", ""))
                if parsed <= 0:
                    continue

                # Exact match within 5%
                if line_amount > 0:
                    diff_ratio = abs(parsed - line_amount) / line_amount
                    if diff_ratio <= 0.05:
                        best_score = max(best_score, 1.0 - diff_ratio)
                    elif diff_ratio <= 0.15:
                        best_score = max(best_score, 0.5)
            except (ValueError, ZeroDivisionError):
                continue

        return best_score if best_score > 0 else None

    def _match_date(
        self,
        txn_date_str: Optional[str],
        record_date_str: Optional[str],
    ) -> Optional[float]:
        """
        Score date proximity between transaction date and email date.
        Within 1 day = 1.0, within 7 days = linear decay to 0.3.
        """
        if not txn_date_str or not record_date_str:
            return None

        try:
            txn_date = self._parse_date(txn_date_str)
            record_date = self._parse_date(record_date_str)

            if txn_date is None or record_date is None:
                return None

            days_diff = abs((txn_date - record_date).days)

            if days_diff <= 1:
                return 1.0
            elif days_diff <= 7:
                # Linear decay: 1.0 at day 1 → 0.3 at day 7
                return 1.0 - (days_diff - 1) * (0.7 / 6)
            else:
                return 0.0

        except Exception:
            return None

    def _tokenize(self, text: str) -> set:
        """Split text into lowercase tokens, removing common noise words."""
        if not text:
            return set()
        tokens = set(re.split(r'\W+', text.lower()))
        tokens.discard("")
        # Remove very common noise words
        noise = {"the", "inc", "llc", "ltd", "co", "corp", "noreply", "no", "reply"}
        return tokens - noise

    def _token_similarity(self, tokens_a: set, tokens_b: set) -> float:
        """Combined Jaccard + containment similarity."""
        if not tokens_a or not tokens_b:
            return 0.0

        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b

        jaccard = len(intersection) / len(union) if union else 0.0
        containment = len(intersection) / len(tokens_a) if tokens_a else 0.0

        # Also check substring match on joined tokens
        a_str = " ".join(sorted(tokens_a))
        b_str = " ".join(sorted(tokens_b))
        substring_score = 0.75 if a_str in b_str or b_str in a_str else 0.0

        return max(jaccard, containment * 0.85, substring_score)

    def _extract_domain_name(self, email: str) -> Optional[str]:
        """Extract the domain name part from an email (e.g., 'homedepot' from 'noreply@homedepot.com')."""
        if not email or "@" not in email:
            return None
        domain = email.split("@")[1].lower()
        # Remove TLD
        parts = domain.split(".")
        if len(parts) >= 2:
            return parts[-2]  # e.g., "homedepot" from "homedepot.com"
        return parts[0]

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse various date formats into a datetime."""
        if not date_str:
            return None

        # Handle datetime objects passed as strings
        if isinstance(date_str, datetime):
            return date_str

        for fmt in (
            "%Y-%m-%d",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%S.%f+00:00",
            "%m/%d/%Y",
        ):
            try:
                return datetime.strptime(date_str[:len(fmt) + 5], fmt)
            except (ValueError, IndexError):
                continue

        # Try ISO format as fallback
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return None
