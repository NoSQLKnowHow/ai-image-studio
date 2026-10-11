"""The words the studio adds to the user's idea for a Vector picture (DESIGN.md §31.3, §31.12): the *recipe*.

Why it matters: the clean-up can only make good laser files from a picture that is already flat. Qwen-Image will draw gradients, shading,
soft edges, photographic texture and tiny details unless it is told firmly not to, and the clean-up would then have to throw most of that
away. So the positive text asks for FLAT artwork: solid colour regions, thick clean outlines, a plain white background, a stated number of
colours (and the named ones), no text; and the negative text, which is fixed and which the user cannot edit, lists everything that makes a
picture hard to cut. (The negative only counts when guidance is above 1, so a Vector run always sends guidance: `RECIPE_CFG_SCALE`.)

Pure text, no model and no library: the page asks for it on every keystroke, and the server builds it again when the request has no edited
recipe. The same arguments always give the same text.
"""

from __future__ import annotations

from .colours import colour_name

# Fixed, not editable, and the same for every kind. Everything the clean-up cannot use: tone (gradient, shading, shadow, glow, soft edges,
# blur), surface (photograph, texture, noise, grain, halftone, hatching), things that cannot be cut (text, lettering, watermark, thin lines,
# tiny details), and things that add colours that were not asked for (transparency, reflections, 3D, a frame).
NEGATIVE = ("gradient, shading, shadow, glow, soft edges, blur, photograph, photorealistic, 3D render, reflections, texture, noise, grain, "
            "halftone, hatching, text, lettering, letters, numbers, watermark, signature, border, frame, thin lines, hairlines, "
            "tiny details, small dots, transparency, extra colours")

# What each kind of artwork is asked to be (Silhouette or stencil, Flat colour art, Line art: §31.3). The text is a sentence that follows
# the idea and a comma; `{inks}` is filled with the colour wording below.
STYLE = {
    "silhouette": "a bold flat silhouette: solid shapes filled with {inks}, with a smooth clean outline and no detail lines inside them, "
                  "like a stencil or a vinyl sticker",
    "flat": "flat colour artwork made of a few large solid colour regions filled with {inks}, separated by thick clean outlines, "
            "like a screen-printed poster or a paper cut-out",
    "line": "clean bold line art: only even, thick outlines drawn in {inks}, with nothing filled in, like a coloured marker drawing "
            "or a stencil of lines",
}
STYLE_FALLBACK = "flat"  # an unknown kind is treated as flat colour art rather than refused: the server has already checked it


def _ink_words(colours: int, palette: tuple[str, ...]) -> str:
    """The inks in words: the named ones with their codes, then however many the picture is free to choose. White is not an ink (it is
    the background), and `colours` counts it (§31.12.3 item 1), so there are `colours - 1` of them."""
    inks = max(colours - 1, 1)
    named = [f"{colour_name(colour)} ({colour})" for colour in palette]
    free = inks - len(named)
    if named and free <= 0:
        return " and ".join(", ".join(named).rsplit(", ", 1)) if len(named) > 1 else named[0]
    if named:
        more = "one more solid colour that suits" if free == 1 else f"{free} more solid colours that suit"
        return f"{', '.join(named)} and {more} the subject"
    return "one solid colour (a strong dark one)" if inks == 1 else f"{inks} different solid colours"


def positive_text(idea: str, kind: str, colours: int, palette: tuple[str, ...]) -> str:
    """The positive recipe: the idea, the style of the kind, how many colours and which, and the fixed rules of flat artwork."""
    idea = idea.strip().rstrip(".,;:!").strip()
    if idea:
        idea = idea[0].upper() + idea[1:]  # the idea starts the sentence
    style = STYLE.get(kind, STYLE[STYLE_FALLBACK]).format(inks=_ink_words(colours, palette))
    inks = max(colours - 1, 1)
    count = f"Exactly {colours} colours in total: the white background and {inks} other solid colour{'s' if inks != 1 else ''}. "
    rules = ("On a plain pure white background that fills the whole picture, with the subject large and centred. Every colour is perfectly "
             "flat: no gradients, no shading, no shadows, no texture, no soft edges, no text. Thick, simple shapes with smooth clean "
             "edges and nothing too small to cut out with a laser.")
    return f"{idea}, {style}. {count}{rules}" if idea else f"{style[0].upper() + style[1:]}. {count}{rules}"
