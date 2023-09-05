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

from konnect.curl import Multi

from ._request import Request
from .response import Response


class Session:
	"""
	A shared request state class

	Users *should* use a `Session` instance as an asynchronous context manager.
	"""

	# TODO: timeouts
	# TODO: methods other than GET
	# TODO: cookies + cookiejars
	# TODO: proxies
	# TODO: authorisation

	def __init__(self, *, multi: Multi|None = None) -> None:
		self.multi = multi or Multi()

	async def __aenter__(self) -> Self:
		# For future use; likely downloading PAC files if used for proxies
		return self

	async def __aexit__(self, *exc_info: object) -> None:
		return

	async def get(self, url: str) -> Response:
		"""
		Perform an HTTP GET request
		"""
		req = Request(self.multi, url)
		return await req.get_response()
