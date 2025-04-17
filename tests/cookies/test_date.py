# Copyright 2023, 2025  Dom Sekotill <dom.sekotill@kodo.org.uk>

from datetime import UTC
from datetime import datetime
from unittest import TestCase

from konnect.http.cookies.dates import dateparse


class Tests(TestCase):
	"""
	Tests for the `dateparse` function
	"""

	def test_working(self) -> None:
		"""
		Check that the function parses various acceptable strings
		"""
		testvals = [
			"Thu Jan  1 12:34:56 1970",
			"January 1st 70 12:34:56",
			"12:34:56 1970 Jan 1st",
			"1-jan-1970/12:34:56",
			"Thu, 1 Jan 1970 12:34:56 GMT", # Strictly correct according to Moz docs
		]

		for string in testvals:
			with self.subTest(string=string):
				assert dateparse(string) == datetime(1970, 1, 1, 12, 34, 56, tzinfo=UTC)

		string = "Jan 1 30 12:34:56"
		with self.subTest(string=string):
			assert dateparse(string) == datetime(2030, 1, 1, 12, 34, 56, tzinfo=UTC)

	def test_working_months(self) -> None:
		"""
		Check that the function parses all months
		"""
		testvals = [
			("jan", "Jan", "JAN", "january"),
			("feb", "Feb", "FEB", "february"),
			("mar", "Mar", "MAR", "march"),
			("apr", "Apr", "APR", "april"),
			("may", "May", "MAY", "may"),
			("jun", "Jun", "JUN", "june"),
			("jul", "Jul", "JUL", "july"),
			("aug", "Aug", "AUG", "august"),
			("sep", "Sep", "SEP", "september"),
			("oct", "Oct", "OCT", "october"),
			("nov", "Nov", "NOV", "november"),
			("dec", "Dec", "DEC", "december"),
		]
		for number, names in enumerate(testvals):
			for name in names:
				with self.subTest(name=name):
					date = dateparse(f"1 {name} 1970 12:34:56")
					assert date == datetime(1970, number + 1, 1, 12, 34, 56, tzinfo=UTC)

	def test_incomplete(self) -> None:
		"""
		Check that strings missing all the required parts fail correctly
		"""
		testvals = [
			"Thursday 1st January 1970",
			"12:34:56",
			"1 JAN 12:34:56",
			"1st Ianuary 1970 12:34:56",  # No month due to misspelling
		]

		for string in testvals:
			with self.subTest(string=string):
				with self.assertRaises(ValueError) as cm:
					dateparse(string)
				assert "Incomplete" in str(cm.exception)

	def test_repeat_fields(self) -> None:
		"""
		Check that fields that look like repetitions raise ValueError
		"""
		testvals = [
			"Thu Jan  1 12:34:56 1970 FEB",
			"January 1st 70 12:34:56 1970",
			"12:34:56 1970 Jan 1st 02",
		]

		for string in testvals:
			with self.subTest(string=string):
				with self.assertRaises(ValueError) as cm:
					dateparse(string)
				assert "Unexpected" in str(cm.exception)

	def test_too_old(self) -> None:
		"""
		Check that dates before 1601 raise ValueError
		"""
		with self.assertRaises(ValueError) as cm:
			dateparse("January 1st 1599 12:34:56")
		assert "1601" in str(cm.exception)

	def test_invalid(self) -> None:
		"""
		Check that invalid day, hour, minute or second values raise ValueError
		"""
		testvals = [
			"24:34:56 1st Jan 1970",
			"12:60:56 1st Jan 1970",
			"12:34:60 1st Jan 1970",
			"12:34:56 32nd Jan 1970",
		]

		for string in testvals:
			with self.subTest(string=string):
				with self.assertRaises(ValueError) as cm:
					dateparse(string)
				assert "Invalid" in str(cm.exception)
