# pacts/

Generated consumer contracts land here (`MTP_PACT_DIR`). They are build output — `*.json` is
gitignored — and are regenerated on every `pytest -m pact_consumer` run. Share them through the Pact
Broker (`make pact-publish`) or the CI artifact, not through git.
