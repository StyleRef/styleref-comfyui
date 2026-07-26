"""
StyleRef Login — account status and sign-in from inside ComfyUI.

Not in any data path: it takes no inputs and feeds nothing. The node's surface
is three JS buttons (web/styleref.js) backed by /styleref/auth/* — a status
line, Sign in (browser), Sign out. There are deliberately NO widgets: an
`action` dropdown driven by Queue was the old fallback, and it survived in the
UI next to the buttons as pure confusion.

Queueing the node still does something useful everywhere (including API-only
installs with no frontend): it reports the current auth status in ui.text —
with the STYLEREF_TOKEN instructions when the install is headless. Sign-in
without a frontend goes through `npx styleref login` (the CLI shares this
pack's credentials file) or STYLEREF_TOKEN; sign-out without a frontend is
deleting the credentials file, which the status text names.

Remote installs: the loopback flow cannot work when ComfyUI
runs on a rented GPU box — there is no browser to open and `localhost` is the
wrong machine. The sign-in path detects that case up front and returns the
STYLEREF_TOKEN instructions instead of hanging on a callback that will never
arrive.
"""

from __future__ import annotations

from typing import Any

from styleref_auth import HEADLESS_HELP, auth_status, credentials_path, is_headless

CATEGORY = "StyleRef"


class StyleRefLogin:
    """Shows whether you're signed in; the node's buttons sign you in and out."""

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {"required": {}}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("status",)
    OUTPUT_TOOLTIPS = ("The current sign-in status, also shown in the node body.",)
    FUNCTION = "run"
    CATEGORY = CATEGORY
    DESCRIPTION = (
        "Sign in to StyleRef so your own private styles resolve. Use the "
        "node's buttons; queueing the node reports the current status."
    )
    OUTPUT_NODE = True

    def run(self):
        signed_in, message = auth_status()
        if signed_in:
            message += f"\n\nTo sign out: use this node's Sign out button, or remove {credentials_path()}."
        elif is_headless():
            message = f"{message}\n\nThis looks like a headless install.\n{HEADLESS_HELP}"
        else:
            message += "\n\nUse this node's Sign in button, or run: npx styleref login"
        print(f"[StyleRef] {message}")
        # ui.text renders in the node body, so the user sees it without opening
        # the server console — which they may not have access to at all.
        return {"ui": {"text": [message]}, "result": (message,)}

    @classmethod
    def IS_CHANGED(cls, **_kw):
        # Always re-run: a cached "signed out" status would be wrong the moment
        # a sign-in succeeded.
        return float("nan")
