# Claude Desktop extension distribution

**Goal:** one permanent URL for the `.mcpb`, linked from the website, with the
file itself still served by GitHub Releases.

**Decision: link, don't mirror.** The website will not host a copy of the
bundle. Mirroring turns an artifact that `release.yml` publishes automatically
into a manual upload plus a page edit, and that pattern has already failed here
— the download pages sat at engine 1.3.2 while 1.3.4 was live on the CDN.
Provenance also argues against it: the bundle declares
`io.github.montygovernance.montycat-mcp` and `build_mcpb.sh` does not sign it,
so a download origin that matches the manifest identity is worth keeping. If we
ever do self-host, sign the bundle first (`mcpb sign`).

## The problem

`release.yml` attaches version-named assets only:

```
montycat-mcp-1.1.0.mcpb
montycat-mcp-1.1.0.mcpb.sha256
```

So `releases/latest/download/montycat-mcp-1.1.0.mcpb` resolves today (200) and
breaks the moment 1.1.1 ships. There is no name that survives a release, which
is why nothing on the website links the bundle at all — only the repo root.

## Part A — montycat_mcp

1. **Stable-named copies in `release.yml`.** After the MCPB is built and
   checksummed, copy it to `montycat-mcp.mcpb` and generate a matching
   `montycat-mcp.mcpb.sha256` whose body names the stable file (a checksum file
   naming a different path fails `sha256sum -c`). Both go into the uploaded
   artifact and onto the GitHub Release beside the versioned pair, which stays
   for anyone pinning an exact build.
2. **Keep this plan out of the bundle.** `.mcpbignore` names plan files
   individually (`CLAUDE_DIRECTORY_PLAN.md`, `MCPB_DIRECTORY_PLAN.md`), so a new
   `*_PLAN.md` at the root ships inside the `.mcpb` — and the "Reject private or
   build-only files in MCPB" guard then fails the release. Replace the two
   entries with a `*_PLAN.md` wildcard.
3. **README** points Claude Desktop users at the stable URL instead of the
   releases page, so the instruction stops being "find the right asset".

## Part B — montycat_web

4. **A Claude Desktop card on `/download`.** The page routes to Linux, macOS,
   and Windows today; agent memory has no entry, and a Claude Desktop user
   landing on `/ai-memory` currently gets a link to a repo root. The card links
   the stable `.mcpb` URL and names the other install paths (Claude Code plugin,
   `uvx montycat-mcp`) so each client goes to its own route.
5. **`llms.txt`** gains the direct download beside the PyPI entry.

## Sequencing — the one real risk

`releases/latest/download/montycat-mcp.mcpb` is **404 until a release carries
that asset**. Publishing the website link before then ships a dead download.
Two ways to close it, in order of preference:

- Upload the stable-named pair to the existing `v1.1.0` release now
  (`gh release upload v1.1.0 …`), which makes the URL live immediately and
  reversible with `gh release delete-asset`.
- Or hold the website change until the next tag cuts a release through the
  updated workflow.

The workflow change alone does nothing for existing releases — it only takes
effect on the next tag.

## Verification

- `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"`
- Run `build_mcpb.sh`, apply the copy/checksum steps locally, confirm
  `sha256sum -c montycat-mcp.mcpb.sha256` passes against the stable name.
- Confirm `unzip -Z1` on the built bundle contains no `*_PLAN.md`.
- `pnpm run build` in montycat_web; card renders; Prettier clean.
- `curl -I` the stable URL and expect 200 once the asset exists.
