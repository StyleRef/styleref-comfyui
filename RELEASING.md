# Releasing an update

For an agent or contributor picking this up cold: this is the repeatable flow
for shipping a change to users — a node fix, a new workflow template, updated
artwork, anything. It is distinct from [the one-time steps](../../internal-docs/connectors-repo-graduation.md)
that stood this repo up in the first place (those live in the private
monorepo and don't apply again).

If this repo reached you as a git submodule inside `StyleRef-Webapp`, work
**here**, in the submodule — not by editing a plain copy of these files
somewhere else. A commit made outside this repo publishes nothing.

## 1. Make the change

Edit the source. If you touched a workflow's *shape* (nodes, links, layout,
subjects, the style it loads) rather than hand-editing JSON, regenerate:

```bash
python scripts/build_workflows.py
```

The committed `workflows/*.json` files must match the generator's output —
CI fails the build otherwise, and a hand-edit to the JSON will just be
overwritten the next time someone runs the script.

## 2. Bump the version — four places, together

There is no tooling for this step; it is easy to update three and miss the
fourth, so check all four:

| File | What to change |
| --- | --- |
| `pyproject.toml` | `version = "x.y.z"` |
| `__init__.py` | `_FALLBACK_VERSION = "x.y.z"` |
| `styleref_api.py` | `USER_AGENT = "styleref-comfyui/x.y.z (...)"` |
| `README.md` | The `[StyleRef] vx.y.z — 5 nodes registered` line in **Install** |

`tests/test_api.py::test_version_strings_agree_with_pyproject` fails if the
first three disagree — trust it, but it can't see the README line, so check
that one by eye.

Use semver. The publish workflow (`.github/workflows/publish.yml`) rejects a
tag that doesn't exactly match `pyproject.toml`'s version.

## 3. Verify locally

```bash
ruff check .
pytest tests/ -q
```

If the change touches `web/styleref.js`, at minimum run `node --check
web/styleref.js` — there is no JS test harness, so a syntax check is the only
automated signal before it reaches a real ComfyUI frontend.

## 4. Commit and push, in this repo

```bash
git add -A
git commit -m "..."
git push origin main
```

## 5. Tag the commit you just pushed

```bash
git tag -a vX.Y.Z -m "..."
git push origin vX.Y.Z
```

The tag push fires **Publish to Comfy Registry**
(`.github/workflows/publish.yml`): it re-checks the tag against
`pyproject.toml`, lints, runs the test suite, then calls
`Comfy-Org/publish-node-action` with the `REGISTRY_ACCESS_TOKEN` secret.
Watch it in the Actions tab or with `gh run watch`.

**A branch-protection ruleset on `main` requires 3 status checks** that a
direct push can't have satisfied yet (see the [public-repo branch ruleset
convention](../../internal-docs/connectors-repo-graduation.md)). The push
above will report "Bypassed rule violations" — that's expected, not an
error, as long as the account pushing is on the bypass list.

## 6. Bump the pointer in the monorepo

A tag and a Registry publish are invisible to `StyleRef-Webapp` until its
submodule pointer moves. Back in the monorepo root:

```bash
git add connectors/styleref-comfyui
git commit -m "chore(comfyui): bump connector to vX.Y.Z (<why>)"
git push origin main
```

Skipping this step is the easy mistake — nothing errors, the monorepo just
quietly keeps pointing at the old commit. `git status` in the monorepo shows
a moved submodule as a one-line change; `git diff --submodule` shows which
commits moved.

## 7. Confirm the release actually landed — don't trust a green check alone

A successful publish workflow proves the *build* succeeded, not that the
*Registry* did the right thing with it, and not that ComfyUI reads what you
shipped the way you assumed. Check the real state:

```bash
# Per-version status (Pending → Active is normal and can take a while;
# Flagged sometimes clears on its own — see the note below)
curl -s https://api.comfy.org/nodes/styleref-comfyui/versions | python3 -m json.tool

# Node-level: which version is actually live for install
curl -s https://api.comfy.org/nodes/styleref-comfyui | python3 -m json.tool
```

If the change affects what's inside the shipped package (thumbnails, a new
file, `.comfyignore`), download and inspect the **actual published archive**
rather than trusting what you intended to ship:

```bash
curl -sL "https://cdn.comfy.org/styleref/styleref-comfyui/X.Y.Z/node.zip" -o node.zip
unzip -l node.zip
```

This caught two real bugs in past releases: model-download metadata that was
declared in the wrong JSON location (present in the repo, silently ignored
by ComfyUI), and template thumbnails shipped as `.webp` when custom node
packs are only read as `.jpg` — both looked correct in CI and in the archive
listing, and only showing up wrong in a running ComfyUI proved it.

## On `NodeVersionStatusFlagged`

Comfy's scanner has flagged every version of this pack at some point,
including versions identical to ones that were flagged and later cleared on
their own — this looks like a transient state in their pipeline, not a
verdict on the code. Don't rush to change anything or re-publish in response
to a flag by itself. Give it time and re-check; if it's still flagged after
a day or two with no `NodeVersionStatusActive` version anywhere, that's when
it's worth asking in the Comfy Discord (icon in the registry.comfy.org
header) rather than guessing at a code change.

## If the ComfyUI-Manager listing needs updating too

`custom-node-list.json` in
[`Comfy-Org/ComfyUI-Manager`](https://github.com/Comfy-Org/ComfyUI-Manager)
is a **second, separate** listing from the Comfy Registry — Manager's
"default channel," predating the Registry and not auto-synced from it. A
Registry publish does not update it. If this pack's entry there
(`id: "styleref-comfyui"`) ever needs a description or metadata change, that
is its own PR against that repo, not something this release flow touches.
