# Copyright 2023, 2025  Dom Sekotill <dom.sekotill@kodo.org.uk>

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from unittest import TestCase

from konnect.http.cookies.set_cookie_parser import TokenType
from konnect.http.cookies.set_cookie_parser import tokenise

from ..checks import check_exc

GOOD_VALS = [
	(
		b"Set-Cookie: cookie=choc,choc-chip",
		{TokenType.NAME: "cookie", TokenType.VALUE: b"choc,choc-chip"},
	),

	(
		b"Set-Cookie: cookie=choc; domain=example.com; path=/",
		{TokenType.NAME: "cookie", TokenType.VALUE: b"choc", TokenType.DOMAIN: "example.com", TokenType.PATH: "/"},
	),

	(
		b"Set-Cookie: ssid=1; expires=Thu, Jan 1 1970 12:34:56",
		{TokenType.NAME: "ssid", TokenType.VALUE: b"1", TokenType.EXPIRES: datetime(1970, 1, 1, 12, 34, 56, tzinfo=UTC)},
	),

	(
		b"Set-Cookie: ssid=1; max-age=1000",
		{TokenType.NAME: "ssid", TokenType.VALUE: b"1", TokenType.MAX_AGE: timedelta(seconds=1000)},
	),

	(
		b"Set-Cookie:ssid=1 ;domain=example.com;path=/  ",
		{TokenType.NAME: "ssid", TokenType.VALUE: b"1", TokenType.DOMAIN: "example.com", TokenType.PATH: "/"},
	),

	(
		b"Set-Cookie: ssid=1 ; secure; httpOnly",
		{TokenType.NAME: "ssid", TokenType.VALUE: b"1", TokenType.SECURE: True, TokenType.HTTP_ONLY: True},
	),

	(
		b"Set-Cookie: ssid=1; secure=; httpOnly=false",
		{TokenType.NAME: "ssid", TokenType.VALUE: b"1", TokenType.SECURE: True, TokenType.HTTP_ONLY: True},
	),

	(
		b"Set-Cookie: ssid=1; spam=ham",
		{TokenType.NAME: "ssid", TokenType.VALUE: b"1"},
	),

	(
		b"Set-Cookie: ssid=1; eggs",
		{TokenType.NAME: "ssid", TokenType.VALUE: b"1"},
	),

	(
		b"Set-Cookie: ssid=1; domain=.example.com",
		{TokenType.NAME: "ssid", TokenType.VALUE: b"1", TokenType.DOMAIN: "example.com"},
	),

	(
		b"Set-Cookie: ssid=1; domain=EXAMPLE.COM",
		{TokenType.NAME: "ssid", TokenType.VALUE: b"1", TokenType.DOMAIN: "example.com"},
	),

	(
		b"Set-Cookie: ssid=1; domain=example.com.",
		{TokenType.NAME: "ssid", TokenType.VALUE: b"1"},
	),

	(
		b"Set-Cookie: ssid=1; path=spam/ham",
		{TokenType.NAME: "ssid", TokenType.VALUE: b"1"},
	),
]

BAD_VALS = [
	(
		b"Host: example.com",
		"not a Set-Cookie header",
	),

	(
		b"Set-Cookie: secure",
		"cookies require a value",
	),

	(
		b"Set-Cookie: secure; path=/",
		"cookies require a value",
	),

	(
		b"Set-Cookie: =spam",
		"cookies require a name",
	),

	(
		b"Set-Cookie: =spam; path=/",
		"cookies require a name",
	),

	(
		b"Set-Cookie: food=spam; path",
		"attribute [...] requires a value",
	),
]


class Tests(TestCase):
	"""
	Tests for the `parse` function
	"""

	def test_good(self) -> None:
		"""
		Check that acceptable (not necessarily strictly correct) Set-Cookie headers parse
		"""
		for header, expect in GOOD_VALS:
			expect.setdefault(TokenType.SECURE, False)
			expect.setdefault(TokenType.HTTP_ONLY, False)
			with self.subTest(header=header):
				self.assertDictEqual(dict(tokenise(header)), expect)

	def test_bad(self) -> None:
		"""
		Check that unacceptable Set-Cookie headers raise ValueError
		"""
		for header, mesg in BAD_VALS:
			with self.subTest(message=mesg):
				with self.assertRaises(ValueError) as cm:
					[*tokenise(header)]

				check_exc(cm.exception, mesg)
