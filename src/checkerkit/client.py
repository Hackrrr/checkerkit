import logging
import random
import time
from typing import Callable, ParamSpec, Self, TypeVar

import requests
import requests.structures
from ctf_gameserver.checkerlib.lib import CheckResult, _is_conn_error

from . import CheckStatus, check, check_eq, fail, randbool, random_user_agent
from .conn import Conn

__all__ = [
    "TCPClient",
    "HTTPClient",
]

UNKNOWN_RESPONSE = "Unexpected/Mumbled response"
RETRY_WAIT_TIME = 3

P = ParamSpec("P")
T = TypeVar("T")


def fail_on_timeout_exception(f: Callable[P, T]) -> Callable[P, T]:
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return f(*args, **kwargs)
        except Exception as e:
            if _is_conn_error(e):
                logging.exception("Connection/Timeout error")
                fail("Connection/Timeout error")
            raise

    return wrapped


class TCPClient:
    """Helper class wrapper around `Conn` which provides basic asserts
    for receving responses. All operations apart from `.recv_raw()` work
    with `str` instead of `bytes` (and they call `fail(UNKNOWN_RESPONSE)`
    if they find some non-ASCII data in reponses).

    It is good idea to derive your own "service client" from this class.

    Simple usage::

        client = TCPClient(HOST, 1337)
        client.recv_line("Choice:") # Fails if "Choice:\\n" is not the response
        client.send_line("1")
        client2 = client.duplicate() # This is same as `TCPClient(HOST, 1337)`
        client.close()
        client2.close()

    """

    def __init__(self, host: str, port: int):
        self.host: str = host
        self.port: int = port
        self.conn: Conn = Conn(host, self.port)

    @fail_on_timeout_exception
    def recv_raw(self, size: int) -> bytearray:
        return self.conn.recv(size)

    @fail_on_timeout_exception
    def recv_until(self, delim: str, drop: bool = True) -> str:
        resp = self.conn.recv_until(delim.encode(), drop)
        try:
            return resp.decode()
        except:
            logging.error("Got non-ASCII response: %r", resp)
            fail(UNKNOWN_RESPONSE)

    def recv_line(self, expected: str | None = None) -> str:
        resp = self.recv_until("\n")
        if expected is not None:
            check_eq(resp, expected, UNKNOWN_RESPONSE)
        return resp

    @fail_on_timeout_exception
    def recv_expected(self, expected: str):
        for x in range(len(expected)):
            c = self.recv_raw(1).decode()
            check(
                c == expected[x],
                UNKNOWN_RESPONSE,
                "Expected %r but got %r so far",
                expected,
                expected[:x] + c,
            )

    @fail_on_timeout_exception
    def send_line(self, data: str):
        self.conn.send(f"{data}\n".encode())

    def duplicate(self) -> Self:
        return type(self)(self.host, self.port)

    def close(self):
        self.conn.close()


def repr_req(req: requests.Request, truncate_data: int = 0) -> str:
    data = (
        repr(req.data)
        if truncate_data == 0 or len(req.data) <= truncate_data
        else "..."
    )
    return f"Request(method={req.method!r}, url={req.url!r}, data={data})"


_requests_version = tuple(map(int, requests.__version__.split(".")))


class RequestFailed(CheckStatus):
    def __init__(self, request: requests.Request):
        self.request: requests.Request = request
        super().__init__(CheckResult.FAULTY, "Request failed")


class HTTPClient:
    """Helper class wrapper around `requests` which helps
    with handling of HTTP sessions.

    This class takes care of following:
    - Asserting `response.ok` and raising a `RequestFailed`
      exception when it is `False`.
    - Slight randomization of `User-Agent` header
    - Random order of (some) HTTP headers

    Addinationally you can force sessions to be "not-keeplive".
    This feature is enabled by passing `False` to constructor
    for `initial_keepalive` argument and it is implemented by
    setting a "Connection" HTTP header to `"close"`. It can be
    useful when you want to make it harder to do a traffic
    analysis as it results in multiple TCP connections instead
    of a single one.

    There is also support for unreliable networks/services by
    setting constructor keyword argument `retries` to number
    of retries it should attempt when connection fails.
    Note that class retries a request ONLY if it is (probably)
    a network error (e.g. closed port). Explicitly it DOES NOT
    retry e.g. when server returns a "500 Internal error".

    It is good idea to derive your own "service client" from this class.

    Simple usage::

        client = HTTPClient(HOST, 1337)
        resp = client.get("/") # Returns `requests.Response`
        client.post("/login", {"user": "admin", "pass": "admin"})
        client2 = client.duplicate() # This is same as `HTTPClient(HOST, 1337)`
        client2.send_request(requests.Requests("TRACE", client2.base_server_url))

    """

    def __init__(
        self, host: str, port: int, initial_keepalive: bool = True, *, retries: int = 0
    ):
        self.host: str = host
        self.port: int = port
        self.retries: int = retries
        self.session: requests.Session = self._create_session(initial_keepalive)

    def _create_session(self, keepalive: bool) -> requests.Session:
        session = requests.Session()

        if not keepalive:
            session.headers["Connection"] = "close"

        # Let's make User-Agent filtering a little bit harder...
        if randbool(0.2):
            session.headers["User-Agent"] = random_user_agent(True)

        # Let's also make header ordering checking little bit harder...
        if randbool(1 / 3):
            # TODO: This doesn't have such effect as it shuffles only Accept-Encoding, Close
            # and User-Agent headers (as others are not known yet)
            # Solution is probably to hook at some point when `PreparedRequest` is ready
            # and modify it. Note that we still want to have headers consistent so we somehow
            # need to remember the order of the headers (per session).
            headers = list(session.headers.items())
            random.shuffle(headers)
            # `dict` keys are ordered from Python 3.7 so we are good with that `dict` expression
            session.headers = requests.structures.CaseInsensitiveDict(
                {x[0]: x[1] for x in headers}
            )
        return session

    def duplicate(self, keepalive_value: bool = True) -> Self:
        return type(self)(self.host, self.port, keepalive_value, retries=self.retries)

    @property
    def base_server_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @fail_on_timeout_exception
    def send_request(self, req: requests.Request) -> requests.Response:
        logging.debug("Sending request: %s", repr_req(req, 50))
        current_try = 0
        while True:
            try:
                resp = self.session.send(self.session.prepare_request(req))
            except Exception as e:
                if current_try == self.retries:
                    # Maximum retries reached
                    raise RequestFailed(req) from e
                current_try += 1
                logging.warning(
                    "Request failed, going to retry it (%s/%s): %s",
                    current_try,
                    self.retries,
                    e,
                )
                time.sleep(RETRY_WAIT_TIME)
            else:
                break
        if not resp.ok:
            logging.error(
                "Failed response, code: %s; content: %s ",
                resp.status_code,
                resp.content,
            )
            raise RequestFailed(req)
        if current_try != 0:
            logging.warning("Request succeded after %s retries", current_try)
        return resp

    # TODO: Somehow propage typing annotations from `requests.Request`
    def get(self, endpoint: str, **kwargs) -> requests.Response:
        endpoint = endpoint.lstrip("/")
        return self.send_request(
            requests.Request("GET", f"{self.base_server_url}/{endpoint}", **kwargs)
        )

    def post(
        self, endpoint: str, data: dict[str, str] | None, **kwargs
    ) -> requests.Response:
        endpoint = endpoint.lstrip("/")
        # TODO: Randomize data order?
        if data is not None:
            # One would expect that `requests` would encode it but no...
            # This is little bit strange, because e.g. `\x01` is encoded.
            data = {
                k.replace("%", "%25"): v.replace("%", "%25") for k, v in data.items()
            }
        return self.send_request(
            requests.Request(
                "POST", f"{self.base_server_url}/{endpoint}", data=data, **kwargs
            )
        )
