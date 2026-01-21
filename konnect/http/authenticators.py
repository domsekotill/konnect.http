# Copyright 2023-2026  Dom Sekotill <dom.sekotill@kodo.org.uk>

"""
Authentication handlers for adding auth data to requests and implementing auth flows

Each handler implements `konnect.http.request.Hook`.
"""

from codecs import encode
from http import HTTPStatus

from konnect.curl.certificates import CertificateSource
from konnect.curl.certificates import CommonEncodedSource
from konnect.curl.certificates import PrivateKeySource

from .exceptions import UnauthorizedError
from .request import Request
from .request import ResponseT
from .request import encode_header


class BasicAuth:
	"""
	Provide user authentication credentials with requests

	Instances must be registered to authenticate a user for an endpoint using
	`konnect.http.Session.add_authentication()`.
	"""

	def __init__(self, username: str, password: str) -> None:
		self.username = username
		self.password = password

	async def prepare_request(self, request: Request, /) -> None:
		"""
		Insert a basic authentication header into a request
		"""
		val = f"{self.username}:{self.password}".encode()
		val = b"Basic " + encode(val, "base64").strip()
		request.headers.append(encode_header(b"Authorization", val))

	@staticmethod
	async def process_response(_: Request[ResponseT], response: ResponseT, /) -> ResponseT:
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

	async def prepare_request(self, request: Request, /) -> None:
		"""
		Insert a bearer token authentication header into a request
		"""
		val = b"Bearer " + self.token
		request.headers.append(encode_header(b"Authorization", val))

	@staticmethod
	async def process_response(_: Request[ResponseT], response: ResponseT, /) -> ResponseT:
		"""
		Process a response
		"""
		if response.code == HTTPStatus.UNAUTHORIZED:
			raise UnauthorizedError
		return response


class ClientCertificateAuth:
	"""
	Provide an X.509 certificate when negotiating TLS connections

	Instances must be registered to authenticate to an endpoint with a token using
	`konnect.http.Session.add_authentication()`.
	"""

	def __init__(
		self,
		certificate: CommonEncodedSource | tuple[CertificateSource, PrivateKeySource],
	) -> None:
		self.certificate = certificate

	async def prepare_request(self, request: Request, /) -> None:
		"""
		Add a client certificate to a request
		"""
		request.certificate = self.certificate

	@staticmethod
	async def process_response(_: Request[ResponseT], response: ResponseT, /) -> ResponseT:
		"""
		Process a response
		"""
		if response.code == HTTPStatus.UNAUTHORIZED:
			raise UnauthorizedError
		return response
