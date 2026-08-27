# Publishing MemoCat MCP

## Release prerequisites

1. Confirm access to the existing
   [`memocat-mcp` PyPI project](https://pypi.org/project/memocat-mcp/).
2. Confirm a published Montycat Python SDK release satisfies
   `montycat>=1.2.2,<2`.
   It must contain typed governance, scoped/hybrid semantic methods,
   query/write vectors, external-vector enrollment, semantic status,
   re-embedding, and subscription cleanup behavior used here.
3. Confirm `https://github.com/MontyGovernance/memocat-mcp` is public and
   that its README, issues, and changelog URLs resolve.
4. Configure PyPI Trusted Publishing for the release workflow or create a
   short-lived scoped API token. Do not store a token in the repository.

## Build and validate

```bash
git status --short
rm -rf dist
uv run --with build --with twine python -m build
uv run --with twine twine check dist/*
```

Run the complete test suite against Montycat Semantic:

```bash
uv run --extra test pytest -q
```

Test the wheel in a clean environment:

```bash
release_dir="$(mktemp -d)"
python3 -m venv "$release_dir/venv"
"$release_dir/venv/bin/pip" install dist/memocat_mcp-0.4.1-py3-none-any.whl
"$release_dir/venv/bin/python" -c \
  'import memocat_mcp; assert memocat_mcp.__version__ == "0.4.1"'
```

Inspect package contents before uploading:

```bash
unzip -l dist/memocat_mcp-0.4.1-py3-none-any.whl
tar -tzf dist/memocat_mcp-0.4.1.tar.gz
```

## TestPyPI

```bash
uv run --with twine twine upload --repository testpypi dist/*
uvx --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ memocat-mcp==0.4.1
```

Verify the rendered description, project links, classifiers, and install
command on TestPyPI. Then perform an MCP handshake against a live Semantic
engine >=1.3.0, including query/write vectors, external-vector enrollment,
semantic status, and re-embedding.

## PyPI

Tag the exact tested commit:

```bash
git tag -s v0.4.1 -m "MemoCat MCP 0.4.1"
git push origin v0.4.1
```

Upload through Trusted Publishing. If a manual token is required:

```bash
uv run --with twine twine upload dist/*
```

After publishing, verify:

```bash
uvx memocat-mcp==0.4.1
```

Then add the PyPI URL to the MCP registry submission, Docker image labels,
website server card, agent-skills index, and directory listings.
