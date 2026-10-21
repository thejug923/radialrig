# Tidepool

A small command-line tool that syncs a team's shared bookmark list to a
static status page. `tidepool sync` pulls the list, writes the page and
uploads it; `tidepool status` prints the last run. A nightly scheduled
run does the sync unattended.

Run the tests with `uv run pytest -q`. Deploy to staging with
`python -m tidepool.deploy --target staging`.
