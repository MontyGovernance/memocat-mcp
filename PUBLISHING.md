# Publishing MemoCat MCP

## First-release prerequisites

1. Confirm the `memocat-mcp` project name is available on PyPI.
2. Publish a Montycat Python SDK release satisfying `montycat>=1.0.8,<2`.
   It must contain the typed governance API, scoped semantic methods, hybrid
   `_where` search methods, and subscription cleanup behavior used here.
   PyPI's 1.0.7 is insufficient: it does not export the policy enums or provide
   the policy methods and keyspace-scoped semantic signatures.
3. Confirm `https://github.com/MontyGovernance/meow_memory_mcp` is public and
   that its README, issues, and changelog URLs resolve.
4. Configure PyPI Trusted Publishing for the release workflow or create a
   short-lived scoped API token. Do not store a token in the repository.

## Build and validate

```bash
git status --short
uv run --with build --with twine python -m build
uv run --with twine twine check dist/*
```

Run the complete test suite against Montycat Semantic:

```bash
.venv/bin/pytest -q
```

Test the wheel in a clean environment:

```bash
release_dir="$(mktemp -d)"
python3 -m venv "$release_dir/venv"
"$release_dir/venv/bin/pip" install dist/memocat_mcp-0.4.0-py3-none-any.whl
"$release_dir/venv/bin/python" -c \
  'import memocat_mcp; assert memocat_mcp.__version__ == "0.4.0"'
```

Inspect package contents before uploading:

```bash
unzip -l dist/memocat_mcp-0.4.0-py3-none-any.whl
tar -tzf dist/memocat_mcp-0.4.0.tar.gz
```

## TestPyPI

```bash
uv run --with twine twine upload --repository testpypi dist/*
uvx --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ memocat-mcp==0.4.0
```

Verify the rendered description, project links, classifiers, and install
command on TestPyPI. Then perform an MCP handshake against a live Semantic
engine.

## PyPI

Tag the exact tested commit:

```bash
git tag -s v0.4.0 -m "MemoCat MCP 0.4.0"
git push origin v0.4.0
```

Upload through Trusted Publishing. If a manual token is required:

```bash
uv run --with twine twine upload dist/*
```

After publishing, verify:

```bash
uvx memocat-mcp==0.4.0
```

Then add the PyPI URL to the MCP registry submission, Docker image labels,
website server card, agent-skills index, and directory listings.
