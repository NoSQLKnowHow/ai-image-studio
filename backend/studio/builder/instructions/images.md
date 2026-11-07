<!-- FIRST DRAFT for the owner's review. What the image model likes, as far as the studio knows it (DESIGN.md section 37.5); not yet tested against the real model. -->

# This prompt is for Images (Qwen-Image)

The model takes long, descriptive, natural-language prompts. Write sentences, not a list of keywords: say what is in the picture, where it is, what it is doing, the light, the mood, the style or medium (a photograph, a watercolour, a flat illustration), the viewpoint and the framing, and the colours. Be specific rather than grand: "low golden light from the left, long shadows on wet cobblestones" does more than "beautiful lighting". Do not pile up quality words ("masterpiece", "8k", "trending") unless the person asks for them.

- **Words in the picture.** If words must appear in the picture, put them in "double quotes", spelled exactly, and say where they are and what they look like ("a hand-painted sign reading "HARBOUR BAKERY" above the door"). Keep such text short.
- **A negative prompt** says what to avoid. It only has an effect when guidance is above 1, so whenever you suggest a negative prompt also suggest a guidance above 1 (4 is a good starting value). Only suggest a negative prompt when there is something real to avoid; otherwise leave it out.
- **Settings.** In "settings" you may give "shape", one of {{shapes}} (width to height), chosen to suit the subject (a standing person: 3:4 or 2:3; a wide landscape: 16:9 or 3:2; a phone wallpaper: 9:16; an icon or a sticker: 1:1), and "guidance", a number from {{guidance_min}} to {{guidance_max}}. Leave "steps" out unless the person asks for a quick try.
- Keep the draft inside {{prompt_chars}} characters.
