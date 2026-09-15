# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Explicit offline Skill checks or static navigation; stdout only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .skill_inventory import (
    ProtocolError,
    assess_skill_route,
    load_policy,
    read_response,
    render_stable_navigation,
    select_route_dependencies,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path)
    source.add_argument("--navigation", action="store_true")
    parser.add_argument("--cwd")
    parser.add_argument("--policy", type=Path,
                        default=Path(__file__).with_name("policy.toml"))
    parser.add_argument("--target", action="append", default=[])
    parser.add_argument("--require", action="append", default=[])
    parser.add_argument("--expect-locator", action="append", default=[])
    args = parser.parse_args(argv)
    if args.navigation and (args.cwd or args.target or args.require or args.expect_locator):
        parser.error("--navigation cannot be combined with route arguments")
    if not args.navigation and args.cwd is None:
        parser.error("--cwd is required for a route check")
    try:
        try:
            policy = load_policy(args.policy)
        except UnicodeError as error:
            raise ProtocolError("policy must be UTF-8") from error
        if args.navigation:
            print(render_stable_navigation(policy), end="")
            return 0
        selection = select_route_dependencies(
            policy, target_ids=args.target, required_ids=args.require,
        )
        pins = {}
        for value in args.expect_locator:
            name, separator, path = value.partition("=")
            if not separator or not name or name in pins:
                raise ProtocolError("locator pin must be a unique ID=PATH")
            pins[name] = path
        try:
            response = read_response(args.input)
        except UnicodeError as error:
            raise ProtocolError("skills/list response must be UTF-8") from error
        result = assess_skill_route(
            response, expected_cwd=args.cwd,
            target_ids=selection["target_ids"], required_ids=selection["required_ids"],
            expected_locators=pins,
        )
        result["selection_basis"] = selection["selection_basis"]
        result["requirement_completeness"] = selection["requirement_completeness"]
    except ProtocolError as error:
        result = {
            "schema_version": 1, "kind": "selected-skill-route-error",
            "status": "UNKNOWN", "reason": str(error),
            "instructions_loaded": False, "write_authorized": False,
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["status"] == "VERIFIED":
        return 0
    return 4 if result["status"] == "UNKNOWN" else 3


if __name__ == "__main__":
    raise SystemExit(main())
