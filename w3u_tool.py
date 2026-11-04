#!/usr/bin/env python3
"""Inspect, export, edit and rebuild Warcraft III object-data files.

The tool uses only the Python standard library and is intended for .w3u files.
The same record format is also used by .w3t/.w3b/.w3d/.w3a/.w3h/.w3q.

Examples:
    python w3u_tool.py inspect 1.w3u
    python w3u_tool.py export 1.w3u
    python w3u_tool.py build 1.w3u.json 1.modified.w3u
    python w3u_tool.py rename 1.w3u 1.renamed.w3u h000=H001
    python w3u_tool.py verify 1.w3u
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import struct
import sys
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


FORMAT_ID = "wc3-object-data"
FIXED_TYPES = {0, 1, 2, 4, 5}
STRING_TYPES = {3, *range(6, 22)}
OPTIONAL_INT_EXTENSIONS = {".w3a", ".w3d", ".w3q"}
OBJECT_EXTENSIONS = {".w3u", ".w3t", ".w3b", ".w3d", ".w3a", ".w3h", ".w3q"}

TYPE_NAMES = {
    0: "int",
    1: "real",
    2: "unreal",
    3: "string",
    4: "bool",
    5: "char",
    6: "unitList",
    7: "itemList",
    8: "regenType",
    9: "attackType",
    10: "weaponType",
    11: "targetType",
    12: "moveType",
    13: "defenseType",
    14: "pathingTexture",
    15: "upgradeList",
    16: "stringList",
    17: "abilityList",
    18: "heroAbilityList",
    19: "missileArt",
    20: "attributeType",
    21: "attackBits",
}


class W3UError(Exception):
    pass


@dataclass
class ObjectData:
    version: int
    extension: str = ".w3u"
    uses_optional_ints: bool = False
    tables: List[Dict[str, Any]] = field(default_factory=list)
    trailing_hex: str = ""


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, size: int) -> bytes:
        if size < 0 or self.pos + size > len(self.data):
            raise W3UError(
                f"unexpected end of file at 0x{self.pos:X}; need {size} byte(s)"
            )
        value = self.data[self.pos : self.pos + size]
        self.pos += size
        return value

    def i32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def cstring_bytes(self) -> bytes:
        end = self.data.find(b"\0", self.pos)
        if end < 0:
            raise W3UError(f"unterminated string at 0x{self.pos:X}")
        value = self.data[self.pos : end]
        self.pos = end + 1
        return value


def rawcode_to_text(raw: bytes) -> Optional[str]:
    if raw == b"\0\0\0\0":
        return None
    # Object IDs are four bytes. latin-1 provides a lossless one-byte mapping.
    return raw.decode("latin-1")


def text_to_rawcode(value: Any, allow_zero: bool = True) -> bytes:
    if value is None or value == "":
        if allow_zero:
            return b"\0\0\0\0"
        raise W3UError("an object/field ID cannot be empty")
    if not isinstance(value, str):
        raise W3UError(f"rawcode must be a string, got {type(value).__name__}")
    try:
        raw = value.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise W3UError(f"rawcode is not 8-bit text: {value!r}") from exc
    if len(raw) != 4:
        raise W3UError(f"rawcode must be exactly 4 bytes: {value!r} ({len(raw)})")
    if raw == b"\0\0\0\0" and not allow_zero:
        raise W3UError("this rawcode cannot be 0000")
    return raw


def rawcode_number(raw: bytes) -> int:
    return struct.unpack("<i", raw)[0]


def decode_text(raw: bytes) -> Tuple[str, str]:
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1"), "latin-1"


def encode_text(value: str, encoding: str = "utf-8") -> bytes:
    if not isinstance(value, str):
        value = str(value)
    for candidate in (encoding, "utf-8", "gb18030", "latin-1"):
        try:
            return value.encode(candidate)
        except UnicodeEncodeError:
            continue
    raise W3UError(f"cannot encode text value: {value!r}")


def decode_value(raw: bytes, type_id: int) -> Tuple[Any, str]:
    if type_id == 0:
        return struct.unpack("<i", raw)[0], "int"
    if type_id in (1, 2):
        return struct.unpack("<f", raw)[0], "float"
    if type_id == 4:
        return struct.unpack("<i", raw)[0], "bool"
    if type_id == 5:
        return list(raw), "bytes"
    text_raw = raw[:-1] if raw.endswith(b"\0") else raw
    return decode_text(text_raw)


def encode_value(value: Any, type_id: int, encoding: str) -> bytes:
    if type_id == 0:
        return struct.pack("<i", int(value))
    if type_id in (1, 2):
        return struct.pack("<f", float(value))
    if type_id == 4:
        if isinstance(value, bool):
            return struct.pack("<i", 1 if value else 0)
        return struct.pack("<i", int(value))
    if type_id == 5:
        if isinstance(value, list):
            raw = bytes(int(x) & 0xFF for x in value)
            if len(raw) != 4:
                raise W3UError("char value must contain exactly 4 bytes")
            return raw
        if isinstance(value, int):
            return struct.pack("<i", value)
        return encode_text(str(value), encoding)
    return encode_text(str(value), encoding) + b"\0"


def bytes_from_hex(value: str) -> bytes:
    try:
        return bytes.fromhex(value.replace(" ", "").strip())
    except ValueError as exc:
        raise W3UError(f"invalid hex string: {value!r}") from exc


def candidate_value_ends(data: bytes, start: int, type_id: int) -> List[int]:
    candidates: List[int] = []
    if type_id in FIXED_TYPES:
        candidates.append(start + 4)
    elif type_id in STRING_TYPES:
        end = data.find(b"\0", start)
        if end >= 0:
            candidates.append(end + 1)
    return [pos for pos in candidates if 0 <= pos <= len(data) - 4]


def valid_terminator(value: int, original_id: int, new_id: int) -> bool:
    return value in (0, original_id, new_id)


def decode_modification(
    data: bytes,
    value_start: int,
    value_end: int,
    type_id: int,
    field_raw: bytes,
    level: Optional[int],
    data_pointer: Optional[int],
    end_value: int,
) -> Dict[str, Any]:
    raw = data[value_start:value_end]
    value, encoding = decode_value(raw, type_id)
    return {
        "field_id": rawcode_to_text(field_raw),
        "field_hex": field_raw.hex(),
        "type": type_id,
        "type_name": TYPE_NAMES.get(type_id, "unknown"),
        "level": level,
        "data_pointer": data_pointer,
        "value": value,
        "original_value": value,
        "encoding": encoding,
        "raw_hex": raw.hex(),
        "end": end_value,
        "end_hex": f"{end_value & 0xFFFFFFFF:08x}",
    }


def parse_modifications(
    reader: Reader,
    count: int,
    original_id: int,
    new_id: int,
    uses_optional_ints: bool,
) -> List[Dict[str, Any]]:
    if count == 0:
        return []
    if count < 0 or count > 10_000_000:
        raise W3UError(f"invalid modification count: {count}")

    start_pos = reader.pos
    field_raw = reader.take(4)
    type_id = reader.i32()
    level = reader.i32() if uses_optional_ints else None
    data_pointer = reader.i32() if uses_optional_ints else None
    value_start = reader.pos

    candidates = candidate_value_ends(reader.data, value_start, type_id)
    if not candidates:
        # Unknown type: try every endpoint whose next int is a legal record
        # terminator. Recursion confirms the rest of the object.
        for endpos in range(value_start, len(reader.data) - 3):
            end_value = struct.unpack_from("<i", reader.data, endpos)[0]
            if valid_terminator(end_value, original_id, new_id):
                candidates.append(endpos)

    last_error: Optional[Exception] = None
    for value_end in candidates:
        end_value = struct.unpack_from("<i", reader.data, value_end)[0]
        if type_id in FIXED_TYPES or type_id in STRING_TYPES:
            if not valid_terminator(end_value, original_id, new_id):
                continue
        reader.pos = value_end + 4
        try:
            rest = parse_modifications(
                reader, count - 1, original_id, new_id, uses_optional_ints
            )
            modification = decode_modification(
                reader.data,
                value_start,
                value_end,
                type_id,
                field_raw,
                level,
                data_pointer,
                end_value,
            )
            return [modification] + rest
        except W3UError as exc:
            last_error = exc
            reader.pos = start_pos

    raise W3UError(
        f"cannot decode modification at 0x{start_pos:X} "
        f"(field={rawcode_to_text(field_raw)!r}, type={type_id}): {last_error}"
    )


def parse_object_data(data: bytes, extension: str = ".w3u") -> ObjectData:
    extension = extension.lower()
    if extension not in OBJECT_EXTENSIONS:
        extension = ".w3u"
    uses_optional_ints = extension in OPTIONAL_INT_EXTENSIONS
    reader = Reader(data)
    version = reader.i32()
    if version < 0 or version > 100:
        raise W3UError(f"unexpected object-data version: {version}")

    tables: List[Dict[str, Any]] = []
    for table_name in ("original", "custom"):
        table_start = reader.pos
        count = reader.i32()
        if count < 0 or count > 1_000_000:
            raise W3UError(
                f"invalid {table_name} object count {count} at 0x{table_start:X}"
            )
        objects: List[Dict[str, Any]] = []
        for index in range(count):
            object_start = reader.pos
            original_raw = reader.take(4)
            new_raw = reader.take(4)
            mod_count = reader.i32()
            if mod_count < 0 or mod_count > 10_000_000:
                raise W3UError(
                    f"invalid mod count {mod_count} for {table_name}[{index}] "
                    f"at 0x{object_start:X}"
                )
            modifications = parse_modifications(
                reader,
                mod_count,
                rawcode_number(original_raw),
                rawcode_number(new_raw),
                uses_optional_ints,
            )
            objects.append(
                {
                    "index": index,
                    "original_id": rawcode_to_text(original_raw),
                    "original_id_hex": original_raw.hex(),
                    "new_id": rawcode_to_text(new_raw),
                    "new_id_hex": new_raw.hex(),
                    "modification_count": mod_count,
                    "modifications": modifications,
                }
            )
        tables.append({"name": table_name, "count": count, "objects": objects})

    return ObjectData(
        version=version,
        extension=extension,
        uses_optional_ints=uses_optional_ints,
        tables=tables,
        trailing_hex=data[reader.pos :].hex(),
    )


def write_modification(
    writer: io.BytesIO, modification: Dict[str, Any], uses_optional_ints: bool
) -> None:
    field_raw = text_to_rawcode(modification.get("field_id"), allow_zero=False)
    type_id = int(modification["type"])
    writer.write(field_raw)
    writer.write(struct.pack("<i", type_id))
    if uses_optional_ints:
        writer.write(struct.pack("<i", int(modification.get("level") or 0)))
        writer.write(struct.pack("<i", int(modification.get("data_pointer") or 0)))

    original_value = modification.get("original_value", modification.get("value"))
    value = modification.get("value")
    raw_hex = modification.get("raw_hex", "")
    use_raw = bool(raw_hex) and value == original_value
    if use_raw:
        value_raw = bytes_from_hex(raw_hex)
        if type_id in FIXED_TYPES and len(value_raw) != 4:
            use_raw = False
    if not use_raw:
        value_raw = encode_value(value, type_id, modification.get("encoding", "utf-8"))
    writer.write(value_raw)
    writer.write(struct.pack("<i", int(modification.get("end", 0))))


def document_to_json(document: ObjectData) -> Dict[str, Any]:
    return {
        "format": FORMAT_ID,
        "extension": document.extension,
        "version": document.version,
        "uses_optional_ints": document.uses_optional_ints,
        "tables": document.tables,
        "trailing_hex": document.trailing_hex,
    }


def build_object_data(document: Dict[str, Any]) -> bytes:
    if document.get("format") not in (None, FORMAT_ID):
        raise W3UError(f"unsupported JSON format: {document.get('format')!r}")
    tables = document.get("tables", [])
    if len(tables) != 2:
        raise W3UError("JSON must contain exactly two tables")
    output = io.BytesIO()
    output.write(struct.pack("<i", int(document.get("version", 1))))
    uses_optional_ints = bool(document.get("uses_optional_ints", False))
    for table in tables:
        objects = table.get("objects", [])
        output.write(struct.pack("<i", len(objects)))
        for obj in objects:
            original_raw = text_to_rawcode(obj.get("original_id"), allow_zero=False)
            new_raw = text_to_rawcode(obj.get("new_id"), allow_zero=True)
            old_new_raw = new_raw
            old_new_hex = obj.get("new_id_hex", "")
            if old_new_hex:
                try:
                    old_new_raw = bytes_from_hex(old_new_hex)
                except W3UError:
                    old_new_raw = new_raw
            output.write(original_raw)
            output.write(new_raw)
            modifications = obj.get("modifications", [])
            output.write(struct.pack("<i", len(modifications)))
            for modification in modifications:
                item = modification
                if rawcode_number(old_new_raw) != rawcode_number(new_raw):
                    if modification.get("end") == rawcode_number(old_new_raw):
                        item = dict(modification)
                        item["end"] = rawcode_number(new_raw)
                write_modification(output, item, uses_optional_ints)
    trailing = document.get("trailing_hex", "")
    if trailing:
        output.write(bytes_from_hex(trailing))
    return output.getvalue()


def save_json(document: ObjectData, path: Path) -> None:
    text = json.dumps(document_to_json(document), ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")


def flatten(document: ObjectData) -> Iterable[Dict[str, Any]]:
    for table in document.tables:
        for obj in table["objects"]:
            if not obj["modifications"]:
                yield {
                    "table": table["name"],
                    "object_index": obj["index"],
                    "mod_index": "",
                    "original_id": obj["original_id"] or "",
                    "new_id": obj["new_id"] or "",
                    "field_id": "",
                    "field_hex": "",
                    "type": "",
                    "type_name": "",
                    "level": "",
                    "data_pointer": "",
                    "value": "",
                    "raw_hex": "",
                    "end": "",
                }
                continue
            for mod_index, modification in enumerate(obj["modifications"]):
                yield {
                    "table": table["name"],
                    "object_index": obj["index"],
                    "mod_index": mod_index,
                    "original_id": obj["original_id"] or "",
                    "new_id": obj["new_id"] or "",
                    "field_id": modification["field_id"],
                    "field_hex": modification["field_hex"],
                    "type": modification["type"],
                    "type_name": modification["type_name"],
                    "level": ""
                    if modification["level"] is None
                    else modification["level"],
                    "data_pointer": ""
                    if modification["data_pointer"] is None
                    else modification["data_pointer"],
                    "value": modification["value"],
                    "raw_hex": modification["raw_hex"],
                    "end": modification["end"],
                }


def save_csv(document: ObjectData, path: Path) -> None:
    fieldnames = [
        "table",
        "object_index",
        "mod_index",
        "original_id",
        "new_id",
        "field_id",
        "field_hex",
        "type",
        "type_name",
        "level",
        "data_pointer",
        "value",
        "raw_hex",
        "end",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(flatten(document))


def field_header_key(field_id: str) -> str:
    raw = field_id.encode("latin-1")
    if any(byte < 32 or byte == 127 for byte in raw):
        return f"@hex:{raw.hex()}"
    return field_id


def _header_parts(header: str) -> Tuple[str, str]:
    if " [" in header:
        raw, label = header.split(" [", 1)
        raw, label = raw.strip(), label.rstrip("]").strip()
    elif " / " in header:
        raw, label = header.split(" / ", 1)
        raw, label = raw.strip(), label.strip()
    else:
        raw, label = header.strip(), header.strip()
    if raw.startswith("@hex:"):
        try:
            raw = bytes.fromhex(raw[5:]).decode("latin-1")
        except (ValueError, UnicodeDecodeError):
            pass
    return raw, label


def _column_letter(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _xlsx_text(value: Any) -> str:
    text = "" if value is None else str(value)
    parts: List[str] = []
    for char in text:
        codepoint = ord(char)
        if codepoint < 32 and char not in "\t\n":
            parts.append(f"_x{codepoint:04X}_")
        else:
            parts.append(char)
    return "".join(parts)


def _xlsx_decode_text(value: str) -> str:
    return re.sub(
        r"_x([0-9A-Fa-f]{4})_",
        lambda match: chr(int(match.group(1), 16)),
        value,
    )


def _xlsx_cell(reference: str, value: Any, style: int) -> str:
    text = _xlsx_text(value)
    if text == "":
        return f'<c r="{reference}" s="{style}"/>'
    return (
        f'<c r="{reference}" s="{style}" t="inlineStr">'
        f'<is><t xml:space="preserve">{escape(text)}</t></is></c>'
    )


def write_styled_xlsx(
    path: Path,
    columns: List[str],
    rows: List[Dict[str, Any]],
    metadata: List[Dict[str, str]],
    localized: Dict[str, str],
) -> None:
    identifiers = {
        "table": ("Table", "表类型"),
        "object_index": ("Object Index", "对象索引"),
        "object_id": ("Object ID", "对象编码"),
        "base_id": ("Base ID", "原始编码"),
        "level": ("Level", "等级"),
    }
    meta_by_id = {meta.get("ID", ""): meta for meta in metadata if meta.get("ID")}
    english_headers: List[str] = []
    chinese_headers: List[str] = []
    for header in columns:
        raw, label = _header_parts(header)
        if raw in identifiers:
            english, chinese = identifiers[raw]
            english_headers.append(f"{raw} / {english}")
            chinese_headers.append(chinese)
            continue
        meta = meta_by_id.get(raw, {})
        raw_display = field_header_key(raw)
        english_name = _xlsx_text(meta.get("field", raw) or raw)
        display_key = meta.get("displayName", "")
        chinese = label or localized.get(display_key, english_name)
        english_headers.append(
            raw_display
            if english_name == raw_display
            else f"{raw_display} / {english_name}"
        )
        chinese_headers.append(chinese or raw_display)

    max_row = len(rows) + 2
    max_col = len(columns)
    last_ref = f"{_column_letter(max_col)}{max_row}"

    sheet_rows: List[str] = []
    sheet_rows.append(
        '<row r="1" ht="30" customHeight="1">'
        + "".join(
            _xlsx_cell(f"{_column_letter(index)}1", value, 1)
            for index, value in enumerate(english_headers, 1)
        )
        + "</row>"
    )
    sheet_rows.append(
        '<row r="2" ht="24" customHeight="1">'
        + "".join(
            _xlsx_cell(f"{_column_letter(index)}2", value, 2)
            for index, value in enumerate(chinese_headers, 1)
        )
        + "</row>"
    )
    for row_index, row in enumerate(rows, 3):
        table_value = _xlsx_text(row.get(columns[0], ""))
        cells: List[str] = []
        for col_index, header in enumerate(columns, 1):
            if col_index <= (5 if "level" in identifiers and rows and "level" in rows[0] else 4):
                style = 3
            elif table_value == "original":
                style = 4
            else:
                style = 5 if row_index % 2 == 1 else 6
            cells.append(
                _xlsx_cell(
                    f"{_column_letter(col_index)}{row_index}",
                    row.get(header, ""),
                    style,
                )
            )
        sheet_rows.append(f'<row r="{row_index}">' + "".join(cells) + "</row>")

    freeze_columns = 5 if rows and "level" in rows[0] else 4
    freeze_ref = f"{_column_letter(freeze_columns + 1)}3"
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:{last_ref}"/>'
        "<sheetViews><sheetView workbookViewId=\"0\"><pane "
        f'xSplit="{freeze_columns}" ySplit="2" topLeftCell="{freeze_ref}" '
        'activePane="bottomRight" state="frozen"/></sheetView></sheetViews>'
        '<sheetFormatPr defaultRowHeight="18"/>'
        "<cols>"
        '<col min="1" max="2" width="15" customWidth="1"/>'
        '<col min="3" max="4" width="13" customWidth="1"/>'
        '<col min="5" max="5" width="9" customWidth="1"/>'
        f'<col min="6" max="{max_col}" width="22" customWidth="1"/>'
        "</cols><sheetData>"
        + "".join(sheet_rows)
        + "</sheetData>"
        + f'<autoFilter ref="A2:{last_ref}"/>'
        + "</worksheet>"
    )

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="4">'
        '<font><sz val="10"/><name val="Microsoft YaHei UI"/></font>'
        '<font><b/><color rgb="FFFFFFFF"/><sz val="10"/><name val="Microsoft YaHei UI"/></font>'
        '<font><b/><color rgb="FF174D3D"/><sz val="10"/><name val="Microsoft YaHei UI"/></font>'
        '<font><color rgb="FF52665B"/><sz val="9"/><name val="Microsoft YaHei UI"/></font>'
        "</fonts>"
        '<fills count="7">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF1D624D"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFDCE9DF"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFE9EEF2"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFEEF3F8"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFF6FAF5"/><bgColor indexed="64"/></patternFill></fill>'
        "</fills>"
        '<borders count="2"><border/><border>'
        '<left style="thin"><color rgb="FFD3DED5"/></left>'
        '<right style="thin"><color rgb="FFD3DED5"/></right>'
        '<top style="thin"><color rgb="FFD3DED5"/></top>'
        '<bottom style="thin"><color rgb="FFD3DED5"/></bottom>'
        "</border></borders>"
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="7">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFill="1" applyFont="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>'
        '<xf numFmtId="0" fontId="2" fillId="3" borderId="1" xfId="0" applyFill="1" applyFont="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>'
        '<xf numFmtId="0" fontId="3" fillId="4" borderId="1" xfId="0" applyFill="1" applyFont="1"/>'
        '<xf numFmtId="0" fontId="0" fillId="5" borderId="1" xfId="0" applyFill="1"/>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0"/>'
        '<xf numFmtId="0" fontId="0" fillId="6" borderId="1" xfId="0" applyFill="1"/>'
        "</cellXfs>"
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="物编表" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/styles.xml", styles_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def read_xlsx_dicts(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            shared: List[str] = []
            if "xl/sharedStrings.xml" in names:
                root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                for item in root.iter():
                    if item.tag.endswith("}si"):
                        shared.append(
                            "".join(
                                node.text or ""
                                for node in item.iter()
                                if node.tag.endswith("}t")
                            )
                        )
            worksheets = sorted(
                name
                for name in names
                if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
            )
            if not worksheets:
                raise W3UError(f"no worksheet found in {path}")
            root = ET.fromstring(archive.read(worksheets[0]))
    except (zipfile.BadZipFile, ET.ParseError) as exc:
        raise W3UError(f"cannot read XLSX workbook: {path}") from exc

    values: Dict[int, Dict[int, str]] = {}
    for row in root.iter():
        if not row.tag.endswith("}row"):
            continue
        try:
            row_number = int(row.get("r", "0"))
        except ValueError:
            continue
        row_values: Dict[int, str] = {}
        next_column = 1
        for cell in row:
            if not cell.tag.endswith("}c"):
                continue
            reference = cell.get("r", "")
            match = re.match(r"([A-Z]+)", reference)
            if match:
                column = 0
                for char in match.group(1):
                    column = column * 26 + ord(char) - 64
            else:
                column = next_column
            next_column = column + 1
            cell_type = cell.get("t", "")
            text = ""
            if cell_type == "inlineStr":
                text = "".join(
                    node.text or ""
                    for node in cell.iter()
                    if node.tag.endswith("}t")
                )
            else:
                value_node = next(
                    (node for node in cell if node.tag.endswith("}v")), None
                )
                raw_value = value_node.text if value_node is not None else ""
                if cell_type == "s" and raw_value:
                    text = shared[int(raw_value)]
                else:
                    text = raw_value or ""
            row_values[column] = _xlsx_decode_text(text)
        values[row_number] = row_values

    if 1 not in values:
        raise W3UError(f"XLSX has no header row: {path}")
    max_column = max(values[1]) if values[1] else 0
    headers = [
        _header_parts(values[1].get(column, ""))[0]
        for column in range(1, max_column + 1)
    ]
    rows: List[Dict[str, str]] = []
    for row_number in sorted(values):
        if row_number < 3:
            continue
        source = values[row_number]
        row = {
            headers[column - 1]: source.get(column, "")
            for column in range(1, max_column + 1)
            if headers[column - 1]
        }
        if any(str(value).strip() for value in row.values()):
            rows.append(row)
    return headers, rows


def read_csv_dicts(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    if path.suffix.casefold() == ".xlsx":
        return read_xlsx_dicts(path)
    raw = path.read_bytes()
    text = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise W3UError(
            f"cannot decode CSV as UTF-8 or GB18030: {path}; "
            "save it as CSV UTF-8 or a Chinese-code-page CSV"
        )
    reader = csv.DictReader(io.StringIO(text, newline=""))
    rows = list(reader)
    return reader.fieldnames or [], rows


def write_table(
    path: Path,
    columns: List[str],
    rows: List[Dict[str, Any]],
    metadata: List[Dict[str, str]],
    localized: Dict[str, str],
) -> None:
    if path.suffix.casefold() == ".xlsx":
        write_styled_xlsx(path, columns, rows, metadata, localized)
        return
    if path.suffix.casefold() != ".csv":
        raise W3UError("table output must use .csv or .xlsx")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def export_modifications_csv(
    document: ObjectData,
    metadata: List[Dict[str, str]],
    localized: Dict[str, str],
    output: Path,
) -> int:
    metadata_by_id = {row["ID"]: row for row in metadata if row.get("ID")}
    columns = [
        "table",
        "object_index",
        "object_id",
        "base_id",
        "mod_index",
        "field_id",
        "field_name",
        "level",
        "data_pointer",
        "type",
        "type_name",
        "value",
    ]
    rows: List[Dict[str, Any]] = []
    for table in document.tables:
        for obj in table["objects"]:
            object_id = (
                obj["new_id"] if table["name"] == "custom" else obj["original_id"]
            ) or obj["original_id"]
            if not obj["modifications"]:
                rows.append(
                    {
                        "table": table["name"],
                        "object_index": obj["index"],
                        "object_id": object_id,
                        "base_id": obj["original_id"],
                        "mod_index": "",
                        "field_id": "",
                        "field_name": "",
                        "level": "",
                        "data_pointer": "",
                        "type": "",
                        "type_name": "",
                        "value": "",
                    }
                )
                continue

            for index, mod in enumerate(obj["modifications"]):
                meta = metadata_by_id.get(mod["field_id"], {})
                display_key = meta.get("displayName", "")
                label = localized.get(
                    display_key, display_key or meta.get("field", "")
                )
                if mod["level"] is not None or mod["data_pointer"] is not None:
                    label = (
                        f"{label} [L{mod['level']};D{mod['data_pointer']}]"
                        if label
                        else f"L{mod['level']};D{mod['data_pointer']}"
                    )
                rows.append(
                    {
                        "table": table["name"],
                        "object_index": obj["index"],
                        "object_id": object_id,
                        "base_id": obj["original_id"],
                        "mod_index": index,
                        "field_id": mod["field_id"],
                        "field_name": label or mod["field_id"],
                        "level": "" if mod["level"] is None else mod["level"],
                        "data_pointer": ""
                        if mod["data_pointer"] is None
                        else mod["data_pointer"],
                        "type": mod["type"],
                        "type_name": TYPE_NAMES.get(mod["type"], "unknown"),
                        "value": mod["value"],
                    }
                )

    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def csv_mod_value(value: str, value_type: int) -> Any:
    if value_type == 0:
        return int(value.strip() or "0")
    if value_type in (1, 2):
        return float(value.strip() or "0")
    if value_type == 3:
        return value
    raise W3UError(f"unsupported modification value type: {value_type}")


def apply_modifications_csv(document: ObjectData, csv_path: Path) -> int:
    _headers, rows = read_csv_dicts(csv_path)
    objects: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for table in document.tables:
        for obj in table["objects"]:
            objects[(table["name"], obj["index"])] = obj

    grouped: Dict[Tuple[str, int], List[Dict[str, str]]] = {}
    for row in rows:
        try:
            key = (row.get("table", ""), int(row.get("object_index", "")))
        except (TypeError, ValueError) as exc:
            raise W3UError("CSV object_index is missing or invalid") from exc
        obj = objects.get(key)
        if obj is None:
            raise W3UError(f"CSV object not found in source file: {key[0]}[{key[1]}]")
        expected_id = (
            obj["new_id"] if key[0] == "custom" else obj["original_id"]
        ) or obj["original_id"]
        if row.get("object_id", "") != expected_id:
            raise W3UError(
                f"object ID changed in CSV at {key[0]}[{key[1]}]; "
                "keep table, object_index and object_id unchanged"
            )
        grouped.setdefault(key, []).append(row)

    missing = set(objects) - set(grouped)
    if missing:
        table_name, index = sorted(missing)[0]
        raise W3UError(
            f"CSV is missing object {table_name}[{index}]; keep at least one row "
            "for every object"
        )

    changed = 0
    for key, obj in objects.items():
        old_mods = obj["modifications"]
        new_mods: List[Dict[str, Any]] = []
        seen_indices = set()
        csv_rows = sorted(
            grouped[key],
            key=lambda item: (
                not bool(item.get("mod_index", "").strip()),
                int(item["mod_index"])
                if item.get("mod_index", "").strip()
                else 0,
            ),
        )

        for row in csv_rows:
            field_id = row.get("field_id", "").strip()
            index_text = row.get("mod_index", "").strip()
            if not field_id:
                # Empty placeholder rows represent objects with zero modifiers.
                continue
            field_raw = text_to_rawcode(field_id, allow_zero=False)
            try:
                value_type = int(row.get("type", ""))
                if value_type not in (0, 1, 2, 3):
                    raise ValueError
                value = csv_mod_value(row.get("value", ""), value_type)
            except (TypeError, ValueError) as exc:
                raise W3UError(
                    f"invalid type/value in {key[0]}[{key[1]}] field {field_id}"
                ) from exc

            if index_text:
                try:
                    mod_index = int(index_text)
                except ValueError as exc:
                    raise W3UError(f"invalid mod_index: {index_text!r}") from exc
                if mod_index < 0 or mod_index >= len(old_mods):
                    raise W3UError(
                        f"mod_index {mod_index} is out of range for "
                        f"{key[0]}[{key[1]}]"
                    )
                if mod_index in seen_indices:
                    raise W3UError(
                        f"duplicate modification row {key[0]}[{key[1]}]#{mod_index}"
                    )
                seen_indices.add(mod_index)
                mod = dict(old_mods[mod_index])
                original_field_id = mod["field_id"]
                original_type = mod["type"]
                original_value = mod["value"]
                original_level = mod["level"]
                original_data_pointer = mod["data_pointer"]
                mod["field_id"] = field_id
                mod["field_hex"] = field_raw.hex()
                mod["type"] = value_type
                mod["type_name"] = TYPE_NAMES[value_type]
                mod["value"] = value
                mod["original_value"] = value
                if (
                    value != original_value
                    or value_type != original_type
                    or field_id != original_field_id
                ):
                    mod["raw_hex"] = ""
                    changed += 1
                if document.uses_optional_ints:
                    try:
                        mod["level"] = int(row.get("level", "") or 0)
                        mod["data_pointer"] = int(row.get("data_pointer", "") or 0)
                    except ValueError as exc:
                        raise W3UError(
                            f"invalid level/data_pointer for {key[0]}[{key[1]}]"
                        ) from exc
                    if (
                        mod["level"] != original_level
                        or mod["data_pointer"] != original_data_pointer
                    ):
                        changed += 1
                new_mods.append(mod)
            else:
                if document.uses_optional_ints:
                    try:
                        level = int(row.get("level", "") or 0)
                        data_pointer = int(row.get("data_pointer", "") or 0)
                    except ValueError as exc:
                        raise W3UError(
                            f"invalid level/data_pointer for {key[0]}[{key[1]}]"
                        ) from exc
                else:
                    level = data_pointer = None
                object_id = obj["new_id"] or obj["original_id"]
                end_value = int.from_bytes(
                    text_to_rawcode(object_id, allow_zero=False),
                    "little",
                    signed=True,
                )
                new_mods.append(
                    {
                        "field_id": field_id,
                        "field_hex": field_raw.hex(),
                        "type": value_type,
                        "type_name": TYPE_NAMES[value_type],
                        "level": level,
                        "data_pointer": data_pointer,
                        "value": value,
                        "original_value": value,
                        "encoding": "utf-8",
                        "raw_hex": "",
                        "end": end_value,
                        "end_hex": f"{end_value & 0xFFFFFFFF:08x}",
                    }
                )
                changed += 1

        changed += max(0, len(old_mods) - len(new_mods))
        obj["modifications"] = new_mods
        obj["modification_count"] = len(new_mods)
    return changed


def parse_mapping(values: Sequence[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise W3UError(f"mapping must be OLDID=NEWID: {value!r}")
        old, new = value.split("=", 1)
        old_text = text_to_rawcode(old, allow_zero=False).decode("latin-1")
        new_text = text_to_rawcode(new, allow_zero=False).decode("latin-1")
        result[old_text] = new_text
    return result


def rename_custom_ids(
    document: ObjectData, mapping: Dict[str, str], replace_text: bool
) -> int:
    all_new_ids = {
        obj["new_id"]
        for table in document.tables
        for obj in table["objects"]
        if obj["new_id"]
    }
    changed = 0
    for table in document.tables:
        if table["name"] != "custom":
            continue
        for obj in table["objects"]:
            old_id = obj["new_id"]
            if not old_id or old_id not in mapping:
                continue
            new_id = mapping[old_id]
            if new_id != old_id and new_id in all_new_ids:
                raise W3UError(f"rename collision: {old_id} -> {new_id}")
            obj["new_id"] = new_id
            obj["new_id_hex"] = text_to_rawcode(new_id).hex()
            old_number = rawcode_number(old_id.encode("latin-1"))
            new_number = rawcode_number(new_id.encode("latin-1"))
            for modification in obj["modifications"]:
                if modification["end"] == old_number:
                    modification["end"] = new_number
                if replace_text and isinstance(modification.get("value"), str):
                    replacement = modification["value"].replace(old_id, new_id)
                    if replacement != modification["value"]:
                        modification["value"] = replacement
                        modification["raw_hex"] = ""
            changed += 1
    return changed


def apply_csv(document: ObjectData, path: Path) -> int:
    _headers, rows = read_csv_dicts(path)
    tables = {table["name"]: table for table in document.tables}
    updated = 0
    for row in rows:
        table = tables.get(row.get("table", ""))
        if table is None:
            continue
        try:
            object_index = int(row["object_index"])
        except (KeyError, TypeError, ValueError):
            continue
        if object_index < 0 or object_index >= len(table["objects"]):
            continue
        obj = table["objects"][object_index]

        row_original = row.get("original_id", "")
        row_new = row.get("new_id", "")
        if row_original and row_original != (obj["original_id"] or ""):
            obj["original_id"] = row_original
            obj["original_id_hex"] = text_to_rawcode(row_original, False).hex()
            updated += 1
        if row_new != (obj["new_id"] or ""):
            obj["new_id"] = row_new or None
            obj["new_id_hex"] = text_to_rawcode(row_new, True).hex()
            updated += 1

        try:
            mod_index = int(row.get("mod_index", ""))
        except (TypeError, ValueError):
            continue
        if mod_index < 0 or mod_index >= len(obj["modifications"]):
            continue
        modification = obj["modifications"][mod_index]

        row_field = row.get("field_id", "")
        row_type = row.get("type", "")
        if row_field and row_field != modification["field_id"]:
            modification["field_id"] = row_field
            modification["field_hex"] = text_to_rawcode(row_field, False).hex()
            updated += 1
        if row_type != "" and int(row_type) != modification["type"]:
            modification["type"] = int(row_type)
            modification["type_name"] = TYPE_NAMES.get(modification["type"], "unknown")
            modification["raw_hex"] = ""
            updated += 1

        value_text = row.get("value", "")
        if value_text != "" or modification["type"] in (0, 1, 2, 4, 5):
            if modification["type"] in (0, 4):
                new_value: Any = int(value_text or 0)
            elif modification["type"] == 5:
                new_value = int(value_text) if value_text else modification["value"]
            elif modification["type"] in (1, 2):
                new_value = float(value_text or 0)
            else:
                new_value = value_text
            if new_value != modification["value"]:
                modification["value"] = new_value
                modification["raw_hex"] = ""
                updated += 1

        for key in ("level", "data_pointer", "end"):
            if row.get(key, "") != "":
                new_number = int(row[key])
                if new_number != modification.get(key):
                    modification[key] = new_number
                    updated += 1
    return updated


def parse_sylk(path: Path) -> List[Dict[str, str]]:
    """Read the simple cell-based SYLK subset used by Warcraft SLK files."""
    cells: Dict[Tuple[int, int], str] = {}
    row = 1
    cell_re = re.compile(r"(?:^|;)X(\d+)|(?:^|;)Y(\d+)|(?:^|;)K(.*)$")
    raw = path.read_bytes()
    text = None
    for encoding in ("utf-8-sig", "gb18030", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    for line in text.splitlines():
        x = None
        y = None
        value = None
        for match in cell_re.finditer(line):
            if match.group(1) is not None:
                x = int(match.group(1))
            elif match.group(2) is not None:
                y = int(match.group(2))
            elif match.group(3) is not None:
                value = match.group(3)
        if x is None or value is None:
            continue
        if y is not None:
            row = y
        value = value.strip()
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1].replace('""', '"')
        cells[(row, x)] = value

    if not cells:
        return []
    max_row = max(y for y, _ in cells)
    max_col = max(x for _, x in cells)
    headers = [cells.get((1, x), "").strip() for x in range(1, max_col + 1)]
    result = []
    for y in range(2, max_row + 1):
        row_values = {
            headers[x - 1]: cells[(y, x)]
            for x in range(1, max_col + 1)
            if x <= len(headers) and headers[x - 1] and (y, x) in cells
        }
        if row_values:
            result.append(row_values)
    return result


def parse_slk_table(path: Path) -> Tuple[str, List[Dict[str, str]]]:
    rows = parse_sylk(path)
    if not rows:
        return "", []
    key_column = next(
        (name for name in ("unitBalanceID", "unitID", "unitAbilitiesID") if name in rows[0]),
        "",
    )
    return key_column, rows


def parse_ini_sections(path: Path) -> Dict[str, Dict[str, str]]:
    result: Dict[str, Dict[str, str]] = {}
    current: Optional[Dict[str, str]] = None
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="gb18030", errors="replace")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("//", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = {}
            result[line[1:-1].strip()] = current
        elif current is not None and "=" in line:
            key, value = line.split("=", 1)
            current[key.strip()] = value.strip().strip('"')
    return result


def load_format_metadata(
    data_dir: Path, extension: str
) -> Tuple[List[Dict[str, str]], Dict[str, str]]:
    extension = extension.casefold()
    if extension == ".w3u":
        metadata, _bases, localized = load_unit_metadata(data_dir)
        return metadata, localized

    metadata_file = {
        ".w3t": "UnitMetaData.slk",
        ".w3a": "AbilityMetaData.slk",
        ".w3q": "UpgradeMetaData.slk",
    }.get(extension)
    if metadata_file is None:
        raise W3UError(f"unsupported object-data format: {extension}")
    meta_path = data_dir / metadata_file
    if not meta_path.is_file():
        raise W3UError(f"{metadata_file} not found in data directory: {data_dir}")
    metadata = parse_sylk(meta_path)

    if extension == ".w3t":
        metadata = [
            row
            for row in metadata
            if row.get("ID")
            and row.get("field")
            and row.get("useItem", "").strip().casefold() in ("1", "true", "yes")
        ]
    else:
        source_tables = (
            {"abilitydata", "profile"}
            if extension == ".w3a"
            else {"upgradedata", "profile"}
        )
        metadata = [
            row
            for row in metadata
            if row.get("ID")
            and row.get("field")
            and row.get("slk", "").casefold() in source_tables
        ]

    localized: Dict[str, str] = {}
    ui_strings = data_dir.parent / "ui" / "WorldEditStrings.txt"
    if ui_strings.is_file():
        for section in parse_ini_sections(ui_strings).values():
            localized.update(section)
    return metadata, localized


def load_format_bases(
    data_dir: Path, extension: str
) -> Dict[str, Dict[str, str]]:
    extension = extension.casefold()
    config = {
        ".w3a": ("abilitydata.slk", "alias", "*abilitystrings.txt", "*abilityfunc.txt"),
        ".w3t": ("itemdata.slk", "itemID", "*itemstrings.txt", "*itemfunc.txt"),
        ".w3q": ("upgradedata.slk", "upgradeid", "*upgradestrings.txt", "*upgradefunc.txt"),
    }.get(extension)
    if config is None:
        raise W3UError(f"unsupported object-data format: {extension}")

    slk_name, key_column, text_glob, func_glob = config
    bases: Dict[str, Dict[str, str]] = {}
    slk_path = data_dir / slk_name
    if not slk_path.is_file():
        raise W3UError(f"{slk_name} not found in data directory: {data_dir}")
    for row in parse_sylk(slk_path):
        object_id = row.get(key_column, "")
        if object_id:
            bases[object_id] = dict(row)

    for pattern in (text_glob, func_glob):
        for path in data_dir.glob(pattern):
            for object_id, properties in parse_ini_sections(path).items():
                bases.setdefault(object_id, {}).update(properties)
    return bases


def load_unit_metadata(data_dir: Path) -> Tuple[List[Dict[str, str]], Dict[str, Dict[str, str]], Dict[str, str]]:
    meta_path = data_dir / "UnitMetaData.slk"
    if not meta_path.exists():
        raise W3UError(f"UnitMetaData.slk not found in data directory: {data_dir}")
    metadata = parse_sylk(meta_path)
    if not metadata:
        raise W3UError(f"UnitMetaData.slk has no rows: {meta_path}")
    # UnitMetaData also contains item-only entries. Their field names overlap
    # unit fields (for example item and unit ability lists), so they must not
    # participate in a unit-file merge.
    metadata = [
        row
        for row in metadata
        if row.get("ID")
        and row.get("field")
        and row.get("useUnit", "").strip().casefold() in ("1", "true", "yes")
    ]

    localized: Dict[str, str] = {}
    ui_strings = data_dir.parent / "ui" / "WorldEditStrings.txt"
    if ui_strings.exists():
        for section in parse_ini_sections(ui_strings).values():
            localized.update(section)

    # SLK names in metadata select the base table; the field column is the
    # actual column name in that table, and ID is the four-byte W3U field ID.
    base_tables: Dict[str, Dict[str, Dict[str, str]]] = {}
    for table_path in data_dir.glob("*.slk"):
        table_name = table_path.stem.casefold()
        if table_name == "unitmetadata":
            continue
        key_column, rows = parse_slk_table(table_path)
        if not key_column:
            continue
        base_tables[table_name] = {
            row[key_column]: row
            for row in rows
            if row.get(key_column)
        }

    # Each unit's base values come from the rows sharing its rawcode in the
    # separate UnitData/Balance/Weapons/Abilities tables.
    bases: Dict[str, Dict[str, str]] = {}
    for table in base_tables.values():
        for object_id, row in table.items():
            bases.setdefault(object_id, {}).update(row)

    # Include editor properties even when a particular unit's base table has
    # no explicit cell for them. This matches the editor's inherited/default
    # property view more closely than listing W3U overrides alone.
    fields_by_id = {
        row["ID"]: row
        for row in metadata
        if row.get("ID") and row.get("field")
    }
    field_names: List[str] = []
    for row in metadata:
        field_name = row.get("field", "")
        if field_name and field_name not in field_names:
            field_names.append(field_name)
    for row in metadata:
        table_name = row.get("slk", "").casefold()
        field_name = row.get("field", "")
        if not field_name or table_name not in base_tables:
            continue
        # Ensure metadata-described fields are present, including blank values.
        for values in bases.values():
            values.setdefault(field_name, "")

    # String and function TXT records are editor/game data not represented in
    # the numeric SLK tables. Load the localized unit strings and function data.
    txt_files = list(data_dir.glob("*unitstrings.txt")) + list(data_dir.glob("*unitfunc.txt"))
    for txt_path in txt_files:
        for object_id, properties in parse_ini_sections(txt_path).items():
            bases.setdefault(object_id, {}).update(properties)
            for field_name in properties:
                if field_name not in field_names:
                    field_names.append(field_name)

    return metadata, bases, localized


def metadata_base_value(meta: Dict[str, str], base_values: Dict[str, str]) -> Any:
    value = base_values.get(meta.get("field", ""), "")
    if meta.get("slk", "").casefold() == "profile":
        try:
            index = int(meta.get("index", "-1"))
        except ValueError:
            index = -1
        if index >= 0:
            parts = str(value).split(",")
            return parts[index].strip() if index < len(parts) else ""
    return value


def format_base_value(
    meta: Dict[str, str], base_values: Dict[str, str], level: int
) -> Any:
    repeat = 0
    try:
        repeat = int(meta.get("repeat", "0") or 0)
    except ValueError:
        pass
    field = meta.get("field", "")
    slk = meta.get("slk", "").casefold()

    if slk == "profile":
        value = base_values.get(field, "")
        try:
            index = int(meta.get("index", "-1"))
        except ValueError:
            index = -1
        if index >= 0:
            parts = str(value).split(",")
            return parts[index].strip() if index < len(parts) else ""
        return value

    if slk == "abilitydata" and repeat > 0:
        if field == "Data":
            try:
                pointer = int(meta.get("data", "0") or 0)
            except ValueError:
                pointer = 0
            channel = {0: "A", 1: "B", 2: "C", 3: "D", 4: "F", 5: "G", 6: "H"}.get(
                pointer, "A"
            )
            return base_values.get(f"Data{channel}{level}", "")
        return base_values.get(f"{field}{level}", "")
    return base_values.get(field, "")


def format_level_count(
    obj: Dict[str, Any], metadata: List[Dict[str, str]], base_values: Dict[str, str]
) -> int:
    maximum = 1
    for key in ("levels", "maxlevel"):
        text = str(base_values.get(key, "") or "1").replace("-", "1")
        try:
            maximum = max(maximum, int(float(text)))
        except ValueError:
            pass
    for mod in obj["modifications"]:
        if mod.get("level") is not None:
            maximum = max(maximum, int(mod["level"]))
    return min(maximum, 100)


def format_full_export_rows(
    document: ObjectData,
    metadata: List[Dict[str, str]],
    bases: Dict[str, Dict[str, str]],
    localized: Dict[str, str],
) -> Tuple[List[str], List[Dict[str, Any]]]:
    metadata_by_id: Dict[str, Dict[str, str]] = {}
    for meta in metadata:
        field_id = meta.get("ID", "")
        if field_id and field_id not in metadata_by_id:
            metadata_by_id[field_id] = meta

    for table in document.tables:
        for obj in table["objects"]:
            for mod in obj["modifications"]:
                field_id = mod["field_id"]
                if field_id not in metadata_by_id:
                    metadata_by_id[field_id] = {
                        "ID": field_id,
                        "field": field_id,
                        "displayName": field_id,
                        "repeat": "1" if mod.get("level") else "0",
                        "type": TYPE_NAMES.get(mod["type"], "string"),
                    }

    column_by_id: Dict[str, str] = {}
    for field_id, meta in metadata_by_id.items():
        display_key = meta.get("displayName", "")
        label = localized.get(display_key, display_key or meta.get("field", field_id))
        header_key = field_header_key(field_id)
        column_by_id[field_id] = f"{header_key} [{label}]"

    columns = ["table", "object_index", "object_id", "base_id"]
    if document.uses_optional_ints:
        columns.append("level")
    columns.extend(column_by_id.values())

    rows: List[Dict[str, Any]] = []
    for table in document.tables:
        for obj in table["objects"]:
            object_id = (
                obj["new_id"] if table["name"] == "custom" else obj["original_id"]
            ) or obj["original_id"]
            base_values = bases.get(obj["original_id"], {})
            levels = (
                range(1, format_level_count(obj, metadata, base_values) + 1)
                if document.uses_optional_ints
                else (None,)
            )
            mods_by_key: Dict[Tuple[str, int, int], Dict[str, Any]] = {}
            for mod in obj["modifications"]:
                mods_by_key[
                    (
                        mod["field_id"],
                        int(mod.get("level") or 0),
                        int(mod.get("data_pointer") or 0),
                    )
                ] = mod

            for level in levels:
                row: Dict[str, Any] = {
                    "table": table["name"],
                    "object_index": obj["index"],
                    "object_id": object_id,
                    "base_id": obj["original_id"],
                }
                if level is not None:
                    row["level"] = level
                for field_id, meta in metadata_by_id.items():
                    repeat = _meta_int(meta, "repeat", 0)
                    mod_level = level if repeat > 0 and level is not None else 0
                    pointer = int(meta.get("data", "0") or 0)
                    mod = mods_by_key.get((field_id, mod_level, pointer))
                    if mod is None and repeat == 0 and document.uses_optional_ints:
                        mod = mods_by_key.get((field_id, 0, pointer))
                    if mod is not None:
                        value = mod["value"]
                    elif not document.uses_optional_ints or level == 1 or repeat > 0:
                        value = format_base_value(
                            meta, base_values, level if level is not None else 1
                        )
                    else:
                        value = ""
                    row[column_by_id[field_id]] = value
                rows.append(row)
    return columns, rows


def full_export_rows(
    document: ObjectData,
    metadata: List[Dict[str, str]],
    bases: Dict[str, Dict[str, str]],
    localized: Dict[str, str],
) -> Tuple[List[str], List[Dict[str, Any]]]:
    metadata_by_id = {
        row.get("ID"): row for row in metadata if row.get("ID")
    }
    column_by_id: Dict[str, str] = {}
    for meta in metadata:
        field_id = meta["ID"]
        display_key = meta.get("displayName", "")
        label = localized.get(display_key, display_key or meta.get("field", field_id))
        header_key = field_header_key(field_id)
        column_by_id[field_id] = f"{header_key} [{label}]"

    rows: List[Dict[str, Any]] = []
    for table in document.tables:
        for obj in table["objects"]:
            object_id = obj["new_id"] if table["name"] == "custom" else obj["original_id"]
            object_id = object_id or obj["original_id"]
            base_values = bases.get(obj["original_id"], {})
            if not base_values:
                base_values = next(
                    (
                        row
                        for base_id, row in bases.items()
                        if base_id.casefold() == (obj["original_id"] or "").casefold()
                    ),
                    {},
                )
            row: Dict[str, Any] = {
                "table": table["name"],
                "object_id": object_id,
                "base_id": obj["original_id"],
                "modification_count": obj["modification_count"],
            }
            for field_id, meta in metadata_by_id.items():
                row[column_by_id[field_id]] = metadata_base_value(meta, base_values)
            for mod in obj["modifications"]:
                column = column_by_id.get(mod["field_id"])
                if column is not None:
                    row[column] = mod["value"]
            rows.append(row)

    columns = ["table", "object_id", "base_id", "modification_count"]
    columns.extend(column_by_id.values())
    return columns, rows


def command_full_export(args: argparse.Namespace) -> int:
    path = Path(args.input)
    if path.suffix.casefold() != ".w3u":
        raise W3UError("full-export is the inherited-property export for .w3u files")
    data_dir = Path(args.data)
    document = parse_object_data(path.read_bytes(), path.suffix)
    metadata, bases, localized = load_unit_metadata(data_dir)
    columns, rows = full_export_rows(document, metadata, bases, localized)
    output = Path(args.output)
    write_table(output, columns, rows, metadata, localized)
    print(f"full editor view: {output}")
    print(f"objects: {len(rows)}; property columns: {len(columns) - 4}")
    print(f"base rows loaded: {len(bases)}; metadata fields: {len(metadata)}")
    return 0


def command_export_mods(args: argparse.Namespace) -> int:
    source = Path(args.input)
    extension = source.suffix.casefold()
    if extension not in (".w3a", ".w3t", ".w3q"):
        raise W3UError("mod-export supports .w3a, .w3t and .w3q files")
    document = parse_object_data(source.read_bytes(), extension)
    metadata, localized = load_format_metadata(Path(args.data), extension)
    bases = load_format_bases(Path(args.data), extension)
    columns, rows = format_full_export_rows(document, metadata, bases, localized)
    output = Path(args.output)
    write_table(output, columns, rows, metadata, localized)
    print(f"field CSV: {output}")
    identifier_columns = 5 if document.uses_optional_ints else 4
    print(
        f"objects: {sum(len(t['objects']) for t in document.tables)}; "
        f"rows: {len(rows)}; property columns: {len(columns) - identifier_columns}"
    )
    print("Format-level rows keep per-level skill/upgrade defaults and overrides.")
    return 0


def command_apply_mods_csv(args: argparse.Namespace) -> int:
    source = Path(args.input)
    extension = source.suffix.casefold()
    if extension not in (".w3a", ".w3t", ".w3q"):
        raise W3UError("apply-mod-csv supports .w3a, .w3t and .w3q files")
    document = parse_object_data(source.read_bytes(), extension)
    metadata, _localized = load_format_metadata(Path(args.data), extension)
    bases = load_format_bases(Path(args.data), extension)
    changed = apply_format_full_csv(document, Path(args.csv), metadata, bases)
    rebuilt = build_object_data(document_to_json(document))
    parse_object_data(rebuilt, extension)
    output = Path(args.output)
    if output.suffix.casefold() != extension:
        raise W3UError(f"output extension must match input format: {extension}")
    if output.resolve() == source.resolve():
        raise W3UError("output path cannot be the original object-data file")
    if output.exists() and not args.force:
        raise W3UError(f"output already exists: {output}; use --force")
    output.write_bytes(rebuilt)
    print(f"applied {changed} modification change(s): {output}")
    print("source file was not overwritten")
    return 0


def metadata_value_type(metadata_type: str, existing_type: Optional[int]) -> int:
    if existing_type in (0, 1, 2, 3):
        return existing_type
    normalized = metadata_type.strip().casefold()
    if normalized in ("int", "integer", "bool", "boolean"):
        return 0
    if normalized == "real":
        return 1
    if normalized == "unreal":
        return 2
    return 3


def normalize_editor_value(value: Any, type_id: int) -> Any:
    text = "" if value is None else str(value).strip()
    if type_id == 0:
        if not text:
            return 0
        try:
            return int(float(text))
        except ValueError:
            return text
    if type_id in (1, 2):
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError:
            return text
    return text


def apply_full_csv(
    document: ObjectData,
    csv_path: Path,
    metadata: List[Dict[str, str]],
    bases: Dict[str, Dict[str, str]],
) -> int:
    headers, csv_rows = read_csv_dicts(csv_path)

    columns_by_id: Dict[str, str] = {}
    for header in headers:
        raw_header = _header_parts(header)[0]
        if raw_header:
            columns_by_id.setdefault(raw_header, header)
    metadata_by_id = {
        meta["ID"]: meta
        for meta in metadata
        if meta.get("ID") in columns_by_id
    }

    objects: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for table in document.tables:
        for obj in table["objects"]:
            object_id = (
                obj["new_id"] if table["name"] == "custom" else obj["original_id"]
            ) or obj["original_id"]
            objects[(table["name"], object_id)] = obj

    updated = 0
    seen = set()
    for row in csv_rows:
        table_name = row.get("table", "")
        object_id = row.get("object_id", "")
        key = (table_name, object_id)
        obj = objects.get(key)
        if obj is None:
            raise W3UError(f"CSV object not found in source W3U: {table_name}/{object_id}")
        if key in seen:
            raise W3UError(f"duplicate object row in CSV: {table_name}/{object_id}")
        seen.add(key)

        base_id = row.get("base_id") or obj["original_id"]
        base_values = bases.get(base_id)
        if base_values is None:
            base_values = next(
                (
                    values
                    for candidate_id, values in bases.items()
                    if candidate_id.casefold() == base_id.casefold()
                ),
                {},
            )
        mods_by_field = {mod["field_id"]: mod for mod in obj["modifications"]}

        for field_id, meta in metadata_by_id.items():
            existing = mods_by_field.get(field_id)
            value_type = metadata_value_type(
                meta.get("type", ""),
                existing.get("type") if existing else None,
            )
            desired = normalize_editor_value(
                row.get(columns_by_id[field_id], ""), value_type
            )
            default = normalize_editor_value(
                metadata_base_value(meta, base_values), value_type
            )
            current = (
                normalize_editor_value(existing["value"], existing["type"])
                if existing is not None
                else default
            )

            if desired == current:
                continue
            if desired == default:
                if existing is not None:
                    obj["modifications"].remove(existing)
                    mods_by_field.pop(field_id, None)
                    updated += 1
                continue

            if value_type in (0, 1, 2) and not isinstance(desired, (int, float)):
                raise W3UError(
                    f"non-numeric value {desired!r} in numeric field "
                    f"{field_id!r} for object {object_id!r}"
                )

            if existing is None:
                terminator_id = obj["new_id"] or obj["original_id"]
                terminator = int.from_bytes(
                    text_to_rawcode(terminator_id, allow_zero=False),
                    "little",
                    signed=True,
                )
                existing = {
                    "field_id": field_id,
                    "field_hex": text_to_rawcode(field_id, allow_zero=False).hex(),
                    "type": value_type,
                    "type_name": TYPE_NAMES.get(value_type, "unknown"),
                    "level": None,
                    "data_pointer": None,
                    "value": desired,
                    "original_value": desired,
                    "encoding": "utf-8",
                    "raw_hex": "",
                    "end": terminator,
                    "end_hex": f"{terminator & 0xFFFFFFFF:08x}",
                }
                obj["modifications"].append(existing)
                mods_by_field[field_id] = existing
                updated += 1
            elif normalize_editor_value(existing["value"], existing["type"]) != desired:
                existing["value"] = desired
                existing["type"] = value_type
                existing["type_name"] = TYPE_NAMES.get(value_type, "unknown")
                existing["original_value"] = desired
                existing["raw_hex"] = ""
                updated += 1

        obj["modification_count"] = len(obj["modifications"])
    return updated


def _meta_int(meta: Dict[str, str], key: str, default: int = 0) -> int:
    try:
        return int(meta.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def apply_format_full_csv(
    document: ObjectData,
    csv_path: Path,
    metadata: List[Dict[str, str]],
    bases: Dict[str, Dict[str, str]],
) -> int:
    headers, csv_rows = read_csv_dicts(csv_path)
    columns_by_id: Dict[str, str] = {}
    for header in headers:
        raw_header = _header_parts(header)[0]
        if raw_header:
            columns_by_id.setdefault(raw_header, header)

    metadata_by_id = {
        meta["ID"]: meta
        for meta in metadata
        if meta.get("ID") and meta["ID"] in columns_by_id
    }
    objects: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for table in document.tables:
        for obj in table["objects"]:
            objects[(table["name"], int(obj["index"]))] = obj

    grouped: Dict[Tuple[str, int], Dict[int, Dict[str, str]]] = {}
    for row in csv_rows:
        try:
            key = (row.get("table", ""), int(row.get("object_index", "")))
        except (TypeError, ValueError) as exc:
            raise W3UError("CSV object_index is missing or invalid") from exc
        obj = objects.get(key)
        if obj is None:
            raise W3UError(f"CSV object not found in source file: {key[0]}[{key[1]}]")
        expected_id = (
            obj["new_id"] if key[0] == "custom" else obj["original_id"]
        ) or obj["original_id"]
        if row.get("object_id", "") != expected_id:
            raise W3UError(
                f"object ID changed in CSV at {key[0]}[{key[1]}]; "
                "keep table, object_index and object_id unchanged"
            )
        if document.uses_optional_ints:
            try:
                level = int(row.get("level", "") or 1)
            except ValueError as exc:
                raise W3UError(
                    f"invalid level for {key[0]}[{key[1]}]: "
                    f"{row.get('level', '')!r}"
                ) from exc
        else:
            level = 1
        grouped.setdefault(key, {})[level] = row

    missing = set(objects) - set(grouped)
    if missing:
        table_name, index = sorted(missing)[0]
        raise W3UError(
            f"CSV is missing object {table_name}[{index}]; keep all object rows"
        )

    changed = 0
    for key, obj in objects.items():
        rows_by_level = grouped[key]
        base_values = bases.get(obj["original_id"], {})
        modifications = obj["modifications"]
        mods_by_key: Dict[Tuple[str, int, int], Dict[str, Any]] = {}
        for mod in modifications:
            mods_by_key[
                (
                    mod["field_id"],
                    int(mod.get("level") or 0),
                    int(mod.get("data_pointer") or 0),
                )
            ] = mod

        def apply_value(
            field_id: str,
            meta: Dict[str, str],
            level: int,
            row: Dict[str, str],
        ) -> None:
            nonlocal changed
            pointer = _meta_int(meta, "data", 0) if document.uses_optional_ints else 0
            mod_level = level if document.uses_optional_ints else 0
            key3 = (field_id, mod_level, pointer)
            existing = mods_by_key.get(key3)
            value_type = metadata_value_type(
                meta.get("type", ""), existing.get("type") if existing else None
            )
            desired = normalize_editor_value(
                row.get(columns_by_id[field_id], ""), value_type
            )
            default = normalize_editor_value(
                format_base_value(meta, base_values, level), value_type
            )
            current = (
                normalize_editor_value(existing["value"], existing["type"])
                if existing is not None
                else default
            )
            if desired == current:
                return
            if desired == default:
                if existing is not None:
                    modifications.remove(existing)
                    mods_by_key.pop(key3, None)
                    changed += 1
                return
            if value_type in (0, 1, 2) and not isinstance(desired, (int, float)):
                raise W3UError(
                    f"non-numeric value {desired!r} in field {field_id!r} "
                    f"for object {key[1]} level {level}"
                )
            if existing is None:
                object_id = obj["new_id"] or obj["original_id"]
                end_value = int.from_bytes(
                    text_to_rawcode(object_id, allow_zero=False),
                    "little",
                    signed=True,
                )
                existing = {
                    "field_id": field_id,
                    "field_hex": text_to_rawcode(field_id, allow_zero=False).hex(),
                    "type": value_type,
                    "type_name": TYPE_NAMES.get(value_type, "unknown"),
                    "level": mod_level if document.uses_optional_ints else None,
                    "data_pointer": pointer if document.uses_optional_ints else None,
                    "value": desired,
                    "original_value": desired,
                    "encoding": "utf-8",
                    "raw_hex": "",
                    "end": end_value,
                    "end_hex": f"{end_value & 0xFFFFFFFF:08x}",
                }
                modifications.append(existing)
                mods_by_key[key3] = existing
            else:
                existing["value"] = desired
                existing["type"] = value_type
                existing["type_name"] = TYPE_NAMES.get(value_type, "unknown")
                existing["original_value"] = desired
                existing["raw_hex"] = ""
            changed += 1

        for field_id, meta in metadata_by_id.items():
            repeat = _meta_int(meta, "repeat", 0)
            if document.uses_optional_ints and repeat > 0:
                for level, row in rows_by_level.items():
                    if level > 0:
                        apply_value(field_id, meta, level, row)
            else:
                row = rows_by_level.get(1)
                if row is not None:
                    apply_value(field_id, meta, 0, row)

        obj["modification_count"] = len(modifications)
    return changed


def command_apply_full_csv(args: argparse.Namespace) -> int:
    source = Path(args.input)
    document = parse_object_data(source.read_bytes(), source.suffix)
    metadata, bases, _localized = load_unit_metadata(Path(args.data))
    changed = apply_full_csv(document, Path(args.csv), metadata, bases)
    rebuilt = build_object_data(document_to_json(document))
    parse_object_data(rebuilt, source.suffix)

    output = Path(args.output)
    if output.exists() and not args.force:
        raise W3UError(f"output already exists: {output}; use --force")
    output.write_bytes(rebuilt)
    print(f"applied {changed} field change(s): {output}")
    print("source W3U was not overwritten")
    return 0


def command_inspect(args: argparse.Namespace) -> int:
    path = Path(args.input)
    document = parse_object_data(path.read_bytes(), path.suffix)
    print(f"file: {path}")
    print(f"version: {document.version}")
    print(f"optional level/data: {document.uses_optional_ints}")
    for table in document.tables:
        print(f"{table['name']} objects: {table['count']}")
        for obj in table["objects"]:
            old = obj["original_id"] or "<zero>"
            new = obj["new_id"] or "<zero>"
            print(
                f"  [{obj['index']:04d}] {old} -> {new} "
                f"({obj['modification_count']} mods)"
            )
    print(f"trailing bytes: {len(bytes.fromhex(document.trailing_hex))}")
    return 0


def command_export(args: argparse.Namespace) -> int:
    path = Path(args.input)
    document = parse_object_data(path.read_bytes(), path.suffix)
    json_path = Path(args.json) if args.json else path.with_suffix(path.suffix + ".json")
    csv_path = Path(args.csv) if args.csv else path.with_suffix(path.suffix + ".csv")
    save_json(document, json_path)
    save_csv(document, csv_path)
    print(f"JSON: {json_path}")
    print(f"CSV : {csv_path}")
    return 0


def command_build(args: argparse.Namespace) -> int:
    document = json.loads(Path(args.input).read_text(encoding="utf-8"))
    data = build_object_data(document)
    output = Path(args.output)
    if output.exists() and not args.force:
        raise W3UError(f"output already exists: {output}; use --force")
    output.write_bytes(data)
    print(f"wrote: {output} ({len(data)} bytes)")
    return 0


def command_rename(args: argparse.Namespace) -> int:
    path = Path(args.input)
    document = parse_object_data(path.read_bytes(), path.suffix)
    mapping = parse_mapping(args.mapping)
    count = rename_custom_ids(document, mapping, args.replace_text)
    data = build_object_data(document_to_json(document))
    output = Path(args.output)
    if output.exists() and not args.force:
        raise W3UError(f"output already exists: {output}; use --force")
    output.write_bytes(data)
    print(f"renamed {count} object(s): {output}")
    return 0


def command_apply_csv(args: argparse.Namespace) -> int:
    path = Path(args.input)
    document = parse_object_data(path.read_bytes(), path.suffix)
    count = apply_csv(document, Path(args.csv))
    data = build_object_data(document_to_json(document))
    output = Path(args.output)
    if output.exists() and not args.force:
        raise W3UError(f"output already exists: {output}; use --force")
    output.write_bytes(data)
    print(f"applied {count} CSV change(s): {output}")
    return 0


def command_verify(args: argparse.Namespace) -> int:
    path = Path(args.input)
    original = path.read_bytes()
    document = parse_object_data(original, path.suffix)
    rebuilt = build_object_data(document_to_json(document))
    if rebuilt != original:
        raise W3UError(
            f"round-trip mismatch: original={len(original)}, rebuilt={len(rebuilt)}"
        )
    print(f"round-trip OK: {path} ({len(original)} bytes)")
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect/export/edit/rebuild Warcraft III object data."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="show object counts and IDs")
    inspect_parser.add_argument("input")
    inspect_parser.set_defaults(func=command_inspect)

    export_parser = subparsers.add_parser(
        "export", help="export JSON and CSV to edit"
    )
    export_parser.add_argument("input")
    export_parser.add_argument("--json", default="")
    export_parser.add_argument("--csv", default="")
    export_parser.set_defaults(func=command_export)

    build_parser = subparsers.add_parser("build", help="build a w3u from JSON")
    build_parser.add_argument("input")
    build_parser.add_argument("output")
    build_parser.add_argument("--force", action="store_true")
    build_parser.set_defaults(func=command_build)

    rename_parser = subparsers.add_parser(
        "rename", help="quickly rename custom object IDs"
    )
    rename_parser.add_argument("input")
    rename_parser.add_argument("output")
    rename_parser.add_argument("mapping", nargs="+", help="for example h000=H001")
    rename_parser.add_argument(
        "--replace-text",
        action="store_true",
        help="also replace old IDs inside string/list values",
    )
    rename_parser.add_argument("--force", action="store_true")
    rename_parser.set_defaults(func=command_rename)

    csv_parser = subparsers.add_parser(
        "apply-csv", help="apply edits made in the exported CSV"
    )
    csv_parser.add_argument("input")
    csv_parser.add_argument("csv")
    csv_parser.add_argument("output")
    csv_parser.add_argument("--force", action="store_true")
    csv_parser.set_defaults(func=command_apply_csv)

    full_parser = subparsers.add_parser(
        "full-export",
        help="export inherited base values plus W3U overrides as a wide CSV",
    )
    full_parser.add_argument("input")
    full_parser.add_argument("output")
    full_parser.add_argument(
        "--data",
        required=True,
        help="folder containing UnitMetaData.slk and matching unit SLK/TXT data",
    )
    full_parser.set_defaults(func=command_full_export)

    mods_parser = subparsers.add_parser(
        "mod-export",
        help="export complete format-level properties for .w3a, .w3t or .w3q",
    )
    mods_parser.add_argument("input")
    mods_parser.add_argument("output")
    mods_parser.add_argument(
        "--data",
        required=True,
        help="folder containing the matching object metadata SLK",
    )
    mods_parser.set_defaults(func=command_export_mods)

    apply_full_parser = subparsers.add_parser(
        "apply-full-csv",
        help="rebuild W3U after editing the full editor-view CSV",
    )
    apply_full_parser.add_argument("input", help="original W3U used to export the CSV")
    apply_full_parser.add_argument("csv", help="edited full-export CSV")
    apply_full_parser.add_argument("output", help="new W3U output path")
    apply_full_parser.add_argument(
        "--data",
        required=True,
        help="folder containing UnitMetaData.slk and matching unit SLK/TXT data",
    )
    apply_full_parser.add_argument("--force", action="store_true")
    apply_full_parser.set_defaults(func=command_apply_full_csv)

    apply_mods_parser = subparsers.add_parser(
        "apply-mod-csv",
        help="rebuild .w3a/.w3t/.w3q after editing their complete CSV",
    )
    apply_mods_parser.add_argument("input")
    apply_mods_parser.add_argument("csv")
    apply_mods_parser.add_argument("output")
    apply_mods_parser.add_argument(
        "--data",
        required=True,
        help="folder containing the matching object metadata SLK",
    )
    apply_mods_parser.add_argument("--force", action="store_true")
    apply_mods_parser.set_defaults(func=command_apply_mods_csv)

    verify_parser = subparsers.add_parser(
        "verify", help="verify byte-for-byte parse/rebuild round trip"
    )
    verify_parser.add_argument("input")
    verify_parser.set_defaults(func=command_verify)
    return parser


def main() -> int:
    parser = make_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except (W3UError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
