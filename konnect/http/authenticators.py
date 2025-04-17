# Copyright 2023-2025  Dom Sekotill <dom.sekotill@kodo.org.uk>

"""
Authentication handlers for adding auth data to requests and implementing auth flows

The two entrypoints for all concrete authentication handler classes are
`AuthHandler.prepare_request()` for up-front modifications to requests and pre-request
authentication flows, and `AuthHandler.process_response()` for post-request authentication
flows.
"""

from codecs import encode
from http import HTTPStatus
from typing import Protocol

from .exceptions import UnauthorizedError
from .request import CurlRequest
from .request import encode_header
from .response import Response


class AuthHandler(Protocol):
	"""
	Abstract definition of authentication handlers' entrypoints
	"""

	async def prepare_request(self, request: CurlRequest, /) -> None:
		"""
		Process a request instance before the request is enacted

		This method can be used by handlers to modify requests (such as adding headers or
		adding session cookies); it is a coroutine to allow handlers to inject an auth-flow
		before the request.  Any such flow SHOULD use the request's session.
		"""
		...

	async def process_response(self, request: CurlRequest, response: Response, /) -> Response:
		"""
		Examine a response to a request and perform any follow-up actions

		This method may return the passed response if the request was authenticated and no
		further actions need to be taken; or further requests can be made if necessary,
		after which a new successful response to an identical request must be returned.
		"""
		...


class BasicAuth:
	"""
	Provide user authentication credentials with requests

	Instances must be registered to authenticate a user for an endpoint using
	`konnect.http.Session.add_authentication()`.
	"""

	def __init__(self, username: str, password: str) -> None:
		self.username = username
		self.password = password

	async def prepare_request(self, request: CurlRequest, /) -> None:
		"""
		Insert a basic authentication header into a request
		"""
		val = f"{self.username}:{self.password}".encode()
		val = b"Basic " + encode(val, "base64").strip()
		request.headers.append(encode_header(b"Authorization", val))

	@staticmethod
	async def process_response(_: CurlRequest, response: Response, /) -> Response:
		"""
		Process a response
		"""
		if response.code == HTTPStatus.UNAUTHORIZED:
			raise UnauthorizedError
		return response


class BearerTokenAuth:
	"""
	Provide a client authentication token with requests

	Instances must be registered to authenticate to an endpoint with a token using
	`konnect.http.Session.add_authentication()`.
	"""

	def __init__(self, token: bytes|str) -> None:
		self.token = token.encode("ascii") if isinstance(token, str) else token

	async def prepare_request(self, request: CurlRequest, /) -> None:
		"""
		Insert a bearer token authentication header into a request
		"""
		val = b"Bearer " + self.token
		request.headers.append(encode_header(b"Authorization", val))

	@staticmethod
	async def process_response(_: CurlRequest, response: Response, /) -> Response:
		"""
		Process a response
		"""
		if response.code == HTTPStatus.UNAUTHORIZED:
			raise UnauthorizedError
		return response
