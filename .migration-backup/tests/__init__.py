"""Test suite root.

This package marker matters: the suites import shared helpers with
``from tests.conftest import requires_db``. Without it, pytest puts ``tests/``
itself on ``sys.path`` rather than the repository root, and that import fails
under a bare ``pytest`` invocation — ``python -m pytest`` happens to work
because it adds the working directory, which is what masked this locally.
``pythonpath`` in pyproject.toml pins the behaviour regardless of how the
suite is invoked.
"""
