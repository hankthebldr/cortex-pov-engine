# core/integrations/xsiam/archives.py
"""Read a ZIP reply in memory: ``playbooks/get`` and ``scripts/get``.

PANW's Playbooks and Scripts pages say each returns the object's YAML "in a ZIP
file" (tenant-reader spec §3.4, §8.1). The catalog marks those ops ``zip-reply``;
the executor fetches their bytes with ``XsiamClient.request_archive`` and hands
them here.

The archive is read with stdlib ``zipfile`` over a ``BytesIO``: nothing is
extracted and no member name is ever used as a path. A member name is data —
neutralised (invisible, control and bidi characters stripped, capped) and
returned as a string. Caps hold on what is read, not only on what the archive
declares:

* a member whose declared size is over ``max_member_bytes``, or over what is left
  of ``max_total_bytes``, is refused without being opened;
* a member is read at most up to that limit, so one that inflates past its
  declared size (a zip bomb) is never read past it — zipfile stops at the
  declared size and the CRC check then fails, which reads as ``UNREADABLE``;
* at most ``max_members`` file members are listed.

Text is UTF-8 only: anything else is ``binary: true`` with no text.
"""
from __future__ import annotations

import io
import zipfile
import zlib
from typing import Any

from .exceptions import XsiamParseError

try:  # an interpreter built without liblzma cannot read LZMA members at all
    from lzma import LZMAError as _LZMAError
except ImportError:  # pragma: no cover
    _LZMAError = zlib.error

#: The local-file-header signature every non-empty ZIP starts with.
ZIP_MAGIC = b"PK\x03\x04"

MAX_MEMBERS = 50
MAX_MEMBER_BYTES = 262144        # 256 KiB
MAX_TOTAL_BYTES = 1048576        # 1 MiB

#: ``refused`` values: why a member's bytes were not returned.
MEMBER_OVER_CAP = "MEMBER_OVER_CAP"
TOTAL_OVER_CAP = "TOTAL_OVER_CAP"
UNREADABLE = "UNREADABLE"

_NAME_CUT = " [cut at 200 characters]"
#: What reading one member can raise: a corrupt or lying entry, an unsupported
#: or broken compression stream, an encrypted member. The message is never
#: echoed: zipfile quotes the member name in it.
_MEMBER_ERRORS = (zipfile.BadZipFile, EOFError, OSError, ValueError, RuntimeError,
                  NotImplementedError, zlib.error, _LZMAError)


def _name(raw: str) -> str:
    """The member name as data: neutralised, and at most ``TEXT_CAP`` characters
    with the cut marker inside the cap, so neutralising it again changes nothing."""
    from connectors.tenant_reader import TEXT_CAP, neutralise_text  # noqa: PLC0415

    name, cut = neutralise_text(raw)
    return f"{name[:TEXT_CAP - len(_NAME_CUT)]}{_NAME_CUT}" if cut else name


def _member(name: str, *, text: Any = None, size: int, text_truncated: bool = False,
            binary: Any = None, refused: Any = None) -> dict:
    return {"name": name, "text": text, "bytes": size, "text_truncated": text_truncated,
            "binary": binary, "refused": refused}


def zip_members(blob: bytes, *, max_members: int = MAX_MEMBERS,
                max_member_bytes: int = MAX_MEMBER_BYTES,
                max_total_bytes: int = MAX_TOTAL_BYTES) -> dict:
    """``{"members": [...], "member_count", "members_truncated"}`` for a ZIP held
    in memory.

    Each member is ``{"name", "text", "bytes", "text_truncated", "binary",
    "refused"}``:

    * read whole: ``bytes`` is what was read; ``text`` is its UTF-8 decoding and
      ``binary`` false, or ``text`` null and ``binary`` true when it is not UTF-8;
    * refused for a cap (``refused`` ``MEMBER_OVER_CAP`` or ``TOTAL_OVER_CAP``):
      never opened; ``text`` null, ``text_truncated`` true, ``binary`` null (not
      known), ``bytes`` the size the archive declares;
    * ``UNREADABLE``: opening or reading it failed (corrupt, encrypted, an
      unsupported compression, or bytes that do not match the declared size and
      CRC); ``text`` and ``binary`` null, ``bytes`` the declared size.

    ``member_count`` counts the archive's file members (directory entries are
    not members); ``members_truncated`` is true when more than ``max_members``
    were there. A blob zipfile cannot open is :class:`XsiamParseError`, naming
    the failure's kind only.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except (zipfile.BadZipFile, EOFError, OSError, ValueError) as exc:
        raise XsiamParseError(
            f"the tenant's ZIP reply ({len(blob)} bytes) could not be opened as an "
            f"archive ({type(exc).__name__})") from exc
    with zf:
        files = [info for info in zf.infolist() if not info.is_dir()]
        members: list[dict] = []
        total = 0
        for info in files[:max_members]:
            name, declared = _name(info.filename), info.file_size
            left = max_total_bytes - total
            if declared > max_member_bytes:
                members.append(_member(name, size=declared, text_truncated=True,
                                       refused=MEMBER_OVER_CAP))
                continue
            if declared > left:
                members.append(_member(name, size=declared, text_truncated=True,
                                       refused=TOTAL_OVER_CAP))
                continue
            limit = min(max_member_bytes, left)
            try:
                with zf.open(info) as fh:
                    data = fh.read(limit + 1)
            except _MEMBER_ERRORS:
                members.append(_member(name, size=declared, refused=UNREADABLE))
                continue
            if len(data) > limit:
                # Read past what it declared and past the cap: not returned.
                members.append(_member(name, size=declared, text_truncated=True,
                                       refused=MEMBER_OVER_CAP))
                continue
            total += len(data)
            try:
                members.append(_member(name, text=data.decode("utf-8"), size=len(data),
                                       binary=False))
            except UnicodeDecodeError:
                members.append(_member(name, size=len(data), binary=True))
    return {"members": members, "member_count": len(files),
            "members_truncated": len(files) > max_members}
