# ZKT-access-flask

Flask bridge between the Laravel `Elevator` app (`../Elevator`) and ZKTeco access controllers. See `README.md` for the API, the request flow and how to troubleshoot `-307`. The workspace contract is in `../AGENTS.md`.

## Layout

- `app.py`: routes, Bearer auth (`ZKTECO_SHARED_SECRET`, compared with `hmac.compare_digest`), request validation.
- `queue_manager.py`: thin wrappers that run each `main.py` operation under the device lock.
- `device_locks.py`: one `RLock` per `ip:port`, plus `output_lock` for `output.txt`.
- `main.py`: the real controller work through `pyzkaccess` (connstr, model resolution, retries, door masks).
- `observability.py`: Sentry setup from `SENTRY_*` env vars.
- `capture_samples.py`: read-only snapshot of real controller tables. It anonymizes by default; `--no-anonymize` keeps real data, so never commit that output.
- `zkt_main.py`, `wfastcgi.py`: legacy and IIS leftovers, not on the request path.

## Run and test

- Production runs on Windows: `waitress-serve --port=80 --threads=8 app:app` (`run_server.bat`), started by the NSSM service `FlaskAPI` with the global Python 3.9 32-bit (no venv). The PULL SDK DLLs exist only there, so real controller calls do not work on Linux.
- PULL SDK on the server: `C:\Windows\SysWOW64\plcommpro.dll` (248 KB, modified 2023-07-19), loaded by name through pyzkaccess. A rebuilt server needs the SDK's `pl*.dll` files copied there; they are not in git.
- Deploying there: `nssm stop FlaskAPI` before `pip install` (the running service locks compiled `.pyd` files and pip fails halfway), then `nssm start FlaskAPI`.
- Local: `ZKTECO_SHARED_SECRET=change-me flask --app app run --port 5000`
- Dependencies: direct ones are pinned in `requirements.txt`; `requirements-lock.txt` is the full `pip freeze` from production. Change both together and refresh the lock from the server.
- Tests: `.venv/bin/python -m unittest discover -s tests -p 'test_*integration.py'`. The pre-push hook in `.githooks/pre-push` runs the same command (enable it with `git config core.hooksPath .githooks`).
- Tests fake the SDK layer. Before trusting a decoder, run it once against real captured data (see `capture_samples.py`).

## Rules

- Every controller operation must go through the device lock. Two concurrent SDK sessions to one controller corrupt state or fail with `-307`.
- New controller routes require the Bearer check, like the existing ones.
- Changing a route's request or response shape changes the contract with Laravel. Update the request class in `../Elevator/app/Http/Integrations/ZktecoBridge/Requests/` and both test suites together.
- Do not edit `../pyzkaccess`; work around its bugs here.
- Never commit keys, `.rdp` files, passwords or `output.txt`.
