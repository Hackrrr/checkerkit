from __future__ import annotations

import contextlib
import inspect
import logging
import random
import re
import string
import sys
from dataclasses import dataclass
from typing import Callable, NoReturn, ParamSpec, TypeAlias, TypeVar

from ctf_gameserver.checkerlib.lib import CheckResult

__version__ = "0.1"

P = ParamSpec("P")
T = TypeVar("T")

# TODO: Split this file
# I kinda did it in rush and so I just dumped everything into `__init__.py`


def randbool(chance: float = 0.5) -> bool:
    return random.random() < chance


def randstr(
    length: int | tuple[int, int],
    extra: str = "",
    chars: str = string.ascii_letters + string.digits,
) -> str:
    if isinstance(length, tuple):
        length = random.randint(*length)
    return "".join(random.choices(chars + extra, k=length))


def random_text(
    length: int | tuple[int, int],
    *,
    do_digits: bool = True,
    space_prob: float = 1 / 8,
    end_chars: str | None = ".,:?!",
) -> str:
    """Overcomplicated function to generate something text-like..."""
    if isinstance(length, tuple):
        length = random.randint(*length)

    if not end_chars:
        end_chars = ""

    o = ""

    while length:
        length -= 1
        # Do "end char" at the end more likely
        if end_chars and length == 0 and randbool(0.8):
            o += random.choice(end_chars)
            continue

        # Do spaces sometimes...
        if o and o[-1] != " " and length > 1 and randbool(space_prob):
            # ... and times prepend them with a "end char" to make a sentences
            if end_chars and randbool(1 / 5):
                o += random.choice(end_chars)
                length -= 1
            o += " "
        else:
            is_word_start = not o or o[-1] in (end_chars + " ")
            is_sentence_start = not o or (len(o) > 2 and o[-2] in end_chars)

            # Do "chunk of numbers" rarely instead of the words
            if do_digits and (
                (is_word_start and randbool(1 / 20)) or (o and o[-1] in string.digits)
            ):
                c = random.choice(string.digits)
            else:
                # Othewise just do a normal character...
                c = random.choice(string.ascii_lowercase)
                # ... with a chance to be an uppercase char at the start
                # of the words and always at the start of the sentences
                if is_sentence_start or (is_word_start and randbool(1 / 10)):
                    c = c.upper()
            o += c
    return o


def random_user_agent(only_python_requests: bool = False) -> str:
    import requests

    _requests_version = tuple(map(int, requests.__version__.split(".")))

    return (
        f"python-requests/2.{random.randint(_requests_version[1]-9,_requests_version[1]+1)}.{random.randint(0,2)}"
        if only_python_requests or randbool()
        else (
            f"curl/8.{random.randint(0,20)}.{random.randint(0,1)}"
            if randbool()
            else f"curl/7.{random.randint(73,88)}.{random.randint(0,1)}"
        )
    )


@dataclass
class CheckStatus(Exception):
    """Exception to simplify "returning" check result.

    You probably want to use this in conjuction with
    `checkStatusWrapper()` wrapper.
    """

    result: CheckResult
    info: str


def fail(msg: str) -> NoReturn:
    """Raises a FAULTY status with given message."""
    raise CheckStatus(CheckResult.FAULTY, msg)


@contextlib.contextmanager
def fail_context(context: str):
    """Sets "fail context" - `context` string gets prepended to
    the `fail(...)` messages (if such call/message occur).
    """
    try:
        yield
    except CheckStatus as e:
        assert e.info
        raise CheckStatus(e.result, f"{context}: {e.info}").with_traceback(
            sys.exc_info()[2]
        )


def set_fail_context(context: str) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Like `fail_context(...)` but as function wrapper."""

    def wrapper(f: Callable[P, T]) -> Callable[P, T]:
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
            with fail_context(context):
                return f(*args, **kwargs)

        return wrapped

    return wrapper


@contextlib.contextmanager
def faulty_as_flag_not_found():
    try:
        yield
    except CheckStatus as e:
        if e.result == CheckResult.FAULTY:
            e.result = CheckResult.FLAG_NOT_FOUND
        raise


# TODO: We can probably type annote this for `Never` when `cond` is falsy
def check(
    cond: bool,
    msg: str,
    err_log_msg: str | None = None,
    *args,
    frame_back_steps: int = 1,
    as_flag_not_found: bool = False,
):
    """Asserts that `cond` is `True` and raises `CheckStatus(CheckResult.FAULTY, msg)` if not.

    `err_log_msg` is internal error message for the logs,
    %-formatted with `*args`.
    """
    if not cond:
        frame = inspect.currentframe()
        assert frame is not None
        while frame_back_steps:
            assert frame.f_back is not None
            frame = frame.f_back
            frame_back_steps -= 1
        frame_info = inspect.getframeinfo(frame)
        assert frame_info.code_context is not None

        if not err_log_msg:
            err_log_msg = "%s"
            args = (frame_info.code_context[0].strip(),)

        logging.error(
            f"Check failed at %s@%s: {err_log_msg}",
            frame_info.filename,
            frame_info.lineno,
            *args,
        )

        raise CheckStatus(
            CheckResult.FLAG_NOT_FOUND if as_flag_not_found else CheckResult.FAULTY, msg
        )


def check_eq(actual, expected, msg: str):
    """Asserts `actual == expected`, calls `fail(msg)` if not."""
    check(
        actual == expected,
        msg,
        "Expected %r but got %r",
        expected,
        actual,
        frame_back_steps=2,
    )


# TODO: Type annotations (something like `value: T, items: SupportsIn[T]`)
def check_in(value, items, msg: str):
    """Asserts `value in items`, calls `fail(msg)` if not."""
    check(
        value in items,
        msg,
        "Value %r is not in %r",
        value,
        items,
        frame_back_steps=2,
    )


# TODO: `bytes` variant?
def check_regex(actual: str, regex: str, msg: str) -> tuple[str, ...]:
    """Asserts `actual` matches a `regex`, calls `fail(msg)` if not.

    Returns matched groups from the regex (on success).
    """
    m = re.match(regex, actual)
    check(
        m is not None,
        msg,
        "Value %r doesn't match regex %r",
        actual,
        regex,
        frame_back_steps=2,
    )
    assert m is not None
    return m.groups()


CheckResType: TypeAlias = "tuple[CheckResult, str]"


# TODO: Probably remove the parameter as we now have a `faulty_as_flag_not_found`
def check_status_wrapper(
    override_faulty_to_not_found: bool = False,
) -> Callable[[Callable[P, CheckResType]], Callable[P, CheckResType]]:
    """Wrapper for converting `CheckStatus` exception to correct
    `checkerlib` check results.

    You probably want to use this wrapper for `.place_flag()`,
    `.check_service()` and `.check_flag()` checker methods.
    """

    def wrapper(f: Callable[P, CheckResType]) -> Callable[P, CheckResType]:
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> CheckResType:
            try:
                return f(*args, **kwargs)
            except CheckStatus as e:
                if override_faulty_to_not_found and e.result == CheckResult.FAULTY:
                    e.result = CheckResult.FLAG_NOT_FOUND
                # TODO: If we want to support original (non-ECSC2022) Faust checkerlib,
                # then we need to return only `e.result`... Also GL;HF with typing :)
                # TBH solution is maybe to create own subclass of `BasicChecker`?
                # We could then do that magic there and we could also automatically
                # apply this `check_status_wrapper` decorator to methods.
                return (e.result, e.info)

        return wrapped

    return wrapper
