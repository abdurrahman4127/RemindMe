#!/usr/bin/env python3
"""Run the unit tests and surface individual failures in GitHub annotations."""

from __future__ import annotations

import io
import unittest

import test_remindme


stream = io.StringIO()
result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
    unittest.defaultTestLoader.loadTestsFromModule(test_remindme)
)
print(stream.getvalue(), end="")

for test, details in result.failures + result.errors:
    message = details.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::error title={test.id()}::{message}")

if not result.wasSuccessful():
    raise SystemExit(1)
