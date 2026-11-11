"""Zip writer shared by the Box workbook editors (U-786).

drawing_graft.regraft_drawings and draw_tabs._Pkg.to_bytes both write an OOXML package the
same way. A member that exists in the source keeps its date_time, compress_type,
external_attr and create_system. A new member gets the fixed 1980-01-01 epoch and DEFLATED,
so the output depends on the content, not the wall clock.
"""

import zipfile
from io import BytesIO
from typing import Iterable, Mapping, Tuple

NEW_MEMBER_DATE = (1980, 1, 1, 0, 0, 0)


def write_package(members: Iterable[Tuple[str, bytes]], sources: Mapping[str, zipfile.ZipInfo]) -> bytes:
    """Write (name, bytes) members in the given order. `sources` maps a name to its source ZipInfo."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in members:
            src = sources.get(name)
            if src is None:
                info = zipfile.ZipInfo(name, date_time=NEW_MEMBER_DATE)
                info.compress_type = zipfile.ZIP_DEFLATED
            else:
                info = zipfile.ZipInfo(name, date_time=src.date_time)
                info.compress_type = src.compress_type
                info.external_attr = src.external_attr
                info.create_system = src.create_system
            zout.writestr(info, data)
    return buf.getvalue()
