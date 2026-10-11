# tests_sqlserver

Opt-in SQL Server integration tests for the U-535a attachment extraction procedures; not part of the default `tests/` run. Needs a throwaway SQL Server 2022 with database `buildone_test` in `READ_COMMITTED_SNAPSHOT ON` mode.
Set `BUILDONE_TEST_SQLSERVER` to an ODBC string whose `SERVER` is `127.0.0.1` or `localhost`; the module refuses any other host and must never target production.
Run: `./.venv/bin/python -m pytest -o addopts= -p no:cacheprovider -q --rootdir=. tests_sqlserver/`
