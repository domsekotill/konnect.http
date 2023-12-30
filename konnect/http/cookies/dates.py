# Copyright 2023  Dom Sekotill <dom.sekotill@kodo.org.uk>

"""
Parsing of timestamps used for the "expires" attribute of "Set-Cookie:" headers

For historical reasons, although the current standards prefer RFC822#section-5 dates
(updated by RFC1123#section-5.2.14) as stated in RFC2616#section-3.3.1, user-agents must be
able to parse a wide variety of formats.  This is module provides an implementation of the
algorithm described in RFC6265#section-5.1.1
"""

import re
from datetime import datetime as DateTime
from typing import TypedDict

__all__ = ["dateparse"]

MONTHS = [
	"jan", "feb", "mar", "apr", "may", "jun",
	"jul", "aug", "sep", "oct", "nov", "dec",
]

DATETIME_RE = re.compile(
	r"""(?ix)
	(?:^|[\t\x20-\x2f\x3b-\x40\x5b-\x60\x7b-\x7e]+)  # Start or leading delimiter chars
	(?P<match>
		(?P<month>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec) |
		(?P<hour>[0-9]{1,2}):(?P<min>[0-9]{1,2}):(?P<sec>[0-9]{1,2}) (?=[^0-9]*) |
		(?P<number>[0-9]{1,4}) (?=[^0-9]*)
	)
	""",
)


class DateTimeArgs(TypedDict):
	"""
	A dictionary of keyword arguments to pass to `DateTime`
	"""

	year: int
	month: int
	day: int
	hour: int
	minute: int
	second: int


def dateparse(date: str) -> DateTime:
	"""
	Parse a "Set-Cookie:" expiration date string
	"""
	vals = DateTimeArgs(year=0, month=0, day=0, hour=0, minute=0, second=0)
	found_time = found_day = found_month = found_year = False
	for match in DATETIME_RE.finditer(date):
		match match.groupdict():
			case {"hour": str(sh), "min": str(sm), "sec": str(ss)} if not found_time:
				if not (
					(0 <= (hour := int(sh)) < 24) and
					(0 <= (minute := int(sm)) < 60) and
					(0 <= (second := int(ss)) < 60)
				):
					raise ValueError(f"Invalid time: {sh}:{sm}:{ss}")
				found_time = True
				vals["hour"] = hour
				vals["minute"] = minute
				vals["second"] = second
			case {"number": str(sd)} if not found_day and 1 <= len(sd) <= 2:
				if not (0 < (day := int(sd)) <= 31):
					raise ValueError(f"Invalid day of month: {sd}")
				found_day = True
				vals["day"] = day
			case {"month": str(month)} if not found_month:
				found_month = True
				vals["month"] = 1 + MONTHS.index(month.lower())
			case {"number": str(sy)} if not found_year and 2 <= len(sy) <= 4:
				found_year = True
				year = int(sy)
				if 0 <= year < 70:
					year += 2000
				elif 70 <= year < 100:
					year += 1900
				elif year < 1601:
					raise ValueError("Dates before year 1601 are invalid")
				vals["year"] = year
			case _:
				raise ValueError(f"Unexpected value: {match.group('match')}")
	if not (found_time and found_day and found_month and found_year):
		raise ValueError(f"Incomplete date-time: {date} ({vals})")
	return DateTime(**vals)
