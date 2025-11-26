# Copyright 2023-2025  Dom Sekotill <dom.sekotill@kodo.org.uk>

"""
Sessions are the primary entrypoint for users

Sessions handle global, prepared, shared state for requests.  They are also the primary
entrypoint for users, abstracting away request generation and scheduling, and yielding
responses for users to consume.

> **Note:**
> Unlike the `requests` package, there are no top-level functions for generating requests
> and producing responses, as they would have to be synchronous.

The `Session` class has several request methods which return `Response` objects.  These are
conveniences for creating `Request` objects, writing data to them (if appropriate for the
HTTP method), and awaiting a response from them.
"""

from copy import copy
from typing import Generic
from typing import Self
from urllib.parse import urlparse

from kodo.quantities import Quantity
from konnect.curl import SECONDS
from konnect.curl import Multi
from konnect.curl import Time

from .certificates import discover_ca_certs
from .cookies import Cookie
from .exceptions import UnsupportedSchemeError
from .request import Hook
from .request import Method
from .request import Request
from .request import ResponseT
from .request import ServiceIdentifier
from .request import TransportInfo
from .response import Response


class Session(Generic[ResponseT]):
	"""
	A shared request state class

	Users *should* use a `Session` instance as an asynchronous context manager.

	Users can provide a shared `Multi` object to allow connections to be shared between
	sessions (or even different protocol clients), otherwise a new `Multi` object is
	created; either option is safe in a single threaded environment but `Multi` objects
	must not be shared between threads.

	Users may also inject a subclass of `Response` to be returned by the various methods
	that return `Response` objects.
	"""

	# TODO(dom): cookiejars
	# https://code.kodo.org.uk/konnect/konnect.http/-/issues/11

	# TODO(dom): proxies
	# https://code.kodo.org.uk/konnect/konnect.http/-/issues/12

	def __init_subclass__(cls, *, request_class: type[Request] = Request) -> None:
		cls.default_request_class = request_class

	def __init__(
		self, *,
		multi: Multi|None = None,
		response_class: type[ResponseT] = Response,
	) -> None:
		self.multi = multi or Multi()
		self.response_class = response_class
		self.timeout: Quantity[Time] = 0 @ SECONDS
		self.connect_timeout: Quantity[Time] = 300 @ SECONDS
		self.transports = dict[ServiceIdentifier, TransportInfo]()
		self.auth = dict[ServiceIdentifier, Hook]()
		self.cookies = set[Cookie]()
		self.user_agent: str|None = None
		self.ca_certificates = discover_ca_certs()

	async def __aenter__(self) -> Self:
		# For future use; likely downloading PAC files if used for proxies
		return self

	async def __aexit__(self, *exc_info: object) -> None:
		return

	def clone(self) -> Self:
		"""
		Return a cloned `Session` object that can be independently modified
		"""
		return copy(self)

	async def head(self, url: str) -> ResponseT:
		"""
		Perform an HTTP HEAD request
		"""
		req = Request(self, Method.HEAD, url, response_class=self.response_class)
		return await req.get_response()

	async def get(self, url: str) -> ResponseT:
		"""
		Perform an HTTP GET request
		"""
		req = Request(self, Method.GET, url, response_class=self.response_class)
		return await req.get_response()

	async def put(self, url: str, data: bytes) -> ResponseT:
		"""
		Perform a simple HTTP PUT request with in-memory data
		"""
		req = Request(self, Method.PUT, url, response_class=self.response_class)
		async with await req.body() as body:
			await body.send(data)
		return await req.get_response()

	async def post(self, url: str, data: bytes) -> ResponseT:
		"""
		Perform a simple HTTP POST request with in-memory data
		"""
		req = Request(self, Method.POST, url, response_class=self.response_class)
		async with await req.body() as body:
			await body.send(data)
		return await req.get_response()

	async def patch(self, url: str, data: bytes) -> ResponseT:
		"""
		Perform a simple HTTP PATCH request with in-memory data
		"""
		req = Request(self, Method.PATCH, url, response_class=self.response_class)
		async with await req.body() as body:
			await body.send(data)
		return await req.get_response()

	async def delete(self, url: str) -> ResponseT:
		"""
		Perform an HTTP DELETE request
		"""
		req = Request(self, Method.DELETE, url, response_class=self.response_class)
		return await req.get_response()

	def add_redirect(self, url: str, target: TransportInfo) -> None:
		"""
		Add a redirect for a URL base to a target address/port

		The URL base should be a schema and 'hostname[:port]' only,
		e.g. `"http://example.com"`; anything else will be ignored but may have an effect in
		future releases.
		"""
		parts = urlparse(url)
		if parts.scheme not in ("http", "https"):
			raise UnsupportedSchemeError(url)
		self.transports[parts.scheme, parts.netloc] = target  # type: ignore[index]

	def remove_redirect(self, url: str) -> None:
		"""
		Remove a redirect for a URL base

		See `add_redirect()` for the format of the URL base.
		"""
		parts = urlparse(url)
		if parts.scheme not in ("http", "https"):
			raise UnsupportedSchemeError(url)
		del self.transports[parts.scheme, parts.netloc]  # type: ignore[arg-type]

	def add_authentication(self, url: str, authenticator: Hook) -> None:
		"""
		Add an authentication handler to use when accessing URLs under the given URL base

		The URL base should be a schema and 'hostname[:port]' only,
		e.g. `"http://example.com"`; anything else will be ignored but may have an effect in
		future releases.
		"""
		parts = urlparse(url)
		if parts.scheme not in ("http", "https"):
			raise UnsupportedSchemeError(url)
		self.auth[parts.scheme, parts.netloc] = authenticator  # type: ignore[index]

	def remove_authentication(self, url: str) -> None:
		"""
		Remove an authentication handler for a URL base

		See `add_authentication()` for the format of the URL base
		"""
		parts = urlparse(url)
		if parts.scheme not in ("http", "https"):
			raise UnsupportedSchemeError(url)
		del self.auth[parts.scheme, parts.netloc]  # type: ignore[arg-type]

	def add_cookie(self, url: str, name: str, value: bytes) -> None:
		"""
		Add a cookie for the given URL base
		"""
		parts = urlparse(url)
		if parts.hostname is None:
			raise ValueError(f"a hostname is required in URL: {url}")
		cookie = Cookie(name, value, None, parts.hostname, parts.path, secure=(parts.scheme == "https"))
		self.cookies.add(cookie)
