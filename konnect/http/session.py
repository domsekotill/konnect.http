# Copyright 2023  Dom Sekotill <dom.sekotill@kodo.org.uk>

"""
Sessions are the primary entrypoint for users

Sessions handle global, prepared, shared state for requests.  They are also the primary
entrypoint for users, abstracting away request generation and scheduling, and yielding
responses for users to consume.

> **Note:**
> Unlike the `requests` package, there are no top-level functions for generating requests
> and producing responses, as they would have to be synchronous.
"""

from typing import Self

from konnect.curl import SECONDS
from konnect.curl import Multi
from konnect.curl import Time
from konnect.curl.scalars import Quantity

from ._request import Method
from ._request import Request
from .response import Response


class Session:
	"""
	A shared request state class

	Users *should* use a `Session` instance as an asynchronous context manager.
	"""

	# TODO: methods other than GET
	# TODO: cookies + cookiejars
	# TODO: proxies
	# TODO: authorisation

	def __init__(
		self, *,
		multi: Multi|None = None,
		request_class: type[Request] = Request,
	) -> None:
		self.multi = multi or Multi()
		self.request_class = request_class
		self.timeout: Quantity[Time] = 0 @ SECONDS
		self.connect_timeout: Quantity[Time] = 300 @ SECONDS

	async def __aenter__(self) -> Self:
		# For future use; likely downloading PAC files if used for proxies
		return self

	async def __aexit__(self, *exc_info: object) -> None:
		return

	async def head(self, url: str) -> Response:
		"""
		Perform an HTTP HEAD request
		"""
		req = self.request_class(self, Method.HEAD, url)
		return await req.get_response()

	async def get(self, url: str) -> Response:
		"""
		Perform an HTTP GET request
		"""
		req = self.request_class(self, Method.GET, url)
		return await req.get_response()

	async def put(self, url: str, data: bytes) -> Response:
		"""
		Perform a simple HTTP PUT request with in-memory data
		"""
		req = self.request_class(self, Method.PUT, url)
		await req.write(data)
		await req.write(b"")
		return await req.get_response()

	async def post(self, url: str, data: bytes) -> Response:
		"""
		Perform a simple HTTP POST request with in-memory data
		"""
		req = self.request_class(self, Method.POST, url)
		await req.write(data)
		await req.write(b"")
		return await req.get_response()
