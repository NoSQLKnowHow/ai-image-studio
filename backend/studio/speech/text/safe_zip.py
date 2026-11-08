"""Opening the archives of an EPUB or a Word file without trusting them (DESIGN.md §38.5 item 1, criterion 273).

An archive from outside is hostile until shown otherwise: a "zip bomb" is a small file that unpacks to gigabytes, and an XML file can define entities that
expand into gigabytes (the "billion laughs"). The reader process also has a memory and time limit (`isolated.py`), so these checks are the first line
and the limits the second: a refusal with a reason is better than a killed process, and the limits catch whatever the checks miss.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from typing import Optional, Union

from .errors import PrepareError

MAX_ENTRIES = 5000  # files in the archive
MAX_TOTAL_BYTES = 300 * 1024 * 1024  # all of them, unpacked
MAX_MEMBER_BYTES = 60 * 1024 * 1024  # any one of them, unpacked
MAX_XML_BYTES = 20 * 1024 * 1024  # an XML file we parse; a real OPF or document.xml is far smaller than a book's text


def open_zip(source: Union[str, bytes]) -> zipfile.ZipFile:
    """The archive at a path (or in memory), after checking how many files it holds and how big they would be unpacked. Raises PrepareError."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(source) if isinstance(source, bytes) else source)
    except (zipfile.BadZipFile, OSError) as exc:
        raise PrepareError("extract_failed", "The file is not a readable archive (it may be damaged).", "Try saving it again from the program that made it.") from exc
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise PrepareError("too_large", f"The file holds {len(infos)} parts, more than the {MAX_ENTRIES} that are allowed.")
    if sum(i.file_size for i in infos) > MAX_TOTAL_BYTES or any(i.file_size > MAX_MEMBER_BYTES for i in infos):
        raise PrepareError("too_large", "The file would unpack to far more than a book can need, so it was not opened.")
    return zf


def read_member(zf: zipfile.ZipFile, name: str, limit: int = MAX_MEMBER_BYTES) -> Optional[bytes]:
    """One file of the archive, or None if it is not there. At most `limit` bytes are read, whatever the archive claims about its size."""
    try:
        with zf.open(name) as handle:
            data = handle.read(limit + 1)
    except KeyError:
        return None
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError) as exc:  # a damaged or encrypted member
        raise PrepareError("extract_failed", f"Part of the file could not be read ({name}).") from exc
    if len(data) > limit:
        raise PrepareError("too_large", f"Part of the file ({name}) is far larger than it should be, so it was not read.")
    return data


def parse_xml(data: bytes) -> ET.Element:
    """Parse XML that came from outside. An entity definition is refused (that is how an expansion bomb is made); a document type declaration without
    one is allowed, because real EPUB navigation files have them and the parser does not fetch what they point to."""
    if len(data) > MAX_XML_BYTES:
        raise PrepareError("too_large", "An XML part of the file is larger than any real one would be.")
    if b"<!ENTITY" in data or b"<!entity" in data:
        raise PrepareError("extract_failed", "The file defines XML entities, which a book never needs, so it was not read.")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise PrepareError("extract_failed", "Part of the file is not valid XML.") from exc
