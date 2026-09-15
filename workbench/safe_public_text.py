#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Validate plain text before it enters a public progress artifact."""

from __future__ import annotations

import re
import unicodedata


MAX_TEXT_LENGTH = 4096

_URL = re.compile(r"[a-z][a-z0-9+.-]*://")
_SCP_REMOTE = re.compile(
    r"(?<![a-z0-9._%+-])[a-z0-9._%+-]+@[a-z0-9.-]+:[^\s,;]+"
)
_CREDENTIAL = re.compile(
    r"(?:\bbearer|\bbasic)\s+\S+"
    r"|\bcookie\s*:\s*\S+"
    r"|-----begin [a-z0-9 ]*private key-----"
)
_FORBIDDEN_PUBLIC_MARKERS = ("authorization",)
_SAFE_PUBLIC_MESSAGES = {
    "AUTHORIZATION_VALUE_HIDDEN": "认证请求头的值不得进入公开进度。",
}
_RAW_GIT = re.compile(
    r"(?:^|[^\w])"
    r"(?:diff[ \t]+--git[ \t]+|@@(?:[ \t]|$)|---[ \t]+a/|\+\+\+[ \t]+b/|\?\?[ \t]+)"
)
_MARKDOWN_ESCAPES = frozenset(r"\*_{}[]()#+-.!>|<")


class SafePublicTextError(ValueError):
    reason: str

    def __init__(self, reason: str = "UNSAFE_PUBLIC_TEXT"):
        super().__init__(reason)
        self.reason = reason


def _fail() -> None:
    raise SafePublicTextError()


def _skip_horizontal_space(scan_projection: str, index: int) -> int:
    while (
        index < len(scan_projection)
        and scan_projection[index] in " \t"
    ):
        index += 1
    return index


def _markdown_payload_start(scan_projection_line: str) -> int:
    """Return the first payload character after Markdown container tokens."""

    index = _skip_horizontal_space(scan_projection_line, 0)
    while index < len(scan_projection_line):
        if scan_projection_line[index] == ">":
            index = _skip_horizontal_space(scan_projection_line, index + 1)
            continue
        if scan_projection_line[index] in "-*+":
            following = index + 1
            if (
                following < len(scan_projection_line)
                and scan_projection_line[following] in " \t"
            ):
                index = _skip_horizontal_space(
                    scan_projection_line, following
                )
                continue
        if scan_projection_line[index].isdigit():
            following = index
            while (
                following < len(scan_projection_line)
                and scan_projection_line[following].isdigit()
            ):
                following += 1
            if (
                following < len(scan_projection_line)
                and scan_projection_line[following] in ".)"
                and following + 1 < len(scan_projection_line)
                and scan_projection_line[following + 1] in " \t"
            ):
                index = _skip_horizontal_space(
                    scan_projection_line, following + 1
                )
                continue
        break
    return index


def _is_task_list_payload(scan_projection_line: str) -> bool:
    payload = scan_projection_line[
        _markdown_payload_start(scan_projection_line) :
    ]
    return (
        len(payload) >= 3
        and payload[0] == "["
        and payload[1] in " xX"
        and payload[2] == "]"
        and (len(payload) == 3 or payload[3] in " \t")
    )


def _is_ascii_identifier_start(character: str) -> bool:
    return character == "_" or "A" <= character <= "Z" or "a" <= character <= "z"


def _is_ascii_identifier_part(character: str) -> bool:
    return _is_ascii_identifier_start(character) or "0" <= character <= "9"


def _contains_assignment(scan_projection: str) -> bool:
    index = 0
    while index < len(scan_projection):
        if not _is_ascii_identifier_start(scan_projection[index]):
            index += 1
            continue
        if index and _is_ascii_identifier_part(scan_projection[index - 1]):
            index += 1
            continue
        end = index + 1
        while end < len(scan_projection) and _is_ascii_identifier_part(
            scan_projection[end]
        ):
            end += 1
        equals = _skip_horizontal_space(scan_projection, end)
        if (
            equals >= len(scan_projection)
            or scan_projection[equals] != "="
        ):
            index = end
            continue
        if (
            equals + 1 < len(scan_projection)
            and scan_projection[equals + 1] == "="
        ):
            index = equals + 2
            continue
        payload = _skip_horizontal_space(scan_projection, equals + 1)
        if (
            payload < len(scan_projection)
            and scan_projection[payload] not in "=\r\n"
        ):
            return True
        index = equals + 1
    return False


def _safe_scan_projection(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Cf"
    )


def _contains_forbidden_public_marker(scan_projection: str) -> bool:
    return any(
        marker in scan_projection for marker in _FORBIDDEN_PUBLIC_MARKERS
    )


def validate_safe_public_text(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT_LENGTH:
        _fail()
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        _fail()
    for character in value:
        if unicodedata.category(character) == "Cc" and character not in "\t\n\r":
            _fail()
    scan_projection = _safe_scan_projection(value)
    if "`" in scan_projection:
        _fail()
    if (
        _URL.search(scan_projection)
        or _SCP_REMOTE.search(scan_projection)
        or _CREDENTIAL.search(scan_projection)
        or _contains_forbidden_public_marker(scan_projection)
    ):
        _fail()
    if _contains_assignment(scan_projection):
        _fail()
    for line in scan_projection.splitlines() or [scan_projection]:
        if _is_task_list_payload(line) or _RAW_GIT.search(line):
            _fail()
    return value


def render_safe_public_message(code: object) -> str:
    if not isinstance(code, str) or code not in _SAFE_PUBLIC_MESSAGES:
        _fail()
    return validate_safe_public_text(_SAFE_PUBLIC_MESSAGES[code])


def escape_markdown_text(value: str) -> str:
    checked = validate_safe_public_text(value)
    return "".join(
        f"\\{character}" if character in _MARKDOWN_ESCAPES else character
        for character in checked
    )
