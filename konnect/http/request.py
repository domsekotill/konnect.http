# Copyright 2023  Dom Sekotill <dom.sekotill@kodo.org.uk>

"""
Module providing a `konnect.curl.Request` implementation for HTTP requests

Using the `Request` class directly allows for finer-grained control of a request, including
asynchronously sending chunked data.

For many uses, there is a simple interface supplied by the `Session` class which does not
require users to interact directly with the classes supplied in this module.
"""

from __future__ import annotations

from enum import Enum
from enum import auto
from typing import TYPE_CHECKING

from konnect.curl import MILLISECONDS
from pycurl import *

from .response import ReadStream
from .response import Response

if TYPE_CHECKING:
	from .session import Session


__all__ = [
	"Method",
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


class Phase(Enum):

	INITIAL = auto()
	HEADERS = auto()
	BODY_START = auto()
	BODY_CHUNKS = auto()
	TRAILERS = auto()


class Request:
	"""
	This class provides the user-callable API for requests
	"""

	def __init__(self, session: Session, method: Method, url: str):
		self._request = CurlRequest(session, method, url)

	async def write(self, data: bytes, /) -> None:
		"""
		Write data to an upload request

		Signal an EOF by writing b""
		"""
		await self._request.write(data)

	async def get_response(self) -> Response:
		"""
		Progress the request far enough to create a `Response` object and return it
		"""
		return await self._request.get_response()


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

	def has_response(self) -> bool:
		"""
		Return whether calling `response()` will return a value or raise `LookupError`

		This is part of the `konnect.curl.Request` interface.
		"""
		match self._phase:
			case Phase.BODY_START:
				assert self._response is not None
				return self._response.response >= 200
			case Phase.BODY_CHUNKS:
				return self._data != b""
		return False

	def response(self) -> Response|bytes:
		"""
		Return a waiting response or raise `LookupError` if there is none

		See `has_response()` for checking for waiting responses.

		This is part of the `konnect.curl.Request` interface.
		"""
		if self._phase == Phase.BODY_START:
			self._phase = Phase.BODY_CHUNKS
			assert self._response is not None
			if self._response.response < 200:
				raise LookupError
			return self._response
		if self._phase != Phase.BODY_CHUNKS or not self._data:
			raise LookupError
		data, self._data = self._data, b""
		return data

	def completed(self) -> bytes:
		"""
		Complete the transfer by returning the final stream bytes

		This is part of the `konnect.curl.Request` interface.
		"""
		assert self._phase == Phase.BODY_CHUNKS
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
			self._phase = Phase.HEADERS
			stream = ReadStream(self)
			self._response = Response(data.decode("ascii"), stream)
			return
		assert self._response is not None
		if data == b"\r\n":
			self._phase = Phase.BODY_START
			return
		if self._phase not in (Phase.HEADERS, Phase.TRAILERS):
			self._phase = Phase.TRAILERS
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
		resp = await self.session.multi.process(self)
		assert isinstance(resp, Response)
		return resp

	async def get_data(self) -> bytes:
		"""
		Return chunks of received data from the body of the response to the request
		"""
		if self._phase != Phase.BODY_CHUNKS:
			raise RuntimeError("get_data() can only be called after get_response()")
		data = await self.session.multi.process(self)
		assert isinstance(data, bytes), repr(data)
		return data