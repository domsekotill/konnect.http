# Copyright 2023-2025  Dom Sekotill <dom.sekotill@kodo.org.uk>

"""
Module providing a `konnect.curl.Request` implementation for HTTP requests

Using the `Request` class directly allows for finer-grained control of a request, including
asynchronously sending chunked data.

For many uses, there is a simple interface supplied by the `Session` class which does not
require users to interact directly with the classes supplied in this module.
"""

from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from enum import Enum
from enum import Flag
from enum import auto
from ipaddress import IPv4Address
from ipaddress import IPv6Address
from os import fspath
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Literal
from typing import Self
from typing import TypeAlias
from urllib.parse import urlparse
from warnings import warn

from konnect.curl import MILLISECONDS
from konnect.curl.abc import ConfigHandle
from konnect.curl.abc import GetInfoHandle
from pycurl import *
from typing_extensions import deprecated

from .cookies import check_cookie
from .exceptions import UnsupportedSchemeError
from .response import ReadStream
from .response import Response

if TYPE_CHECKING:
	from .authenticators import AuthHandler
	from .session import Session

ServiceIdentifier: TypeAlias = tuple[Literal["http", "https"], str]
TransportInfo: TypeAlias = tuple[IPv4Address | IPv6Address | str, int] | Path


__all__ = [
	"Method",
	"Request",
	"Transport",
	"encode_header",
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
	WRITE_HEADERS = auto()
	WRITE_BODY_AWAIT = auto()
	WRITE_BODY = auto()
	READ_HEADERS = auto()
	READ_BODY_AWAIT = auto()
	READ_BODY = auto()
	READ_TRAILERS = auto()


class Request:
	"""
	An HTTP request
	"""

	# This is the user-callable API for requests

	def __init__(self, session: Session, method: Method, url: str) -> None:
		self._request = CurlRequest(session, method, url)
		self._writer: BodySendStream|None = None

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

	@property
	def headers(self) -> list[bytes]:
		"""
		Wrap the underlying request's header list attribute
		"""
		return self._request.headers

	def set_auth_handler(self, handler: AuthHandler) -> None:
		"""
		Add an authentication handler to a request
		"""
		self._request.auth = handler

	async def body(self) -> BodySendStream:
		"""
		Return a writable object that can be used as an async context manager

		For example (note `async with await` to get a stream and use it as a context):

		>>> async def put_httpbin(session: Session) -> Response:
		...     req = Request(session, Method.PUT, "https://httpbin.org/put")
		...     async with await req.body() as stream:
		...         await stream.send(b"...")
		...     return await req.get_response()
		"""
		return await self._request.get_writer()

	@deprecated("use Request.body() as an async context to get a send stream")
	async def write(self, data: bytes, /) -> None:
		"""
		Write data to an upload request

		Signal an EOF by writing b""
		"""
		if not self._request.method.is_upload():
			raise RuntimeError(f"cannot write to request method {self._request.method}")
		if not self._writer:
			self._writer = await self.body()
		await self._writer.send(data)

	async def get_response(self) -> Response:
		"""
		Progress the request far enough to create a `Response` object and return it
		"""
		if self._writer:
			await self._writer.aclose()
		return await self._request.get_response()


class BodySendStream:
	"""
	Provides an interface for writing to request bodies

	Once a body has been completely written it must be finalised either by calling `aclose()`
	or by exiting the context created by using instances of this class as (async) context
	managers.
	"""

	def __init__(self, writefn: Callable[[bytes], Awaitable[None]]) -> None:
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
	Implementation of the `konnect.curl.Request` interface, callbacks and internal API

	It is not intended to be used directly by users.
	"""

	def __init__(self, session: Session, method: Method, url: str) -> None:
		self.session = session
		self.method = method
		self.url = url
		self.headers = list[bytes]()
		self.auth = get_authenticator(session.auth, url)
		self._handle: ConfigHandle|None = None
		self._stream: BodySendStream|None = None
		self._response: Response|None = None
		self._phase = Phase.INITIAL
		self._upcomplete = False
		self._sentall = False
		self._data = b""

	def configure_handle(self, handle: ConfigHandle) -> None:  # noqa: C901
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
				self._stream = BodySendStream(self.write)
			case Method.POST:
				handle.setopt(POST, True)
				handle.setopt(READFUNCTION, self._process_input)
				self._stream = BodySendStream(self.write)
			case Method.PATCH:
				handle.setopt(CUSTOMREQUEST, "PATCH")
				handle.setopt(UPLOAD, True)
				handle.setopt(INFILESIZE, -1)
				handle.setopt(READFUNCTION, self._process_input)
				self._stream = BodySendStream(self.write)
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

		if (cacert := self.session.ca_certificates):
			handle.setopt(CAPATH if cacert.is_dir() else CAINFO, fspath(cacert))

		if self.session.user_agent is not None:
			handle.setopt(USERAGENT, self.session.user_agent)

		if self.headers:
			handle.setopt(HTTPHEADER, self.headers)

	def has_update(self) -> bool:
		"""
		Return whether calling `response()` will return a value or raise `LookupError`

		This is part of the `konnect.curl.Request` interface.
		"""
		match self._phase:
			case Phase.WRITE_BODY_AWAIT:
				# After first call to input callback, always respond
				return True
			case Phase.WRITE_BODY:
				# While writing request body data, interrupt once buffer depleted
				return self._sentall
			case Phase.READ_BODY_AWAIT:
				assert self._response is not None
				return self._response.code >= 200
			case Phase.READ_BODY:
				return self._data != b""
		return False

	def get_update(self) -> BodySendStream|Response|bytes|None:
		"""
		Return a waiting response or raise `LookupError` if there is none

		See `has_response()` for checking for waiting responses.

		This is part of the `konnect.curl.Request` interface.
		"""
		if self._phase == Phase.WRITE_BODY_AWAIT:
			# First input callback call, subsequent calls can return request body data;
			# self._stream better be set, 'cos a BodySendStream is needed.
			self._phase = Phase.WRITE_BODY
			assert self._stream is not None, "input stream missing when input callback called"
			return self._stream
		if self._phase == Phase.WRITE_BODY:
			# No response in particular is needed after the buffer is drained, just an
			# interrupt.
			return None
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

	def completed(self, handle: GetInfoHandle) -> bytes:
		"""
		Complete the transfer by returning the final stream bytes

		This is part of the `konnect.curl.Request` interface.
		"""
		if self._phase == Phase.READ_TRAILERS:
			assert self._data == b""
			return b""
		assert self._phase == Phase.READ_BODY
		data, self._data = self._data, b""
		return data

	async def write(self, data: bytes, /) -> None:
		"""
		Write data to an upload request

		Signal an EOF by writing b""
		"""
		if data == b"":
			self._upcomplete = True
			if self._handle:
				await self._update_send_state()
				self._phase = Phase.READ_HEADERS
			return
		self._data += data
		if self._handle:
			while self._data:
				await self._update_send_state()

	async def _update_send_state(self) -> None:
		assert self._handle is not None
		self._sentall = False
		self._handle.pause(PAUSE_CONT)
		val = await self.session.multi.process(self)
		assert val is None, val

	def _process_input(self, size: int) -> bytes|int:
		if self._phase == Phase.WRITE_HEADERS:
			if not self._data:
				self._phase = Phase.WRITE_BODY_AWAIT
				return READFUNC_PAUSE
			self._phase = Phase.WRITE_BODY
		assert self._phase == Phase.WRITE_BODY, self._phase
		if not self._data:
			self._sentall = True
			return b"" if self._upcomplete else READFUNC_PAUSE
		data, self._data = self._data[:size], self._data[size:]
		return data

	def _process_header(self, data: bytes) -> None:
		if data.startswith(b"HTTP/"):
			self._phase = Phase.READ_HEADERS
			stream = ReadStream(self)
			self._response = Response(data.decode("ascii"), stream)
			return
		assert self._response is not None
		if data == b"\r\n":
			assert self._phase == Phase.READ_HEADERS, self._phase
			self._phase = Phase.WRITE_HEADERS if self._response.code == 100 else Phase.READ_BODY_AWAIT
			return
		field = self._split_field(data)
		match self._phase:
			case Phase.READ_HEADERS:
				self._response.headers.append(field)
			case Phase.READ_BODY:
				self._phase = Phase.READ_TRAILERS
				self._response.trailers.append(field)
			case Phase.READ_TRAILERS:
				self._response.trailers.append(field)
			case phase:
				raise AssertionError(f"unexpected phase {phase} when reading fields")

	def _split_field(self, field: bytes) -> tuple[str, bytes]:
		assert self._response is not None
		name, has_sep, value = field.partition(b":")
		if has_sep:
			return name.lower().decode("ascii"), value.strip()
		try:
			lname = self._response.headers[-1][0]
		except IndexError:
			raise ValueError("Non-field value when reading HTTP message fields")
		else:
			raise ValueError(f"Non-compliant multi-line field: {lname}")

	def _process_body(self, data: bytes) -> None:
		self._data += data

	async def _start_request(self) -> BodySendStream|Response:
		# Progress the request to the first checkpoint phase: WRITE_BODY_AWAIT
		assert self._phase == Phase.INITIAL, self._phase
		if auth := self.auth:
			await auth.prepare_request(self)
		self._phase = Phase.WRITE_HEADERS
		phase_response = await self.session.multi.process(self)
		assert isinstance(phase_response, BodySendStream | Response), phase_response
		return phase_response

	async def get_writer(self) -> BodySendStream:
		if self._phase != Phase.INITIAL:
			msg = f"only an unstarted request can return a body data stream"
			raise RuntimeError(msg)
		stream = await self._start_request()
		if not isinstance(stream, BodySendStream):
			msg = f"requests of type {self.method} do not support sending body data"
			raise ValueError(msg)
		return stream

	async def get_response(self) -> Response:
		"""
		Progress the request far enough to create a `Response` object and return it
		"""
		# Having the if-else condition this way around makes type checking easier
		if self._phase != Phase.INITIAL:
			resp = await self.session.multi.process(self)
		else:
			resp = await self._start_request()
		if isinstance(resp, BodySendStream):
			msg = f"uploading an empty body with a {self.method} request, " \
				f"did you mean to use Request.body() to get a BodySendStream?"
			warn(msg, stacklevel=3)
			await resp.aclose()
			resp = await self.session.multi.process(self)
		assert isinstance(resp, Response)
		if auth := self.auth:
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


def encode_header(name: str|bytes, value: bytes) -> bytes:
	"""
	Encode a header string without a line terminator
	"""
	if isinstance(name, str):
		name = name.encode("ascii")
	return b":".join((name, value))
