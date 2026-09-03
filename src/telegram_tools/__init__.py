"""Local Telethon CLI tools."""

__all__ = ["__version__"]

# The version the envelope, the plan id and the audit line all carry, so it has
# to be the version that shipped: tests/test_version.py keeps it and
# pyproject.toml equal.
__version__ = "3.8.0"
