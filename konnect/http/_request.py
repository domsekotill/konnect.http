# Copyright 2023  Dom Sekotill <dom.sekotill@kodo.org.uk>

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


class Phase(Enum):

	HEADERS = auto()
	BODY_START = auto()
	BODY_CHUNKS = auto()
	TRAILERS = auto()


class Request:

	def __init__(self, session: Session, url: str):
		self.session = session
		self.url = url
		self._response: Response|None = None
		self._phase = Phase.HEADERS
		self._data = b""

	def configure_handle(self, handle: Curl) -> None:
		"""
		Configure a konnect.curl.Curl handle for this request
		"""
		handle.setopt(URL, self.url)

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
		"""
		match self._phase:
			case Phase.BODY_START:
				return True
			case Phase.BODY_CHUNKS:
				return self._data != b""
		return False

	def response(self) -> Response|bytes:
		"""
		Return a waiting response or raise `LookupError` if there is none

		See `has_response()` for checking for waiting responses.
		"""
		if self._phase == Phase.BODY_START:
			self._phase = Phase.BODY_CHUNKS
			assert self._response is not None
			return self._response
		if self._phase != Phase.BODY_CHUNKS or not self._data:
			raise LookupError
		data, self._data = self._data, b""
		return data

	def completed(self) -> bytes:
		"""
		Complete the transfer by returning the final stream bytes
		"""
		data, self._data = self._data, b""
		return data

	def _process_header(self, data: bytes) -> None:
		if data.startswith(b"HTTP/"):
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
		if self._phase != Phase.HEADERS:
			raise RuntimeError("get_response() can only be called on an unstarted request")
		resp = await self.session.multi.process(self)
		assert isinstance(resp, Response)
		return resp

	async def get_data(self) -> bytes:
		if self._phase != Phase.BODY_CHUNKS:
			raise RuntimeError("get_data() can only be called after get_response()")
		data = await self.session.multi.process(self)
		assert isinstance(data, bytes), repr(data)
		return data
