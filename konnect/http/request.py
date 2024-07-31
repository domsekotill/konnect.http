# Copyright 2023-2024  Dom Sekotill <dom.sekotill@kodo.org.uk>

"""
Module providing a `konnect.curl.Request` implementation for HTTP requests

Using the `Request` class directly allows for finer-grained control of a request, including
asynchronously sending chunked data.

For many uses, there is a simple interface supplied by the `Session` class which does not
require users to interact directly with the classes supplied in this module.
"""

# mypy: disable-error-code="no-untyped-call"

from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from enum import Enum
from enum import Flag
from enum import auto
from ipaddress import IPv4Address
from ipaddress import IPv6Address
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Literal
from typing import Mapping
from typing import Self
from typing import TypeAlias
from typing import Union
from urllib.parse import urlparse

from konnect.curl import MILLISECONDS
from pycurl import *

from .cookies import check_cookie
from .exceptions import UnsupportedSchemeError
from .response import ReadStream
from .response import Response

if TYPE_CHECKING:
	from .authenticators import AuthHandler
	from .session import Session

ServiceIdentifier: TypeAlias = tuple[Literal["http", "https"], str]
TransportInfo: TypeAlias = Union[
	tuple[IPv4Address | IPv6Address | str, int],
	Path,
]


__all__ = [
	"Method",
	"Transport",
	"Request",
]


class Method(Enum):
	"""
	HTTP methods supported by konnect.http
	"""

	GET = auto()
	HEAD = auto()
	PUT = auto()
	POST = auto()
	PATCH = auto()
	DELETE = auto()

	def is_upload(self) -> bool:
		"""
		Return whether a method allows an upload body
		"""
		return self in (Method.PUT, Method.POST, Method.PATCH)


class Transport(Flag):
	"""
	Transport layer types
	"""

	TCP = auto()
	UNIX = auto()
	TLS = auto()


class Phase(Enum):

	INITIAL = auto()
	READ_HEADERS = auto()
	READ_BODY_AWAIT = auto()
	READ_BODY = auto()
	READ_TRAILERS = auto()


class Request:
	"""
	This class provides the user-callable API for requests
	"""

	def __init__(self, session: Session, method: Method, url: str):
		self._request = CurlRequest(session, method, url)

	def __repr__(self) -> str:
		return f"<Request {self._request.method.name} {self._request.url}>"

	@property
	def session(self) -> Session:
		"""
		Wrap the underlying request's session attribute
		"""
		return self._request.session

	@property
	def method(self) -> Method:
		"""
		Wrap the underlying request's method attribute
		"""
		return self._request.method

	@property
	def url(self) -> str:
		"""
		Wrap the underlying request's url attribute
		"""
		return self._request.url

	def body(self) -> BodySendStream:
		"""
		Return a writable object that can be used as an async context manager
		"""
		if not self._request.method.is_upload():
			raise RuntimeError(f"cannot write to request method {self._request.method}")
		return BodySendStream(self._request.write)

	async def write(self, data: bytes, /) -> None:
		"""
		Write data to an upload request

		Signal an EOF by writing b""
		"""
		if not self._request.method.is_upload():
			raise RuntimeError(f"cannot write to request method {self._request.method}")
		await self._request.write(data)

	async def get_response(self) -> Response:
		"""
		Progress the request far enough to create a `Response` object and return it
		"""
		return await self._request.get_response()


class BodySendStream:
	"""
	Provides an interface for writing to request bodies

	Once a body has been completely written it must be finalised either by calling `close()`
	or by exiting the context created by using instances of this class as (async) context
	managers.
	"""

	def __init__(self, writefn: Callable[[bytes], Awaitable[None]]):
		self._write = writefn

	async def __aenter__(self) -> Self:
		return self

	async def __aexit__(self, exc_type: type[Exception]|None, *excinfo: object) -> None:
		if exc_type is None:
			await self._write(b"")

	async def aclose(self) -> None:
		"""
		Finalise the body once complete
		"""
		await self._write(b"")

	async def send(self, data: bytes, /) -> None:
		"""
		Write body data to an upload request
		"""
		# Unlike CurlRequest.write, passing an empty string is a no-op
		if not data:
			return
		await self._write(data)


class CurlRequest:
	"""
	This class provides the `konnect.curl.Request` interface, callbacks and internal API

	It is not intended to be used directly by users.
	"""

	def __init__(self, session: Session, method: Method, url: str):
		self.session = session
		self.method = method
		self.url = url
		self._handle: Curl|None = None
		self._response: Response|None = None
		self._phase = Phase.INITIAL
		self._upcomplete = False
		self._data = b""

	def configure_handle(self, handle: Curl) -> None:
		"""
		Configure a konnect.curl.Curl handle for this request

		This is part of the `konnect.curl.Request` interface.
		"""
		self._handle = handle

		handle.setopt(URL, self.url)

		match self.method:
			case Method.HEAD:
				handle.setopt(NOBODY, True)
			case Method.PUT:
				handle.setopt(UPLOAD, True)
				handle.setopt(INFILESIZE, -1)
				handle.setopt(READFUNCTION, self._process_input)
			case Method.POST:
				handle.setopt(POST, True)
				handle.setopt(READFUNCTION, self._process_input)
			case Method.PATCH:
				handle.setopt(CUSTOMREQUEST, "PATCH")
				handle.setopt(UPLOAD, True)
				handle.setopt(INFILESIZE, -1)
				handle.setopt(READFUNCTION, self._process_input)
			case Method.DELETE:
				handle.setopt(CUSTOMREQUEST, "DELETE")

		match get_transport(self.session.transports, self.url):
			case Path() as path:
				handle.setopt(UNIX_SOCKET_PATH, path.as_posix())
			case [(IPv4Address() | IPv6Address() | str()) as host, int(port)]:
				handle.setopt(CONNECT_TO, [f"::{host}:{port}"])
			case transport:
				raise TypeError(f"Unknown transport: {transport!r}")

		handle.setopt(COOKIE, self.get_cookies())

		handle.setopt(VERBOSE, 0)
		handle.setopt(NOPROGRESS, 1)

		handle.setopt(TIMEOUT_MS, self.session.timeout // MILLISECONDS)
		handle.setopt(CONNECTTIMEOUT_MS, self.session.connect_timeout // MILLISECONDS)

		handle.setopt(PIPEWAIT, 1)
		handle.setopt(DEFAULT_PROTOCOL, "https")
		# handle.setopt(PROTOCOLS_STR, "http,https")
		# handle.setopt(REDIR_PROTOCOLS_STR, "http,https")
		handle.setopt(PROTOCOLS, PROTO_HTTP|PROTO_HTTPS)
		handle.setopt(REDIR_PROTOCOLS, PROTO_HTTP|PROTO_HTTPS)
		handle.setopt(HEADERFUNCTION, self._process_header)
		handle.setopt(WRITEFUNCTION, self._process_body)

		if self.session.user_agent is not None:
			handle.setopt(USERAGENT, self.session.user_agent)

	def has_response(self) -> bool:
		"""
		Return whether calling `response()` will return a value or raise `LookupError`

		This is part of the `konnect.curl.Request` interface.
		"""
		match self._phase:
			case Phase.READ_BODY_AWAIT:
				assert self._response is not None
				return self._response.code >= 200
			case Phase.READ_BODY:
				return self._data != b""
		return False

	def response(self) -> Response|bytes:
		"""
		Return a waiting response or raise `LookupError` if there is none

		See `has_response()` for checking for waiting responses.

		This is part of the `konnect.curl.Request` interface.
		"""
		if self._phase == Phase.READ_BODY_AWAIT:
			self._phase = Phase.READ_BODY
			assert self._response is not None
			if self._response.code < 200:
				raise LookupError
			return self._response
		if self._phase != Phase.READ_BODY or not self._data:
			raise LookupError
		data, self._data = self._data, b""
		return data

	def completed(self) -> bytes:
		"""
		Complete the transfer by returning the final stream bytes

		This is part of the `konnect.curl.Request` interface.
		"""
		assert self._phase == Phase.READ_BODY
		data, self._data = self._data, b""
		return data

	async def write(self, data: bytes, /) -> None:
		"""
		Write data to an upload request

		Signal an EOF by writing b""
		"""
		# TODO: apply back-pressure when self._data reaches a certain length
		# TODO: use a nicer buffer implementation than just appending
		if data == b"":
			self._upcomplete = True
		elif self._data:
			self._data += data
		else:
			self._data = data
		if self._handle:
			self._handle.pause(PAUSE_CONT)

	def _process_input(self, size: int) -> bytes|int:
		if self._data:
			data, self._data = self._data[:size], self._data[size:]
			return data
		if self._upcomplete:
			return b""
		return READFUNC_PAUSE

	def _process_header(self, data: bytes) -> None:
		if data.startswith(b"HTTP/"):
			self._phase = Phase.READ_HEADERS
			stream = ReadStream(self)
			self._response = Response(data.decode("ascii"), stream)
			return
		assert self._response is not None
		if data == b"\r\n":
			self._phase = Phase.READ_BODY_AWAIT
			return
		if self._phase not in (Phase.READ_HEADERS, Phase.READ_TRAILERS):
			self._phase = Phase.READ_TRAILERS
		self._response.headers.append(self._split_field(data))

	def _split_field(self, field: bytes) -> tuple[str, bytes]:
		assert self._response is not None
		name, has_sep, value = field.partition(b":")
		if has_sep:
			# TODO: test performance of str.lower() vs. bytes.lower()
			return name.lower().decode("ascii"), value.strip()
		try:
			lname = self._response.headers[-1][0]
		except IndexError:
			raise ValueError("Non-field value when reading HTTP message fields")
		else:
			raise ValueError(f"Non-compliant multi-line field: {lname}")

	def _process_body(self, data: bytes) -> None:
		self._data += data

	async def get_response(self) -> Response:
		"""
		Progress the request far enough to create a `Response` object and return it
		"""
		if self._phase != Phase.INITIAL:
			raise RuntimeError("get_response() can only be called on an unstarted request")
		auth = get_authenticator(self.session.auth, self.url)
		if auth is not None:
			await auth.prepare_request(self)
		resp = await self.session.multi.process(self)
		assert isinstance(resp, Response)
		if auth is not None:
			resp = await auth.process_response(self, resp)
		return resp

	async def get_data(self) -> bytes:
		"""
		Return chunks of received data from the body of the response to the request
		"""
		if self._phase != Phase.READ_BODY:
			raise RuntimeError("get_data() can only be called after get_response()")
		data = await self.session.multi.process(self)
		assert isinstance(data, bytes), repr(data)
		return data

	def get_cookies(self) -> bytes:
		"""
		Return the encoded cookie values to be sent with the request
		"""
		return b"; ".join(
			bytes(cookie)
			for cookie in self.session.cookies
			if check_cookie(cookie, self.url)
		)


def get_transport(
	transports: Mapping[ServiceIdentifier, TransportInfo],
	url: str,
) -> TransportInfo:
	"""
	For a given http:// or https:// URL, return suitable transport layer information
	"""
	parts = urlparse(url)
	if parts.hostname is None:
		raise ValueError("An absolute URL is required")
	match parts.scheme:
		case "https" as scheme:
			default_port = 443
		case "http" as scheme:
			default_port = 80
		case _:
			raise UnsupportedSchemeError(url)
	try:
		return transports[scheme, parts.netloc]
	except KeyError:
		return parts.hostname, parts.port or default_port


def get_authenticator(
	authenticators: Mapping[ServiceIdentifier, AuthHandler],
	url: str,
) -> AuthHandler|None:
	"""
	For a given http:// or https:// URL, return any `AuthHandler` associated with it
	"""
	parts = urlparse(url)
	if parts.hostname is None:
		raise ValueError("An absolute URL is required")
	if parts.scheme not in ("http", "https"):
		raise UnsupportedSchemeError(url)
	try:
		return authenticators[parts.scheme, parts.netloc]  # type: ignore[index]
	except KeyError:
		return None
