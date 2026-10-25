# Regression checks

- Run backend checks with `python -m unittest discover -s tests -v` and dashboard
  checks with `node --test tests/dashboard.test.cjs`.
- For changes to MCP tools, prompts, validation, ordering, or HTTP transport,
  also run the paired client's v0 E2E gate against this working checkout.
  The client repository is `code-with-foundry-shpe-workshop`, normally a sibling.
- Use the client Python environment to run its `tests/e2e/run.py --suite all`,
  passing this checkout with `--server-repo` and the server Python executable
  with `--server-python`. Its `tests/e2e/README.md` documents setup and artifacts.
- Both the deterministic contract suite and the real Foundry-backed agent
  conversation must pass. If model access is unavailable, run `--suite contract`
  and explicitly report the agent suite as blocked, not passed.
- Never use a hosted workshop endpoint or existing customer/inventory data for
  these tests. The runner creates and stops its own isolated local fixtures.
