# Copyright 2023-2026  Dom Sekotill <dom.sekotill@kodo.org.uk>

"""
Module providing a `konnect.curl.Request` implementation for HTTP requests

Using the `Request` class directly allows for finer-grained control of a request, including
asynchronously sending chunked data.

For many uses, there is a simple interface supplied by the `Session` class which does not
require users to interact directly with the classes supplied in this module.
"""

from __future__ import annotations

from enum import Enum
from enum import Flag
from enum import auto
from http import HTTPStatus
from ipaddress import IPv4Address
from ipaddress import IPv6Address
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Generic
from typing import Literal
from typing import Protocol
from typing import Self
from typing import TypeAlias
from typing import TypeVar
from urllib.parse import urljoin
from urllib.parse import urlparse
from warnings import warn

from konnect.curl import MILLISECONDS
from konnect.curl.certificates import CertificateSource
from konnect.curl.certificates import CommonEncodedSource
from konnect.curl.certificates import PrivateKeySource
from konnect.curl.certificates import add_ca_certificate
from konnect.curl.certificates import add_client_certificate
from pycurl import *

from .cookies import check_cookie
from .exceptions import UnsupportedSchemeError
from .response import ReadStream
from .response import Response

if TYPE_CHECKING:
	from collections.abc import Awaitable
	from collections.abc import Callable
	from collections.abc import Iterator
	from collections.abc import Mapping

	from konnect.curl.abc import ConfigHandle
	from konnect.curl.abc import GetInfoHandle

	from .session import Session

ServiceIdentifier: TypeAlias = tuple[Literal["http", "https"], str]
TransportInfo: TypeAlias = tuple[IPv4Address | IPv6Address | str, int] | Path

ResponseT = TypeVar("ResponseT", bound=Response)


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


class Hook(Protocol[ResponseT]):
	"""
	Abstract definition of request and response hooks
	"""

	async def prepare_request(self, request: Request[ResponseT], /) -> None:
		"""
		Process a request instance before the request is enacted

		This method can be used by handlers to modify requests (such as adding headers or
		adding session cookies); it is a coroutine to allow handlers to inject pre-requests
		(such as an auth-flow) before the request.  Any such flow SHOULD use the request's
		session.
		"""
		...

	async def process_response(
		self, request: Request[ResponseT], response: ResponseT, /
	) -> ResponseT:
		"""
		Examine a response to a request and perform any follow-up actions

		This method may return the passed response if no further actions need to be taken;
		or further requests can be made if necessary, after which a new successful response
		to an identical request must be returned.
		"""
		...


class Request(Generic[ResponseT]):
	"""
	An HTTP request
	"""

	# This is the user-callable API for requests

	def __init__(
		self,
		session: Session[ResponseT],
		method: Method,
		url: str,
		*,
		response_class: type[ResponseT] = Response,
	) -> None:
		self._session = session
		self._method = method
		self._url = url
		self.response_class = response_class
		self.certificate: (
			tuple[CertificateSource, PrivateKeySource]
			| tuple[CommonEncodedSource]
			| CommonEncodedSource
			| None
		) = None
		self.auth_hook = get_authenticator(session.auth, url)
		self._headers = list[bytes]()
		self._handle = CurlHandle(self)

	def __repr__(self) -> str:
		return f"<Request {self.method.name} {self.url}>"

	@property
	def session(self) -> Session[ResponseT]:
		"""
		The session to use with this request
		"""
		return self._session

	@property
	def method(self) -> Method:
		"""
		The request HTTP method
		"""
		return self._method

	@property
	def url(self) -> str:
		"""
		The request URL
		"""
		return self._url

	@property
	def headers(self) -> list[bytes]:
		"""
		The request headers
		"""
		return self._headers

	def copy(self, *, url: str = "", method: Method | None = None) -> Self:
		"""
		Return a new Request with the same configuration and optionally a new URL or method

		The URL may be a relative one, in which case it will be resolved relative to the
		current URL.
		"""
		cls = type(self)
		inst = cls(
			self._session,
			method or self._method,
			urljoin(self._url, url),
			response_class=self.response_class,
		)
		inst.auth_hook = self.auth_hook
		inst.certificate = self.certificate
		inst._headers[:] = self._headers
		return inst

	def add_header(self, name: str, value: bytes | str) -> None:
		"""
		Add an HTTP header by name to send with the request
		"""
		header = b": ".join(
			(name.encode("ascii"), value if isinstance(value, bytes) else value.encode("ascii"))
		)
		self._headers.append(header)

	def set_auth_handler(self, handler: Hook[ResponseT]) -> None:
		"""
		Add an authentication handler to a request
		"""
		self.auth_hook = handler

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
		if self._handle is None:
			msg = f"{type(self)}.body() cannot be called after {type(self)}.get_response()"
			raise RuntimeError(msg)
		return await self._handle.get_writer()

	async def get_response(self, *, follow_redirects: bool = False) -> ResponseT:
		"""
		Progress the request far enough to create a `Response` object and return it
		"""
		# Drop CurlHandle object so it can be GC'd once the Response.stream is closed
		request, self._handle = self._handle, None
		if request is None:
			msg = f"{type(self)}.get_response() already called on {self}"
			raise RuntimeError(msg)
		response = await request.get_response()
		if not follow_redirects:
			return response
		method = self._method
		while redirect := response.get_redirect():
			previous = response
			match response.code:
				case HTTPStatus.MOVED_PERMANENTLY:
					msg = f"Moved permanently (301): {response.request.url} -> {redirect}"
					warn(msg, stacklevel=2)
					method = Method.HEAD if method is Method.HEAD else Method.GET
				case HTTPStatus.PERMANENT_REDIRECT:
					msg = f"Permanently redirected (308): {response.request.url} -> {redirect}"
					warn(msg, stacklevel=2)
				case HTTPStatus.FOUND | HTTPStatus.SEE_OTHER:
					method = Method.HEAD if method is Method.HEAD else Method.GET
			response = await self.copy(url=redirect, method=method).get_response()
			response.previous = previous
		return response


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
		# Unlike CurlHandle.write, passing an empty string is a no-op
		if not data:
			return
		await self._write(data)


class CurlHandle(Generic[ResponseT]):
	"""
	Implementation of the `konnect.curl.Request` interface, callbacks and internal API

	It is not intended to be used directly by users.
	"""

	def __init__(self, request: Request[ResponseT]) -> None:
		self.request = request
		self._handle: ConfigHandle|None = None
		self._stream: BodySendStream|None = None
		self._response: ResponseT | None = None
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

		url = self.request.url
		session = self.request.session

		handle.setopt(URL, url)

		match self.request.method:
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

		match get_transport(session.transports, url):
			case Path() as path:
				handle.setopt(UNIX_SOCKET_PATH, path.as_posix())
			case [(IPv4Address() | IPv6Address() | str()) as host, int(port)]:
				handle.setopt(CONNECT_TO, [f"::{host}:{port}"])
			case transport:
				raise TypeError(f"Unknown transport: {transport!r}")

		handle.setopt(COOKIE, self.get_cookies())

		handle.setopt(VERBOSE, 0)
		handle.setopt(NOPROGRESS, 1)

		handle.setopt(TIMEOUT_MS, session.timeout // MILLISECONDS)
		handle.setopt(CONNECTTIMEOUT_MS, session.connect_timeout // MILLISECONDS)

		handle.setopt(PIPEWAIT, 1)
		handle.setopt(DEFAULT_PROTOCOL, "https")
		# handle.setopt(PROTOCOLS_STR, "http,https")
		# handle.setopt(REDIR_PROTOCOLS_STR, "http,https")
		handle.setopt(PROTOCOLS, PROTO_HTTP | PROTO_HTTPS)
		handle.setopt(REDIR_PROTOCOLS, PROTO_HTTP | PROTO_HTTPS)
		handle.setopt(HEADERFUNCTION, self._process_header)
		handle.setopt(WRITEFUNCTION, self._process_body)

		match self.request.certificate:
			case None:
				pass
			case [cert, key]:
				add_client_certificate(handle, cert, key)
			case [cert] | cert:
				add_client_certificate(handle, cert)

		if cacert := session.ca_certificates:
			add_ca_certificate(handle, cacert)

		if session.user_agent is not None:
			handle.setopt(USERAGENT, session.user_agent)

		if self.request.headers:
			handle.setopt(HTTPHEADER, self.request.headers)

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

	def get_update(self) -> BodySendStream | ResponseT | bytes | None:
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
		val = await self.request.session.multi.process(self)
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
			self._response = self.request.response_class(data.decode("ascii"), stream)
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

	async def _start_request(self) -> BodySendStream | ResponseT:
		# Progress the request to the first checkpoint phase: WRITE_BODY_AWAIT
		assert self._phase == Phase.INITIAL, self._phase
		for hook in self.get_hooks():
			await hook.prepare_request(self.request)
		self._phase = Phase.WRITE_HEADERS
		phase_response = await self.request.session.multi.process(self)
		assert isinstance(phase_response, BodySendStream | Response), phase_response
		return phase_response

	async def get_writer(self) -> BodySendStream:
		if self._phase != Phase.INITIAL:
			msg = f"only an unstarted request can return a body data stream"
			raise RuntimeError(msg)
		stream = await self._start_request()
		if not isinstance(stream, BodySendStream):
			msg = f"requests of type {self.request.method} do not support sending body data"
			raise ValueError(msg)
		return stream

	async def get_response(self) -> ResponseT:
		"""
		Progress the request far enough to create a `Response` object and return it
		"""
		# Having the if-else condition this way around makes type checking easier
		if self._phase != Phase.INITIAL:
			resp = await self.request.session.multi.process(self)
		else:
			resp = await self._start_request()
		if isinstance(resp, BodySendStream):
			msg = (
				f"uploading an empty body with a {self.request.method} request, "
				f"did you mean to use Request.body() to get a BodySendStream?"
			)
			warn(msg, stacklevel=3)
			await resp.aclose()
			resp = await self.request.session.multi.process(self)
		assert isinstance(resp, Response)
		for hook in self.get_hooks():
			resp = await hook.process_response(self.request, resp)
		return resp

	async def get_data(self) -> bytes:
		"""
		Return chunks of received data from the body of the response to the request
		"""
		if self._phase != Phase.READ_BODY:
			raise RuntimeError("get_data() can only be called after get_response()")
		data = await self.request.session.multi.process(self)
		assert isinstance(data, bytes), repr(data)
		return data

	def get_cookies(self) -> bytes:
		"""
		Return the encoded cookie values to be sent with the request
		"""
		return b"; ".join(
			bytes(cookie)
			for cookie in self.request.session.cookies
			if check_cookie(cookie, self.request.url)
		)

	def get_hooks(self) -> Iterator[Hook[ResponseT]]:
		if hook := self.request.auth_hook:
			yield hook
		yield from self.request.session.hooks


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
	authenticators: Mapping[ServiceIdentifier, Hook[ResponseT]],
	url: str,
) -> Hook[ResponseT] | None:
	"""
	For a given http:// or https:// URL, return any authentication `Hook` associated with it
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
