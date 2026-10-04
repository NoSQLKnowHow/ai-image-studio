"""AI Image Studio backend: API process, job queue and GPU worker."""

# The one place the version lives. The page shows it in its title ("AI Image Studio v1.1") and the API
# reports it in /api/health and /api/status. frontend/package.json carries the same number (a test
# checks that they agree). 1.0 was the first build on the Spark; each release since bumps the minor.
__version__ = "1.7"
