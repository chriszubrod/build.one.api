"""Resolve extracted vendor names to dbo.Vendor rows (U-676a / U-676b)."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from entities.bill.business.extraction_mapper import BillExtractionMapper, BillExtractionResult
from entities.vendor.business.service import VendorService

_VENDOR_LIST_TTL_SECONDS = 60.0
_FUZZY_MIN_SCORE = 0.5
_COMBINED_MIN_SCORE = 0.35

_cache_at: float = 0.0
_cache_pairs: list[tuple[str, str, Optional[int]]] = []
_now_fn: Callable[[], float] = time.monotonic
_vendor_list_loader: Optional[Callable[[], list]] = None


def configure_vendor_list_cache_for_tests(
    *,
    now_fn: Optional[Callable[[], float]] = None,
    vendor_list_loader: Optional[Callable[[], list]] = None,
) -> None:
    global _cache_at, _cache_pairs, _now_fn, _vendor_list_loader
    _cache_at = 0.0
    _cache_pairs = []
    _vendor_list_loader = vendor_list_loader
    if now_fn is not None:
        _now_fn = now_fn
    elif vendor_list_loader is None:
        _now_fn = time.monotonic


def _load_vendor_pairs() -> list[tuple[str, str, Optional[int]]]:
    if _vendor_list_loader is not None:
        vendors = _vendor_list_loader()
    else:
        vendors = VendorService().read_all()
    out: list[tuple[str, str, Optional[int]]] = []
    for v in vendors:
        if not v.public_id:
            continue
        vid = getattr(v, "id", None)
        out.append((str(v.public_id), v.name or "", int(vid) if vid is not None else None))
    return out


def _vendor_pairs_cached() -> list[tuple[str, str, Optional[int]]]:
    global _cache_at, _cache_pairs
    now = _now_fn()
    if _cache_pairs and (now - _cache_at) < _VENDOR_LIST_TTL_SECONDS:
        return _cache_pairs
    _cache_pairs = _load_vendor_pairs()
    _cache_at = now
    return _cache_pairs


def _match_vendor_in_pairs(
    vendor_name: str,
    vendor_pairs: list[tuple[str, str, Optional[int]]],
    *,
    confidence: float,
) -> Optional[tuple[str, str, Optional[int], float]]:
    """Pure matcher: case-insensitive exact, then BillExtractionMapper fuzzy thresholds."""
    name = vendor_name.strip()
    if not name or not vendor_pairs:
        return None

    candidates = [(name, confidence)]
    best_match: Optional[tuple[str, str, Optional[int]]] = None
    best_combined = 0.0
    for cand_name, cand_conf in candidates:
        exact_match: Optional[tuple[str, str, Optional[int]]] = None
        for public_id, vname, vid in vendor_pairs:
            if vname and vname.strip().lower() == cand_name.strip().lower():
                exact_match = (public_id, vname, vid)
                break
        if exact_match is not None:
            combined = cand_conf * 0.99
            if combined > best_combined:
                best_combined = combined
                best_match = exact_match
            continue

        fuzzy_pairs = [(pid, vname) for pid, vname, _vid in vendor_pairs]
        match, score = BillExtractionMapper.fuzzy_match_entity_names(cand_name, fuzzy_pairs)
        if match and score >= _FUZZY_MIN_SCORE:
            combined = cand_conf * score
            if combined > best_combined:
                best_combined = combined
                vid_by_pid = {pid: vid for pid, _n, vid in vendor_pairs}
                best_match = (match[0], match[1], vid_by_pid.get(match[0]))

    if best_match and best_combined >= _COMBINED_MIN_SCORE:
        return best_match[0], best_match[1], best_match[2], best_combined
    return None


def resolve_vendor_public_id_from_name(
    vendor_name: Optional[str],
    mapper: Optional[BillExtractionMapper] = None,
    *,
    confidence: float = 0.9,
) -> Optional[str]:
    """Map a free-text vendor name through the same matcher as U-676a arm A/B."""
    if not vendor_name or not str(vendor_name).strip():
        return None
    if mapper is not None:
        engine = mapper
        stub = BillExtractionResult(
            vendor_name=vendor_name.strip(),
            vendor_confidence=confidence,
            vendor_candidates=[(vendor_name.strip(), confidence)],
        )
        resolved = engine.resolve_entities(stub)
        if resolved.vendor_match:
            return resolved.vendor_match.public_id
        return None

    matched = _match_vendor_in_pairs(
        vendor_name.strip(),
        _vendor_pairs_cached(),
        confidence=confidence,
    )
    if matched is None:
        return None
    return matched[0]


@dataclass(frozen=True)
class VendorResolveMatch:
    public_id: str
    name: str
    confidence: float
    vendor_id: Optional[int] = None


def resolve_vendor_match_from_name(
    vendor_name: Optional[str],
    mapper: Optional[BillExtractionMapper] = None,
    *,
    confidence: float = 0.9,
    cascade_confidence: Optional[float] = None,
) -> Optional[VendorResolveMatch]:
    """Return vendor match with combined confidence (min cascade, vendor score)."""
    if not vendor_name or not str(vendor_name).strip():
        return None

    if mapper is not None:
        stub = BillExtractionResult(
            vendor_name=vendor_name.strip(),
            vendor_confidence=confidence,
            vendor_candidates=[(vendor_name.strip(), confidence)],
        )
        resolved = mapper.resolve_entities(stub)
        if not resolved.vendor_match:
            return None
        return _finalize_vendor_match(
            str(resolved.vendor_match.public_id),
            resolved.vendor_match.name,
            float(resolved.vendor_match.confidence),
            cascade_confidence,
            vendor_id=getattr(resolved.vendor_match, "id", None),
        )

    matched = _match_vendor_in_pairs(
        vendor_name.strip(),
        _vendor_pairs_cached(),
        confidence=confidence,
    )
    if matched is None:
        return None
    return _finalize_vendor_match(
        matched[0], matched[1], float(matched[3]), cascade_confidence, vendor_id=matched[2]
    )


def _finalize_vendor_match(
    public_id: str,
    name: str,
    vendor_score: float,
    cascade_confidence: Optional[float],
    *,
    vendor_id: Optional[int] = None,
) -> VendorResolveMatch:
    if cascade_confidence is not None:
        vendor_score = min(float(cascade_confidence), vendor_score)
    return VendorResolveMatch(
        public_id=str(public_id),
        name=name,
        confidence=vendor_score,
        vendor_id=vendor_id,
    )
