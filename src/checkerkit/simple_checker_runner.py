"""
You can run this script as:
    python simple_checker_runner.py <HOST> [TICK_LEN=60] [START_TICK=0] [TEAM_ID=0] [--debug] [--http]"

    HOST        - Target HOST passed to the checkers
    TICK_LEN    - Lenght of tick in seconds (60 by default)
    START_TICK  - Initial tick from which to start (0 by default)
    TEAM_ID     - "Dummy" team ID which should runner use (0 by default)
    --debug     - Sets DEBUG logging level
    --http      - Runs a HTTP server with attack info

Checkers are dynamically discovered from files in current directory:
- Script list files in current directory matching pattern "checker*.py"
- It tries to import them
- Looks for classes defined in them
- Takes all classes subclassing `ctf_gameserver.checkerlib.lib.BaseChecker`
"""

import base64
import importlib.util
import inspect
import json
import logging
import os.path
import pickle
import sys
import threading
from datetime import datetime, timedelta
from time import sleep
from typing import Any, TypeAlias, TypedDict

import ctf_gameserver.checkerlib.lib
import rich
from ctf_gameserver.checkerlib.lib import (
    BaseChecker,
    CheckResult,
    _run_check_steps,
    get_flag,
)

# TODO: I'm pretty sure this fails for original Faust fork
ctf_gameserver.checkerlib.lib._LOCAL_STATE_PATH_TMPL = "_{team:d}_state.json"

# TODO: Make this configurable from command line arguments.
# Configuration is hardcoded because that is how I used it before I decided to create
# `checkerkit`. Current state is very rough as I just wanted to package it for now.

#####
# CONFIGURATION
#####

# Max count of flag IDs in attack json
MAX_FLAGID_COUNT = 5
# Attack json location
ATTACK_JSON = "./attack.json"
# Custom JSON containing service state info
# Structure:
# {
#     "<SERVICE>": {
#         "<TEAM_ID>": {
#             "<TICK>": {"result": "<OK/DOWN/FAULTY/...>", "msg": "<CHECKER_MESSAGE>"}
#             # ...
#         }
#         # ...
#     }
#     # ...
# }
SERVICE_STATE_JSON = "./service_state.json"
# Services/Flagstores with theirs checkers
CHECKERS: dict[str, type[BaseChecker]] = {}

# Directory where state files will be stored; You may want to change this to /tmp
STATE_DIR = "."

#####
# ACTUAL CODE
#####


def discover_checkers() -> dict[str, type[BaseChecker]]:
    # Stuff below is not weird enough so we need to do even more cursed stuf...
    # See: https://stackoverflow.com/questions/41861427/python-3-5-how-to-dynamically-import-a-module-given-the-full-file-path-in-the
    # TODO: Look into this bit more. I already dealt with this in past
    # and somehow managed to get it working but currently it is too late
    # in night for me to trying to dig into Python internals once more.
    sys.path = [os.path.abspath("."), *sys.path]

    # This is list to keep the order.
    # Although I guess `.listdir()` doesn't need to be deterministic so TODO.
    checker_classes: list[type[BaseChecker]] = []

    for file in os.listdir():
        if not file.startswith("checker") or not file.endswith(".py"):
            continue

        module_name = file.removesuffix(".py")
        spec = importlib.util.spec_from_file_location(module_name, file)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        for x in module.__dict__.values():
            # We care only about BaseChecker subclasses.
            if not isinstance(x, type) or not issubclass(x, BaseChecker):
                continue

            # We care only about non-abstract classes...
            if inspect.isabstract(x):
                continue

            # ... but `BaseChecker` is not `ABC` so we need to check
            # if it ~~implements~~ overrides all needed methods.
            if (
                x.check_flag is BaseChecker.check_flag
                or x.check_service is BaseChecker.check_service
                or x.place_flag is BaseChecker.place_flag
            ):
                continue

            # We don't want duplicates (which could exists due
            # to reusing one checker class for another checker).
            if x in checker_classes:
                continue

            checker_classes.append(x)

    # If checker class names are unique per checker, then use them
    if len(checker_classes) == len({x.__name__ for x in checker_classes}):
        return {x.__name__: x for x in checker_classes}

    # If checker module names are unique per checker, then use them
    if len(checker_classes) == len({x.__module__ for x in checker_classes}):
        return {x.__module__: x for x in checker_classes}

    # If none above, then we simply create "checker-0", "checker-1", ...
    rich.print("WARNING: Couldn't determine names for checkers, fallback to counter")
    return {f"checker-{i}": x for i, x in enumerate(checker_classes)}


class AttackData(TypedDict):
    teams: list[int]
    flag_ids: dict[str, dict[str, list[Any]]]


class ServiceStateEntry(TypedDict):
    result: str
    msg: str


# {"SERVICE": {"TEAM": {"TICK": ServiceStateEntry}}}
ServiceStateData: TypeAlias = dict[str, dict[str, dict[str, ServiceStateEntry]]]


def get_state_path(team: int, flagstore: str) -> str:
    return f"{STATE_DIR}/_{team}_{flagstore}_state.json"


def get_flagids_from_state(
    state_path: str, start_tick: int, end_tick: int
) -> list[str]:
    try:
        with open(state_path, "r") as f:
            data: dict[str, str] = json.load(f)
    except FileNotFoundError:
        return []

    flagids: list[str] = []
    for tick in range(start_tick, end_tick + 1):
        state_key = f"__flagid_{tick}"
        if state_key in data:
            flagid = pickle.loads(base64.b64decode(data[state_key]))
            assert isinstance(flagid, str)
            flagids.append(flagid)

    return flagids


def checkresult2rich(res: CheckResult) -> str:
    match res:
        case CheckResult.OK:
            c = "green"
        case CheckResult.DOWN:
            c = "red"
        case CheckResult.FAULTY:
            c = "red"
        case CheckResult.FLAG_NOT_FOUND:
            c = "yellow"
        case CheckResult.RECOVERING:
            c = "yellow"
    return f"[{c}]{res}[/{c}]"


def run_checker(
    checker: type[BaseChecker],
    flagstore: str,
    host: str,
    team: int,
    tick: int,
) -> tuple[CheckResult, str]:
    # Setup checkerlib
    # I would say that this is so bad API but I can't because
    # there is no API...
    state_path = get_state_path(team, flagstore)
    ctf_gameserver.checkerlib.lib._LOCAL_STATE_PATH = state_path
    ctf_gameserver.checkerlib.lib.tick = tick
    get_flag._team = team

    # Run checker
    result, phase, message = _run_check_steps(checker(host, team), tick)
    assert isinstance(result, CheckResult)
    return result, f"{phase}{message}"


def do_tick(host: str, team: int, tick: int):
    # Load data
    attack_json_data: AttackData
    try:
        with open(ATTACK_JSON, "r") as f:
            attack_json_data = json.load(f)
    except FileNotFoundError:
        attack_json_data = {"teams": [], "flag_ids": {}}
    if team not in attack_json_data["teams"]:
        attack_json_data["teams"].append(team)
    service_state_data: ServiceStateData
    try:
        with open(SERVICE_STATE_JSON, "r") as f:
            service_state_data = json.load(f)
    except FileNotFoundError:
        service_state_data = {}

    # Run checks
    for flagstore, checker in CHECKERS.items():
        # Run checker
        rich.print(f"Running checker '{flagstore}'")
        result, msg = run_checker(checker, flagstore, host, team, tick)
        rich.print(f'Check result: {checkresult2rich(result)}, message: "{msg}"')

        # Update flag IDs
        attack_json_data["flag_ids"].setdefault(flagstore, {})[str(team)] = (
            get_flagids_from_state(
                get_state_path(team, flagstore), tick - MAX_FLAGID_COUNT + 1, tick
            )
        )

        # Update service state
        service_state_data.setdefault(flagstore, {}).setdefault(str(team), {})[
            str(tick)
        ] = {"result": str(result), "msg": msg}

    # Flush data
    attack_json_data["teams"] = [0]

    with open(ATTACK_JSON, "w") as f:
        json.dump(attack_json_data, f, indent=4)

    with open(SERVICE_STATE_JSON, "w") as f:
        json.dump(service_state_data, f, indent=4)


def main():
    global CHECKERS

    if len(sys.argv) < 2 or "-h" in sys.argv or "--help" in sys.argv:
        print(
            f"{__file__} <HOST> [TICK_LEN=60] [START_TICK=0] [TEAM_ID=0] [--debug] [--http]"
        )
        exit(1)

    argv = sys.argv
    if "--debug" in argv:
        print(">>> We will run in debug mode! <<<")
        argv.remove("--debug")
        logging.getLogger().setLevel("DEBUG")

    import rich

    rich.reconfigure(soft_wrap=True)
    import rich.logging

    logging.basicConfig(
        format="%(message)s",
        handlers=[rich.logging.RichHandler(show_path=False)],
        force=True,
    )

    CHECKERS = discover_checkers()
    if not CHECKERS:
        rich.print("ERROR: Couldn't find any checkers in current directory")
        exit(1)

    rich.print(f"Found {len(CHECKERS)} checkers:")
    for k, v in CHECKERS.items():
        rich.print(f"- {k!r} -> {v}")

    if "--http" in argv:
        argv.remove("--http")
        import http.server

        # TODO: Don't use SimpleHTTPRequestHandler, use custom one which returns only attack/team info
        httpd = http.server.ThreadingHTTPServer(
            ("0.0.0.0", 80), http.server.SimpleHTTPRequestHandler
        )
    else:
        httpd = None

    host = argv[1]
    tick_len = timedelta(seconds=60 if len(argv) <= 2 else int(argv[2]))
    tick = 0 if len(argv) <= 3 else int(argv[3])
    team = 0 if len(argv) <= 4 else int(argv[4])

    existing_states = list(
        filter(
            os.path.exists,
            [
                ATTACK_JSON,
                SERVICE_STATE_JSON,
                *(get_state_path(team, x) for x in CHECKERS),
            ],
        )
    )
    if existing_states:
        print(
            f"There are already some state files:\n- {"\n- ".join(existing_states)}\nDo you want to delete them? [Y/n]"
        )
        while True:
            c = input().lower()
            if len(c) == 0:
                c = "y"
            if c[0] in ("y", "n"):
                if c[0] == "y":
                    for path in existing_states:
                        try:
                            os.remove(path)
                        except FileNotFoundError:
                            pass
                break

    if httpd is not None:
        print(">>> Running HTTP server on port 80 <<<")
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

    next_tick = datetime.now().replace(second=0, microsecond=0) + tick_len
    try:
        while True:
            msg = f"{'#'*30} Tick: {tick} {'#'*30}"
            print(f"{'#'*len(msg)}\n{msg}\n{'#'*len(msg)}")
            do_tick(host, team, tick)
            until_next = (next_tick - datetime.now()).total_seconds()
            if until_next < 0:
                rich.print(
                    "[yellow]Warning: We are behind the schedule (last tick probably took too long)[/yellow]"
                )
            else:
                sleep(until_next)
            tick += 1
            next_tick += tick_len
    except KeyboardInterrupt:
        pass

    if httpd is not None:
        httpd.shutdown()


if __name__ == "__main__":
    main()
