# StyleRef nodes for ComfyUI

Define a creative style once on [StyleRef](https://styleref.io) — colors,
lighting, mood, composition, guardrails — then load it as a node and reuse it
across FLUX, SDXL, SD1.5, and natural-language targets.

The style is compiled server-side for whichever model you point it at, so the
same style produces prompt text phrased appropriately for each one.

**No account needed** to load and apply public gallery styles. An account adds
your own private styles. **Nothing in this plugin spends credits.**

---

## Install

**ComfyUI Manager** (recommended) — search for **StyleRef** and click Install.

**Manually:**

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/StyleRef/styleref-comfyui
```

Restart ComfyUI. No dependencies to install — the pack uses only the Python
standard library. Look for `[StyleRef] v0.1.0 — 5 nodes registered` in the
server console at startup.

## 60-second quickstart

1. Add **StyleRef Load** and paste a slug from the
   [gallery](https://styleref.io/gallery) into `style_ref` (try
   `sbdlwwly-66ae1a89efdc`, the Renaissance Mythic Classicism style) — or
   click **Search styles…** to pick one.
2. Add **StyleRef Apply**, connect `style`, type a subject, pick your target.
3. Wire `positive` / `negative` into your CLIP Text Encode nodes.

Or drag [`workflows/01-quickstart.json`](workflows/01-quickstart.json) onto the
canvas.

## Nodes

| Node | Sign-in | What it does |
| --- | --- | --- |
| **StyleRef Load** | No | Fetch a style by share slug or share URL — and, signed in, your own private styles by id or `/styles/{id}` URL. Includes gallery search with a picker dialog and a ↻ Refresh button. |
| **StyleRef Apply** | No | Compose a subject with a style into `positive` / `negative` prompts for a chosen target, with a prompt preview in the node body. |
| **StyleRef Facets** | No | Every schema section as its own output — exactly the style board's section list, plus the six custom style items. |
| **StyleRef Reference Images** | No | The style's inspiration images as an IMAGE batch — for IPAdapter and reference conditioning. |
| **StyleRef Login** | — | Status line + Sign in / Sign out buttons. Needed only to load your own private styles. |

### Targets

`ai_tools`, `style_md`, `flux`, `midjourney`, `diffusion`, `json` — exactly the
web app's copy-box format list. For the sampler in your graph use `flux` (FLUX
models) or `diffusion` (SDXL, SD1.5, DALL·E-class); the other four are for
copying the output out of ComfyUI, and the node warns if one feeds a CLIP
encoder.

### Making your own styles

Extraction (turning an image into a style) happens on
[styleref.io](https://styleref.io) — upload an image there, refine the result,
and it lands in your library. Load it here by its share slug or id
(sign in with the Login node first).

## Workflow templates

| File | Sign-in | Story |
| --- | --- | --- |
| [`01-quickstart.json`](workflows/01-quickstart.json) | No | Load → Apply → generate |
| [`02-consistency-grid.json`](workflows/02-consistency-grid.json) | No | One style, three subjects, side by side |
| [`03-your-own-style.json`](workflows/03-your-own-style.json) | Yes | Extract on the web, load your own style here |
| [`04-facets.json`](workflows/04-facets.json) | No | Every section of the style as its own output, next to a normal render |

A parallel set wired for **Z-Image Turbo** (an S3-DiT model — UNET + Qwen text
encoder + VAE, compiled from the `diffusion` target) lives in
[`workflows/z-image/`](workflows/z-image): the same quickstart, consistency,
your-own-style and facets stories, plus a reference-images demo. The three
model files download on first run.

**Z-Image takes no negative prompt.** It runs without classifier-free guidance,
so the Z-Image templates carry no negative encoder — the sampler's negative
input gets a zeroed conditioning, as in a FLUX graph. StyleRef still compiles
the negative and previews it on the canvas; fold anything you need from it into
`subject` on Apply as a positive phrase. Raising CFG does not bring it back.

## Signing in

Add the **StyleRef Login** node and click **Sign in (browser)** — no queueing
needed. Your browser opens, you approve, and the node's status flips to signed
in. The token is stored at `~/.config/styleref/credentials.json` with `0600`
permissions. The [`styleref` CLI](https://www.npmjs.com/package/styleref)
shares this login.

(No frontend at all? `npx styleref login` signs in the same credentials file,
and queueing the Login node reports the current status.)

### Running ComfyUI on a remote or headless machine

Browser sign-in **cannot** work on a rented GPU box, RunPod, or any headless
server: there is no browser to open, and `localhost` is the wrong machine. Use a
token instead:

1. Copy a token from your [account page](https://styleref.io/account).
2. Where ComfyUI runs, set `STYLEREF_TOKEN=<token>`
3. Restart ComfyUI.

`STYLEREF_TOKEN` always takes precedence over stored credentials. The Login node
detects the headless case and prints these instructions rather than hanging.

## Credits

**Nothing in this plugin spends credits.** Loading, searching, and applying
styles is free and unlimited within the public rate limit. Extraction — the one
paid action — happens on [styleref.io](https://styleref.io), where you can
preview and refine the result before it costs anything beyond the extraction
itself. See [plans and limits](https://docs.styleref.io/using-styleref/plans-and-limits).

## Configuration

| Variable | Purpose |
| --- | --- |
| `STYLEREF_TOKEN` | Bearer token; overrides stored credentials. The headless path. |
| `STYLEREF_API` | API base URL. Defaults to `https://styleref.io/api/v1`. |
| `STYLEREF_FORCE_BROWSER_LOGIN` | `1` forces browser login, `0` forces the token path. |

## Development

```bash
pip install pytest ruff pillow numpy   # pillow/numpy are test-only deps
pytest tests/ -q
ruff check .
python scripts/build_workflows.py   # regenerate workflow templates
```

The workflow templates are generated — edit
[`scripts/build_workflows.py`](scripts/build_workflows.py), not the JSON.

The nodes are a thin client over StyleRef's public
[REST API v1](https://docs.styleref.io/for-ai-agents/rest-api/overview-and-authentication)
([OpenAPI](https://styleref.io/api/v1/openapi.json)). All prompt compilation
happens server-side — this pack never writes style prose itself, so improvements
to StyleRef's compiler reach you without a plugin update.

## Docs & support

- [ComfyUI connector docs](https://docs.styleref.io/connectors/comfyui/install-and-quickstart)
- [Issues](https://github.com/StyleRef/styleref-comfyui/issues)

## License

MIT — see [LICENSE](LICENSE).
