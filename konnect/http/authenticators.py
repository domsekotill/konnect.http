# Copyright 2023  Dom Sekotill <dom.sekotill@kodo.org.uk>

"""
Authentication handlers for adding auth data to requests and implementing auth flows

The two entrypoints for all concrete authentication handler classes are
`AuthHandler.prepare_request()` for up-front modifications to requests and pre-request
authentication flows, and `AuthHandler.process_response()` for post-request authentication
flows.
"""

from typing import Protocol

from .request import CurlRequest
from .response import Response


class AuthHandler(Protocol):
	"""
	Abstract definition of authentication handlers' entrypoints
	"""

	async def prepare_request(self, request: CurlRequest) -> None:
		"""
		Process a request instance before the request is enacted

		This method can be used by handlers to modify requests (such as adding headers or
		adding session cookies); it is a coroutine to allow handlers to inject an auth-flow
		before the request.  Any such flow SHOULD use the request's session.
		"""

	async def process_response(self, request: CurlRequest, response: Response) -> Response:
		"""
		Examine a response to a request and perform any follow-up actions

		This method may return the passed response if the request was authenticated and no
		further actions need to be taken; or further requests can be made if necessary,
		after which a new successful response to an identical request must be returned.
		"""
