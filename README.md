# Checkerkit
This project is a collection of my helpers and utilities for writing an Attack/Defense CTF checkers. It is primarly intended for the Faust CTF platform but I believe you can make use of it even for other platforms... You can also use this for A/D exploits because how better look like a checker than using the functions that checker uses? Or you can use this even for completely different things than the A/D!

Quick start - run `pip install git+https://github.com/Hackrrr/checkerkit.git` ~~and have a quick look at [example](#full-example)~~ (not yet since I didn't wrote it yet... but there is some small example at the bottom of this README for time of being).

You can have a quick look at the [motivation](#motivation) section at the end to see why this exists and why could you want to use it. It started as a [small script](https://github.com/CzechCyberTeam/cwtc/blob/master/services-2023/baships/checkers/util.py) with a few helper functions for my first A/D service checker. Through the time, I created more A/D services and everytime I started a next one, I have taken the previous iteration of this script, copied it over to the new checker and made some improvements to it. Eventually I realized that it isn't just a "simple script" (and already wasn't for a long time) and it became quite annyoing to have "same thing" copied over across multiple projects. So I finally got myself to properly package it put it somewhere.

Note that this module is aimed at [ECSC 2022 fork of Faust gameserver/checkerlib](https://github.com/ECSC2022/ctf-gameserver) - this is important since the original Faust gameserver DOESN'T have a "checker messages" which are part of the reason this module exists. You can use this module also for original gameserver but you maybe encouter some errors you will need to fix. ~~The module tries to detect the original fork and adapt a bit but it may be broken in some cases since I don't really test it against the original.~~ (Not yet, it is on TODO list.)

You can install ECSC 2022 fork by running `pip install https://github.com/ECSC2022/ctf-gameserver/archive/refs/heads/ecsc2022.zip`. Experienced `pip` users might look a bit weird at such `pip install` command variant but believe me when I say you need to install it this specific way. No, `git+http` "protocol" doesn't work because the ECSC 2022 repo has private(?) repo as a submodule (which `pip` fails to fetch).

<!-- TODO: Support for both ECSC 2022 fork and original => Introspection for the checkerlib variant/version (original doesn't have checker messages) -->

# Features
For sake of brevity I assume familarity with relevant concepts. Tthat is - you are at least somewhat familiar with the general Faust checker "architecture". If you are ever written a Faust checker, then you are good to go. If you aren't familiar, then you will be probably confused at times and you won't get why some features are so nice. You can try to have a look at he [motivation](#motivation) section which showcases very simple Faust checker which should give you at least an idea of how a checker could look like.

Features of this module can be categoried into serveral categories:
- [`CheckResult`](#checkresult) - Handling of the checker result and checker messages
- [`fail()` and checker messsages](#fail-fail_context-and-checker-messages) - Helpers for checker messages and bit of advice for checker messages in general
- [Checks / Asserts](#checks--asserts) - Predefined set of "asserts" which can be used to check expected output
- [Clients](#clients) - classes for TCP and HTTP communication
- [Random](#random) - functions for randomizing the checker
- [Simple checker runner](#simple-checker-runner) - simple utility to test the checker wihtout running the gameserver

<!-- TODO: TL;DR install + usage (= import) -->

## `CheckStatus`
Core feature of this module is a `checkerkit.CheckStatus` execption which holds the check result (`checkerlib.CheckResult.FAULTY`) and the checker message. Idea is that you can decide on the check result from anywhere in the code by doing the following:

```py
from checkerkit import CheckStatus
from ctf_gameserver.checkerlib import CheckResult

def do_registration():
    # [...]
    if registration_failed:
        raise CheckStatus(CheckResult.FAULTY, "Registration failed")
    # [...]
```

This alone won't do it, the `CheckStatus` exceptions need to be catched. This is done by decorating the relevant functions (= `checkerlib.BaseChecker` methods) with `@checkerkit.check_status_wrapper`:

```py
from checkerkit import CheckStatus
from ctf_gameserver.checkerlib import BaseChecker

class Checker(BaseChecker):
    @check_status_wrapper()
    def place_flag(self, tick: int): ...

    @check_status_wrapper()
    def check_service(self): ...

    @check_status_wrapper()
    def check_flag(self, tick: int): ...
```

<!-- TODO: faulty_as_flag_not_found -->

## `fail()`, `fail_context()` and checker messages
`fail(msg)` is a function which is simple shorthand for doing `raise CheckStatus(CheckResult.FAULTY, msg)`. It is useful to have such shorthand since most of the "checker branches" ends with faulty state:
```py
def check_service():
    if do_command_abc() != "123":
        fail("Command ABC has wrong output")
    if do_command_efg() != "987":
        fail("Command EFG has wrong output")
```

Checker messages can be very helpful for the defenders when they try to implement the patch but they fail to do so correctly. You, as the checker author, can therefore decide whenever to provide more detailed error message so defenders can easily find out what exactly they messed up. It is not very helpful for checker to only respond with "faulty service"... such example is bit "extreme" but you can do such unhelpful messages even you don't really intend to:
```py
from checkerkit import fail

def send_raw(): ...
def recv_raw(): ...

def recv_line():
    raw = recv_raw()
    if raw[-1] != "\n":
        fail("Missing newline")
    return raw

def do_command_abc():
    send_raw("ABC\n")
    return recv_line()

def do_command_efg():
    send_raw("EFG\n")
    return recv_line()
```

The checker message from `fail()` call isn't much helpful if `recv_line()` is called from multiple places... does it mean that "ABC" command or "EFG" command is broken? Problem is much clearer in this simple example (especially when we don't show the "service side of the code" which can be much more complex) but one can imagine this being much larger problem when the source code is much bigger. Therefore it could be good idea to provide an information about a context in which the error/fail happened. `fail_context()` context manager solves this problem:
```py
from checkerkit import fail_context

def do_command_abc():
    with fail_context("Command ABC"):
        send_raw("ABC\n")
        return recv_line()
```

If the `recv_line()` fails (when called from `do_command_abc()` function), then checker message will be `"Command ABC: Missing newline"` (instead of just `"Missing newline"`). You can also nest `fail_context()` calls / context managers in which case each of them will add its own context to the final message.

It is quite often (for me) to wrap whole functions to the `fail_context()` and so you can use a `@set_fail_context()` decorator to rewrite the code above as (code will act exactly the same):
```py
from checkerkit import set_fail_context

@set_fail_context("Command ABC")
def do_command_abc():
    send_raw("ABC\n")
    return recv_line()
```

A few final notes about the checker messages:
- Beware of what you put there as everything you put there is basically a "public information" which can anyone see (to be more explicit: even the attackers can see that). If you decide to put e.g. some ID of a request to it (so defenders can exactly see which request was/is broken), then the ID shouldn't be useable as part of an attack/exploit.
- Beware that too much information could, in theory, lead to superman defenses - if you disclose what exactly checker does, then it may become trivial to whitelist only that few operations and block everything else. IMHO this is not a problem unless you are putting exact payloads that checker sends as checker messages.

## Checks / Asserts
One of the checker's job is to validate funcionality of the service. At is core, checkers basically act as an unit tests for the application/service. Therefore it would be nice to have some functions for validating the output.

`checkerkit` introduces several of them. Most generic is `check()` function:
```py
from checkerkit import check

def try_query(query):
    err = service.do_query(query)
    check(
        err == 0,
        "Query failed",
        "Query %r failed with error code %s",
        query,
        err
    )
```

First argument is check it self - it must be truth value to pass. If it falsy value, then `CheckStatus` is raised with faulty state and message equal to second argument of `check()`. Third argument is "internal" checker log message format string - log message isn't shown to the players, it is mostly just for debugging purposes. Remaning (positional) arguments are values for the log message format string. Log message is automatically prepended with filename and line number to find the failing check more easily. If log message is not provided, then the source line of `check()` is logged instead.

You probably won't use `check()` function itself but you will probably use more "specialized" functions instead: `check_eq()`, `check_in()` and `check_regex()`. These functions are simple wrappers around the `check()` function with the condition according to theirs name. They also take the (public) "error message" but they don't take the log message string as they already have one defined.

```py
from checkerkit import check_eq, check_in, check_regex

def get_result(query):
    output = service.get_sha256(query)
    expected = sha256(query)
    check_eq(output, expected, "SHA256 doesn't match")

    output = service.get_random_start_char(query)
    check_in(output, query[:5], "Random character is not from 5 chars of a query")

    output = service.get_response(query)
    number, text = check_regex(output, r"\[(\d+)\]: (.*)", "Response doesn't match the expected format")
```

Most interesting one is `check_regex()` which returns the matched groups in the provided regex. It is also good to point out that even for `check_eq()` order of arguments matter - not that it would change the result but the log message will be bit confusing (first argument is what you actually got as an output, second one is what you were expecting to get).

## Clients
"Clients" are helper classes for comunication. They make it easier to communicate with the services and have some very opinionated quality-of-life features. There are currently two of them: `checkerkit.client.TCPClient` and `checkerkit.client.HTTPClient` (which is wrapper around `requests`). Both are created using a hostname and a port number and both have a `.duplicate()` method which can be used to create new "client" instance with same settings.

In general, you might want to subclass these classes to define "service specific" operations. Example of how could it look like:
```py
from checkerkit import set_fail_context, check_eq
from checkerkit.client import TCPClient

class RandomService(TCPClient):
    def __init__(self, host: str, port: int = 5555):
        super().__init__(host, port)

    @set_fail_context("GET_RANDOM")
    def ask_random(self) -> int:
        self.recv_line("Command:")
        self.send_line("GET_RANDOM")
        return int(self.recv_line())

    @set_fail_context("SET_SEED")
    def set_seed(self, seed: int):
        self.recv_line("Command:")
        self.send_line(f"SET_SEED {seed}")
        self.recv_line("Ok")

# [...]

def predict_from_seed(seed: int) -> int: ...

def check_random_service(host: str):
    client = RandomService(host)
    client.set_seed(42)
    expected = predict_from_seed(42)
    check_eq(client.get_random(), expected, "Random number is too much random")
```

### `TCPClient`
`TCPClient` acts similar to `remote()` from `pwntools`:

```py
from checkerkit.client import TCPClient

client = TCPClient("localhost", 1337)
client.recv_until("Here is your output: ")
received = client.recv_line()
client.send_line(received[::-1])
```

One difference from `pwntools`'s `remote()` is that `TCPClient` works with `str` objects which are nicer to handle... but internally are `str` objects converted to `bytes` using `.encode()` method. Actually `TCPClient` is wrapper around a (custom) `Conn` class, which is actual "communication class" - you can access it via `client.conn` and then you can use "raw" `bytes` methods to do more "precise" things.

The most useful method of `TCPClient` is IMHO `.recv_line()` which can take an argument - when you provide a string as an argument, then received line from the service must be equal to the provided string (otherwise `fail()` is called).


### `HTTPClient`
At its core, `HTTPClient` is "just" a wrapper around `requests` module:

```py
from checkerkit.client import HTTPClient

client = HTTPClient("localhost", 1337)
resp = client.get("/") # Returns `requests.Response`
client.post("/login", {"user": "admin", "pass": "admin"})

import requests
client.send_request(requests.Requests("TRACE", client.base_server_url))
```

You can access the "internal" `requests.Session` object through `client.session`.

`HTTPClient` always checks for `response.ok` and raises a `checkerkit.RequestFailed` exception when it is `False`. `HTTPClient` is also able to automatically retry failed "connection/network" requests (= requests which failed e.g. due to timeout or host being unreachable) when `retries` parameter in constructor is set.

The main reason for `HTTPClient` is checker obfuscation. `HTTPClient` does following:
- Slight randomization of `User-Agent` header
- Random order of (some) HTTP headers
- Optional keep alive shenanings (`initial_keep_alive` parameter in constructor) to force requests into separate TCP connections

## Random
Module provides serveral functions for randomizing the checker.

The most basic one is `randbool()` and it does exactly what one would expect - returns either `True` and `False`. By default it picks `True` with probability of `0.5` but you can override it by passing probability (of `True`) as an argument:
```py
from checkerkit import randbool

def lucky_check():
    if randbool():
        fail("Unlucky, checker doesn't like you")
    if randbool(0.01):
        fail("Very unlucky, checker really doesn't like you")
```

Next we have `randstr()` function and it (yet again) does exactly what you think - it generates a random string of target length. By default, it generates string from charset `string.ascii_letters + string.digits`. `randstr()` takes up to three arguments: first is length of random string (which can be either exact length or length as tuple `(MIN_LENGTH, MAX_LENGTH)`), second are "extra characters" to add to the base charset and third argument is used "base charset" as a whole. "Final" charset used for the string generation is created by joing the base charset together with extra chars so you probably won't use both extra chars and base charset arguments at once. The reasoning behind extra chars is that most of the time you will want to "only" extend the default charset (ASCII letters and digits) and it would be bit annoying to always write `string.ascii_letters + string.digits`. You can also use extra chars argument to increase the probablity of some characters.

```py
from checkerkit import randstr
from checkerkit.client import HTTPClient

def create_note():
    client = HTTPClient("localhost", 1337)

    user = randstr((10, 12))
    password = randstr(16)

    client.post("/register", {"user": user, "pass": password})
    client.post("/login", {"user": user, "pass": password})

    note = randstr(100, " "*10) # Random string with higher probability of space
    client.post("/note/create", {"content": note})
```

Final useful function is `random_text()` which is bit more complex `randstr()`. Its purpose is to generate something that at least somehow reassembles "human-looking" text. Most notably - it tries to construct words and sentences. `random_text()` have only one mandatory parameter - length of generated text. It can be either exact number or range (like it is for `randstr()`). `random_text()` have several optional (keyword) parameters which can be used for tuning its output but I will skip them in this README for simplicity (see docstring of `random_text()` for more details):

```py
from checkerkit import random_text

def create_note():
    # ...

    note = random_text(100, do_digits=False)
    client.post("/note/create", {"content": note})
```

## Simple checker runner
`simple_checker_runner` is a command line utility which runs checkers in a loop simulating the checker "deployment". To start it simpli run `simple_checker_runner <TARGET_HOST>` inside the directory with the checker script(s).

First thing it needs to do is to find a checkers to run. It does it by looking at all files from currect directory matching the wildcard `checker*.py`, executing it and taking all classes which implement `ctf_gameserver.checkerlib.lib.BaseChecker` class (and which are not abstract). After obtaining the checker (classes), it runs in a loop, calling `ctf_gameserver.checkerlib.lib._run_check_steps()` (= calling `.place_flag()`, `.check_service()` and `.check_flag()` methods) for each checker. Two files are (re)generated after each loop: `./attack.json` containing a flag IDs for (last 5 ticks) and `./service_state.json` containing check(er) results (including checker messages).


<!-- # Full example -->
<!-- TODO: "Complete example" = my usual usage -->


# Motivation
> This section shows the main problem I had with a Faust checkerlib and what resulted in "core feature" of this module. TL;DR: `CheckerStatus`.

Let's say you want to write an A/D service. Great! Let's say you have written the service part and now all you need to do is to write a checker. Awesome! Uhh... not that much. Writing a checker may be much harder than writing the service itself, mostly depending on how complex you want your checker to be...

Writing a checker is tedious task. Faust provides a so called "checkerlib" which you should use to imlement your checker. Unfortunatelly even though it has "lib" in the name, it doesn't really do much from your perspective - it mostly "just" takes care of communication with a gamerserver controller but there are nearly no helpers or utitilities to make writing a checker easier. Don't get me wrong - the stuff it does is important and you really don't want to write it yourself and there are some really useful features (e.g. very useful `store_state()`/`load_state()` functions) but to write a checker you would like a bit more.

Simple Faust checker could look like this (shortened [Faust example](https://github.com/fausecteam/ctf-gameserver/blob/master/examples/checker/example_checker.py)):

```py
#!/usr/bin/env python3
from ctf_gameserver import checkerlib

def connect(ip): ...
def recv_line(conn): ...

class Checker(checkerlib.BaseChecker):
    def place_flag(self, tick):
        conn = connect(self.ip)
        flag = checkerlib.get_flag(tick)
        conn.sendall('SET {} {}\n'.format(tick, flag).encode())

        try:
            resp = recv_line(conn)
        except UnicodeDecodeError:
            return checkerlib.CheckResult.FAULTY
        if resp != "OK":
            return checkerlib.CheckResult.FAULTY

        conn.close()
        return checkerlib.CheckResult.OK

    def check_service(self):
        conn = connect(self.ip)
        conn.sendall(b"XXX\n")

        try:
            recv_line(conn)
        except UnicodeDecodeError:
            return checkerlib.CheckResult.FAULTY

        conn.close()
        return checkerlib.CheckResult.OK

    def check_flag(self, tick):
        conn = connect(self.ip)
        flag = checkerlib.get_flag(tick)
        conn.sendall("GET {}\n".format(tick).encode())

        try:
            resp = recv_line(conn)
        except UnicodeDecodeError:
            return checkerlib.CheckResult.FAULTY
        if resp != flag:
            return checkerlib.CheckResult.FLAG_NOT_FOUND

        conn.close()
        return checkerlib.CheckResult.OK

if __name__ == "__main__":
    checkerlib.run_check(Checker)
```

IMHO it is quite long but unfortunatelly this is probably as short checker as you can get. When you use this project/module, you won't get it much shorter. But once your checker gets more complex, you will find that is is getting quickly out of hand and then this module helps a lot.

Let's say you need to do some kinda of authentication upon connection and so we modify `connect()` function to also take an another argument. But someone tried to patch the service and failed and so authentication will be unsucessful. "Goal" of the checker in such case is to return `CheckResult.FAULTY` value from the checker methods. The question - how to return that from the `connect()` function? You could implement this in several ways, the most simple one is following:

```py
class Checker(checkerlib.BaseChecker):
    def check_flag(self, tick):
        try:
            conn = connect(self.ip, "pretty please")
        except:
            return checkerlib.CheckResult.FAULTY
        # [...]
```

Other simple way could be the following:
```py
class Checker(checkerlib.BaseChecker):
    def check_flag(self, tick):
        ok, conn = connect(self.ip, "pretty please")
        if not ok:
            return checkerlib.CheckResult.FAULTY
        # [...]
```

It works but I don't like it. The first variant added 3 more lines just to handle a result, second variant added 2 lines of code together with a variable (and it looks like an ugly Go error handling, which I personaly hate). And that is only one function call! You now need to do same handling/modification for other checker methods. And now imagine creating other functions, e.g. to perform the "SET" operation which can also fail... and what about nested function? If most "inner" function fails, then similar logic would need to be in each of the function just to propagate the result up.

But this all seems similar to something we already know, right? Exceptions. We already tried that in the first variant but it wasn't good enough. If we could just do something like `raise checkerlib.CheckResult.FAULTY` inside some function and it would be "magically" returned from the checker methods, then it would be great, wouldn't it? It would! And that is the core feature of this module: `CheckerStatus` exception together with decorator `@check_status_wrapper`. The checker could be now rewritten like this:

```py
from checkerkit import CheckerStatus, check_status_wrapper

def connect(ip, magic_word):
    # [...]
    if failed:
        raise CheckerStatus(checkerlib.CheckResult.FAULTY, "Magic word doesn't work")
    # [...]

class Checker(checkerlib.BaseChecker):
    @check_status_wrapper()
    def check_flag(self, tick):
        conn = connect(self.ip, "pretty please")
        # [...]
```

As you can see, the checker method itself is basically the same as the orignal one. You don't need to care about the error when you just want to use the `connect()` function.

You can also notice the string/message in `CheckerStatus` constructor. As it was said in the intro (top of the README), this module is intended for variant of Faust gameserver which implements/support the checker messages and that is exactly what this is - a checker message (which is intended to be seen on the scoreboard).

Final note or rather a tip actually - that `raise` line is sooo long... Luckily `checkerkit` has a shorthand for this. Instead of that line you can write just `fail("Magic word doesn't work")` (no need for `raise`; just don't forget to `from checkerkit import fail`).
