/**
 * Progressive enhancement for the StyleRef Load node.
 *
 * Deliberately additive. This file adds:
 *   - a "Search styles…" button opening a picker dialog that owns its own
 *     query input (debounced live search), category filter, Recent/Popular/Saved
 *     source toggle, and thumbnails. Results are split into two independently
 *     paged sections — the user's own library, then the gallery (or their saved
 *     styles) — each 8 rows deep with its own ‹ Prev / Next ›. The query filters
 *     both sections, so the search box narrows the library as well as the
 *     gallery;
 *   - a "↻ Refresh style" button that POSTs /styleref/refresh, which drops the
 *     server-side cache and dirties the node so the next queue re-downloads;
 *   - status + Sign in / Sign out buttons on StyleRef Login, backed by the
 *     /styleref/auth/* routes (the node has no widgets; queueing it reports
 *     status, and `npx styleref login` covers frontend-less installs);
 *   - a warning badge on StyleRef Apply when `target` is a copy-out format
 *     (ai_tools, style_md, midjourney, json) but the `positive` output feeds
 *     a CLIP Text Encode — those are for copying out, not sampler text.
 *
 * It creates no custom widget type, overrides no drawing beyond the badge, and
 * touches no serialization — so if a ComfyUI frontend release changes the
 * extension API and this stops loading, the nodes degrade to exactly what they
 * are without it: text fields you type into. That is the plan's
 * "degraded-but-unbreakable fallback", and it is why the node's own `search` +
 * `category` inputs exist in Python too.
 *
 * Tested against both the LiteGraph frontend and Nodes 2.0.
 */

import { app } from "../../scripts/app.js";

const LOAD_NODE = "StyleRefLoad";
const APPLY_NODE = "StyleRefApply";
const LOGIN_NODE = "StyleRefLogin";

// Targets that are copy-out formats, not sampler text (mirrors Python's
// COPY_OUT_TARGETS — only flux and diffusion belong in a CLIP encoder).
const COPY_OUT_TARGETS = ["ai_tools", "style_md", "midjourney", "json"];

// Mirrors the gallery's controlled vocabulary (kept static like Python's list —
// the server tolerates unknown values).
const CATEGORIES = [
    "Photography", "Graphic design", "Illustration", "Cinematography", "UI/UX",
    "Brand identity", "Motion design", "Concept art", "Game art", "Product design",
    "Architecture", "Interior design", "Fine art", "Character design",
    "Environmental art", "High-end fashion editorial", "Book/editorial design",
    "Packaging design", "3D visualization", "Data visualization", "Copywriting",
    "Content writing", "Social copy", "UX writing",
];

// Rows per section page. Both sections show one page at a time with their own
// ‹ Prev / Next ›, so the dialog stays a fixed height instead of growing.
const PAGE_SIZE = 8;

// Resolved by the availability probe (plan P3-7). `null` = probe still running.
let routesAvailable = null;

async function probeRoutes() {
    // One request at extension setup: if server_routes failed to import, the
    // endpoint 404s and the buttons are simply never added — matching the
    // Python-side degradation instead of shipping a button that alerts.
    try {
        const res = await fetch("/styleref/search?query=&limit=1");
        routesAvailable = res.status !== 404;
    } catch {
        routesAvailable = false;
    }
}

async function fetchJson(url) {
    try {
        const res = await fetch(url);
        if (!res.ok) return { styles: [], error: `Search failed (${res.status})` };
        return await res.json();
    } catch (err) {
        return { styles: [], error: `Search failed: ${err?.message ?? err}` };
    }
}

async function postJson(url, body) {
    try {
        const res = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body ?? {}),
        });
        if (!res.ok) return { ok: false, error: `Request failed (${res.status})` };
        return await res.json();
    } catch (err) {
        return { ok: false, error: `Request failed: ${err?.message ?? err}` };
    }
}

/** DOM helper — textContent only: gallery names/tags are user-authored. */
function el(tag, css, text) {
    const node = document.createElement(tag);
    if (css) node.style.cssText = css;
    if (text != null) node.textContent = text;
    return node;
}

/**
 * The picker dialog (plan P3-3/P3-5): owns its query, filters, and result
 * list. Resolves with the chosen slug/ref, or null when dismissed.
 */
function openPicker(initialQuery) {
    return new Promise((resolve) => {
        const overlay = el(
            "div",
            "position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:10000;" +
                "display:flex;align-items:center;justify-content:center;",
        );
        const panel = el(
            "div",
            "background:#1c1c1f;color:#eee;border-radius:12px;padding:16px;width:560px;" +
                "max-width:92vw;max-height:76vh;overflow:auto;font-family:system-ui,sans-serif;" +
                "box-shadow:0 20px 60px rgba(0,0,0,.5);",
        );

        const close = (value) => {
            document.removeEventListener("keydown", onKey, true);
            overlay.remove();
            resolve(value);
        };
        const onKey = (e) => {
            // Esc-to-close (plan P3-5), capturing so the canvas never sees it.
            if (e.key === "Escape") {
                e.stopPropagation();
                close(null);
            }
        };
        document.addEventListener("keydown", onKey, true);

        panel.append(el("div", "font-weight:600;margin-bottom:12px;font-size:15px;", "Pick a style"));

        // ── controls row: query, category, sort ─────────────────────────────
        const controls = el("div", "display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;");
        const input = el(
            "input",
            "flex:1;min-width:180px;background:#26262a;border:1px solid #3a3a42;color:#eee;" +
                "border-radius:8px;padding:8px 10px;font-size:13px;outline:none;",
        );
        input.placeholder = "Search the gallery… e.g. warm editorial";
        input.value = initialQuery ?? "";

        const category = el(
            "select",
            "background:#26262a;border:1px solid #3a3a42;color:#eee;border-radius:8px;" +
                "padding:8px;font-size:13px;",
        );
        const anyOption = el("option", "", "All categories");
        anyOption.value = "";
        category.append(anyOption);
        for (const name of CATEGORIES) {
            const option = el("option", "", name);
            option.value = name;
            category.append(option);
        }

        const sort = el(
            "select",
            "background:#26262a;border:1px solid #3a3a42;color:#eee;border-radius:8px;" +
                "padding:8px;font-size:13px;",
        );
        // "Saved" is a source, not an ordering: it swaps the lower section from
        // the public gallery to the styles this user saved. It sits in the same
        // dropdown because from the user's side all three answer one question —
        // which styles am I looking at?
        for (const [value, label] of [
            ["recent", "Recent"],
            ["popular", "Popular"],
            ["saved", "Saved"],
        ]) {
            const option = el("option", "", label);
            option.value = value;
            sort.append(option);
        }

        controls.append(input, category, sort);
        panel.append(controls);

        // Two independently paged sections: the user's own library on top, then
        // the gallery (or their saved styles). Separate containers so paging one
        // never re-fetches or scroll-jumps the other.
        const mineBox = el("div", "");
        const galleryBox = el("div", "");
        panel.append(mineBox, galleryBox);

        const cancel = el(
            "button",
            "margin-top:8px;background:transparent;border:1px solid #444;color:#bbb;" +
                "border-radius:8px;padding:8px 14px;cursor:pointer;",
            "Cancel",
        );
        cancel.onclick = () => close(null);
        panel.append(cancel);

        // ── rendering ───────────────────────────────────────────────────────
        const sectionHeader = (text) =>
            el("div", "opacity:.6;font-size:11px;text-transform:uppercase;letter-spacing:.06em;margin:10px 2px 6px;", text);

        const message = (text) =>
            el("div", "background:#26262a;border-radius:8px;padding:10px 12px;margin-bottom:6px;opacity:.75;font-size:13px;", text);

        function row(style) {
            const button = el(
                "button",
                "display:flex;gap:10px;align-items:center;width:100%;text-align:left;" +
                    "background:#26262a;border:0;color:#eee;border-radius:8px;padding:8px 10px;" +
                    "margin-bottom:6px;cursor:pointer;",
            );
            button.onmouseenter = () => (button.style.background = "#32323a");
            button.onmouseleave = () => (button.style.background = "#26262a");

            // Thumbnail (plan P3-1) — a fixed box so rows align with or without one.
            const thumb = el(
                "div",
                "width:44px;height:44px;border-radius:6px;background:#1a1a1d;flex:none;overflow:hidden;",
            );
            if (style.heroImage) {
                const img = el("img", "width:100%;height:100%;object-fit:cover;display:block;");
                img.src = style.heroImage;
                img.loading = "lazy";
                img.alt = "";
                thumb.append(img);
            }

            const text = el("div", "flex:1;min-width:0;");
            const meta = [style.author, style.category].filter(Boolean).join(" · ");
            const nameRow = el("div", "display:flex;align-items:center;gap:6px;min-width:0;");
            nameRow.append(
                el(
                    "div",
                    "font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;",
                    style.name ?? style.slug,
                ),
            );

            // `lastGeneratedAt: null` means the style has never been generated on
            // styleref.io, and loading it answers 409 styleref_not_generated. Only
            // the user's own styles carry the field, so gallery rows never badge.
            // Still selectable: they may have generated it in another tab since
            // this list was fetched, and refusing the click would strand them.
            if (style.lastGeneratedAt === null) {
                const badge = el(
                    "span",
                    "flex:none;font-size:10px;padding:1px 6px;border-radius:999px;" +
                        "background:rgba(180,120,0,.25);color:#f0c060;white-space:nowrap;",
                    "not generated",
                );
                badge.title =
                    "Never generated on styleref.io — generate it once on the web, then load it here.";
                nameRow.append(badge);
            }
            text.append(nameRow);

            if (meta) text.append(el("div", "opacity:.65;font-size:12px;margin-top:2px;", meta));
            const tags = (style.tags || []).join(", ");
            if (tags) text.append(el("div", "opacity:.5;font-size:11px;margin-top:2px;", tags));

            button.append(thumb, text);

            if (style.url) {
                const link = el(
                    "a",
                    "flex:none;opacity:.55;font-size:12px;color:#9db4ff;text-decoration:none;padding:4px;",
                    "view ↗",
                );
                link.href = style.url;
                link.target = "_blank";
                link.rel = "noopener";
                link.onclick = (e) => e.stopPropagation();
                button.append(link);
            }

            button.onclick = () => close(style.slug);
            return button;
        }

        /** A ‹ Prev / Page N / Next › bar. Omitted entirely on a lone page. */
        function pager({ page, hasPrev, hasNext, onPrev, onNext }) {
            if (!hasPrev && !hasNext) return null;
            const bar = el("div", "display:flex;align-items:center;gap:8px;margin:2px 0 12px;");

            const step = (label, enabled, onClick) => {
                const b = el(
                    "button",
                    "background:transparent;border:1px solid #3a3a42;border-radius:8px;" +
                        "padding:5px 12px;font-size:12px;" +
                        (enabled
                            ? "color:#ddd;cursor:pointer;"
                            : "color:#666;cursor:default;opacity:.5;"),
                    label,
                );
                // Disabled by not binding a handler — `disabled` on a plain button
                // inside the canvas overlay still swallows the click in LiteGraph.
                if (enabled) b.onclick = onClick;
                return b;
            };

            bar.append(
                step("‹ Prev", hasPrev, onPrev),
                el("div", "opacity:.55;font-size:12px;", `Page ${page + 1}`),
                step("Next ›", hasNext, onNext),
            );
            return bar;
        }

        /** Replace a section's contents with a header plus whatever `body` adds. */
        function section(box, title, build) {
            const next = document.createDocumentFragment();
            next.append(sectionHeader(title));
            build(next);
            box.replaceChildren(next);
        }

        // ── My styles: keyset paging ────────────────────────────────────────
        // The API pages this lane by opaque cursor, which only walks forward, so
        // Prev works off a stack: index N holds the cursor that produced page N.
        let minePage = 0;
        let mineCursors = [null];
        let mineRequest = 0;

        async function renderMine() {
            const id = ++mineRequest;
            const query = input.value.trim();
            section(mineBox, "My styles", (out) => out.append(message("Loading…")));

            const cursor = mineCursors[minePage] ?? null;
            const url =
                `/styleref/my-styles?limit=${PAGE_SIZE}` +
                (query ? `&query=${encodeURIComponent(query)}` : "") +
                (cursor ? `&cursor=${encodeURIComponent(cursor)}` : "");
            const mine = await fetchJson(url);
            if (id !== mineRequest) return; // a newer keystroke superseded this

            // Remember the cursor for the page after this one, so Next knows it
            // exists and can move there without refetching.
            if (mine.nextCursor) mineCursors[minePage + 1] = mine.nextCursor;
            else mineCursors.length = minePage + 1;

            section(mineBox, "My styles", (out) => {
                if (mine.signedIn === false) {
                    out.append(message("Sign in (StyleRef Login node) to see your styles here."));
                    return;
                }
                if (mine.error) {
                    out.append(message(mine.error));
                    return;
                }
                const styles = mine.styles || [];
                if (!styles.length) {
                    out.append(
                        message(
                            query
                                ? `None of your styles match "${query}".`
                                : "No styles in your library yet.",
                        ),
                    );
                    // A filtered-to-nothing page 2+ still needs a way back.
                    if (minePage === 0) return;
                }
                for (const style of styles) out.append(row(style));

                const bar = pager({
                    page: minePage,
                    hasPrev: minePage > 0,
                    hasNext: Boolean(mineCursors[minePage + 1]),
                    onPrev: () => {
                        minePage -= 1;
                        renderMine();
                    },
                    onNext: () => {
                        minePage += 1;
                        renderMine();
                    },
                });
                if (bar) out.append(bar);
            });
        }

        // ── Gallery / Saved: offset paging ──────────────────────────────────
        let galleryPage = 0;
        let galleryRequest = 0;

        async function renderGallery() {
            const id = ++galleryRequest;
            const query = input.value.trim();
            const saved = sort.value === "saved";
            const title = saved ? "Saved" : "Gallery";
            section(galleryBox, title, (out) => out.append(message("Searching…")));

            const offset = galleryPage * PAGE_SIZE;
            const params =
                `?query=${encodeURIComponent(query)}&limit=${PAGE_SIZE}&offset=${offset}` +
                (category.value ? `&category=${encodeURIComponent(category.value)}` : "");
            const url = saved
                ? `/styleref/saved${params}`
                : `/styleref/search${params}&sort=${encodeURIComponent(sort.value)}`;

            const gallery = await fetchJson(url);
            if (id !== galleryRequest) return;

            section(galleryBox, title, (out) => {
                if (gallery.signedIn === false) {
                    out.append(
                        message("Sign in (StyleRef Login node) to see the styles you saved."),
                    );
                    return;
                }
                if (gallery.error) {
                    // Inline message row, never alert() (plan P3-5).
                    out.append(message(gallery.error));
                    return;
                }
                const styles = gallery.styles || [];
                if (!styles.length) {
                    out.append(
                        message(
                            saved
                                ? query
                                    ? `None of your saved styles match "${query}".`
                                    : "You haven't saved any gallery styles yet."
                                : query
                                  ? `No styles matched "${query}". Try a broader query.`
                                  : "No styles found. Browse styleref.io/gallery",
                        ),
                    );
                    if (galleryPage === 0) return;
                }
                for (const style of styles) out.append(row(style));

                const bar = pager({
                    page: galleryPage,
                    hasPrev: galleryPage > 0,
                    hasNext: Boolean(gallery.hasMore),
                    onPrev: () => {
                        galleryPage -= 1;
                        renderGallery();
                    },
                    onNext: () => {
                        galleryPage += 1;
                        renderGallery();
                    },
                });
                if (bar) out.append(bar);
            });
        }

        /** A changed filter invalidates both lanes' paging — restart at page 1. */
        function renderAll() {
            minePage = 0;
            mineCursors = [null];
            galleryPage = 0;
            renderMine();
            renderGallery();
        }

        let debounce = null;
        input.oninput = () => {
            clearTimeout(debounce);
            debounce = setTimeout(() => renderAll(), 250);
        };
        // Category only narrows the gallery lane, but resetting both keeps "page
        // N" honest across the dialog rather than leaving one lane deep in a
        // result set the user can no longer see the start of.
        category.onchange = () => renderAll();
        sort.onchange = () => renderAll();

        overlay.onclick = (e) => {
            if (e.target === overlay) close(null);
        };
        overlay.append(panel);
        document.body.append(overlay);
        input.focus();
        renderAll();
    });
}

/**
 * The Login node's status + buttons, backed by /styleref/auth/*. The status
 * line doubles as the refresh button. Button labels are widget names, which
 * both frontends render, so no custom widget type is involved.
 */
function enhanceLoginNode(node) {
    const statusWidget = node.addWidget("button", "Status: checking…", null, () =>
        refreshLoginStatus(node, statusWidget),
    );

    node.addWidget("button", "Sign in (browser)", null, async () => {
        setStatus(node, statusWidget, "Status: waiting for browser approval…");
        const res = await postJson("/styleref/auth/signin");
        setStatus(node, statusWidget, loginStatusLabel(res));
        // A failed sign-in carries instructions (e.g. the headless/token
        // path) that don't fit a status label — show them in full.
        if (res && res.ok === false && res.message) showMessage(res.message);
    });

    node.addWidget("button", "Sign out", null, async () => {
        const res = await postJson("/styleref/auth/signout");
        setStatus(node, statusWidget, loginStatusLabel(res));
    });

    // Keep the status honest without a manual click: re-check when the node's
    // own queue-driven action ran, when the tab regains focus (the browser
    // sign-in happens in another tab), and on a slow heartbeat.
    const onExecuted = node.onExecuted;
    node.onExecuted = function (...args) {
        refreshLoginStatus(node, statusWidget);
        return onExecuted?.apply(this, args);
    };
    const onFocus = () => refreshLoginStatus(node, statusWidget);
    window.addEventListener("focus", onFocus);
    const heartbeat = setInterval(onFocus, 30_000);

    const onRemoved = node.onRemoved;
    node.onRemoved = function (...args) {
        window.removeEventListener("focus", onFocus);
        clearInterval(heartbeat);
        return onRemoved?.apply(this, args);
    };

    refreshLoginStatus(node, statusWidget);
}

/** A small dismissable overlay for multi-line messages (no alert()). */
function showMessage(text) {
    const overlay = el(
        "div",
        "position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:10000;" +
            "display:flex;align-items:center;justify-content:center;",
    );
    const panel = el(
        "div",
        "background:#1c1c1f;color:#eee;border-radius:12px;padding:16px;max-width:560px;" +
            "max-height:70vh;overflow:auto;font-family:system-ui,sans-serif;font-size:13px;" +
            "white-space:pre-wrap;box-shadow:0 20px 60px rgba(0,0,0,.5);",
        text,
    );
    const close = el(
        "button",
        "display:block;margin-top:12px;background:transparent;border:1px solid #444;" +
            "color:#bbb;border-radius:8px;padding:8px 14px;cursor:pointer;",
        "Close",
    );
    close.onclick = () => overlay.remove();
    panel.append(close);
    overlay.onclick = (e) => {
        if (e.target === overlay) overlay.remove();
    };
    overlay.append(panel);
    document.body.append(overlay);
}

function loginStatusLabel(payload) {
    if (payload?.error) return `Status: unavailable (${payload.error})`;
    return payload?.signedIn ? "Status: ✔ signed in" : "Status: signed out";
}

/**
 * Write a button widget's visible text, on both frontends.
 *
 * This is why the status used to be stuck on "checking…" forever. Writing only
 * `widget.name` is enough for LiteGraph, which draws `label || name` straight
 * from the widget each frame. The Vue frontend (Nodes 2.0) renders
 * `label ?? name` once into a DOM button and repaints from its own reactive
 * copy, so a bare `name` assignment changed a field nothing was watching and the
 * first label ever set stayed on screen — no failed request involved, which is
 * why it looked like a hang rather than an error.
 *
 * Setting `label` is what actually fixes it; `name` is kept in step so the
 * LiteGraph path and anything reading the widget by name still agree.
 */
function setStatus(node, widget, text) {
    widget.label = text;
    widget.name = text;
    node.setDirtyCanvas(true, true);
    // Nodes 2.0 draws from the canvas object, not the node, and ignores the
    // per-node dirty flag above.
    app.canvas?.setDirty?.(true, true);
}

async function refreshLoginStatus(node, statusWidget) {
    try {
        const res = await fetchJson("/styleref/auth/status");
        setStatus(node, statusWidget, loginStatusLabel(res));
    } catch (err) {
        // fetchJson swallows transport errors, but a frontend API change could
        // still throw here — and silently leaving "checking…" on screen is the
        // exact failure this function exists to prevent.
        setStatus(node, statusWidget, `Status: unavailable (${err?.message ?? err})`);
    }
}

/** True when Apply's `positive` output feeds a CLIPTextEncode (plan P1-3). */
function positiveFeedsClipEncode(node) {
    const graph = node.graph;
    const output = node.outputs?.find((o) => o.name === "positive");
    if (!graph || !output?.links?.length) return false;
    return output.links.some((linkId) => {
        const link = graph.links?.[linkId];
        const target = link ? graph.getNodeById(link.target_id) : null;
        return target?.type === "CLIPTextEncode";
    });
}

app.registerExtension({
    name: "styleref.load.search",

    async setup() {
        await probeRoutes();
    },

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData?.name === LOAD_NODE) {
            const onCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                const result = onCreated?.apply(this, arguments);

                // Only add the search button when the backend routes exist
                // (plan P3-7) — otherwise it could only 404.
                if (routesAvailable !== false) {
                    this.addWidget("button", "Search styles…", null, async () => {
                        const refWidget = this.widgets?.find((w) => w.name === "style_ref");
                        const searchWidget = this.widgets?.find((w) => w.name === "search");
                        if (!refWidget) return;

                        const chosen = await openPicker((searchWidget?.value ?? "").trim());
                        if (!chosen) return;

                        refWidget.value = chosen;
                        refWidget.callback?.(chosen);
                        this.setDirtyCanvas(true, true);
                    });
                }

                // Refresh affordance (plan P1-6): the backend drops its cached
                // copy and dirties the node, so the next queue re-downloads —
                // no need to toggle use_cache off and back on.
                if (routesAvailable !== false) {
                    this.addWidget("button", "↻ Refresh style", null, async () => {
                        const refWidget = this.widgets?.find((w) => w.name === "style_ref");
                        const ref = (refWidget?.value ?? "").trim();
                        if (!ref) return;
                        await postJson("/styleref/refresh", { ref });
                        this.setDirtyCanvas(true, true);
                    });
                }

                return result;
            };
        }

        if (nodeData?.name === LOGIN_NODE && routesAvailable !== false) {
            const onCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                const result = onCreated?.apply(this, arguments);
                enhanceLoginNode(this);
                return result;
            };
        }

        if (nodeData?.name === APPLY_NODE) {
            // Warning badge (plan P1-3): `natural`/`style_md` are copy-out
            // documents; wiring one into a CLIP encoder is almost certainly a
            // mistake. Additive drawing only — nothing else is touched.
            const onDrawForeground = nodeType.prototype.onDrawForeground;
            nodeType.prototype.onDrawForeground = function (ctx) {
                onDrawForeground?.apply(this, arguments);
                if (this.flags?.collapsed) return;
                const target = this.widgets?.find((w) => w.name === "target");
                if (!COPY_OUT_TARGETS.includes(target?.value) || !positiveFeedsClipEncode(this))
                    return;

                const label = `⚠ ${target.value} target → CLIP encoder`;
                ctx.save();
                ctx.font = "11px system-ui, sans-serif";
                const width = ctx.measureText(label).width + 14;
                ctx.fillStyle = "rgba(180,120,0,.85)";
                ctx.beginPath();
                ctx.roundRect(this.size[0] - width - 8, -46, width, 20, 5);
                ctx.fill();
                ctx.fillStyle = "#fff";
                ctx.fillText(label, this.size[0] - width - 1, -32);
                ctx.restore();
            };
        }
    },
});
