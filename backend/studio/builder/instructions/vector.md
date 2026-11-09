<!-- FIRST DRAFT for the owner's review. What the Vector tab needs (DESIGN.md section 31 and 37.5); not yet tested against the real model. -->

# This prompt is for Vector (a drawing that becomes an SVG for a laser)

The picture is drawn by the image model and then reduced to a few flat colours and traced into shapes for a laser cutter or engraver. So the idea must be something that works as flat shapes: flat colours, hard edges, clear outlines, a small number of colours. No gradients, no soft shadows, no textures, no photographic detail, no tiny details that a laser could not cut. The studio adds its own wording about flat drawing, so write only the idea: the subject and what it looks like, in plain sentences.

- **Kind.** "kind" is one of {{vector_kinds}}: "silhouette" (a stencil: one shape against the material), "flat" (flat colour art with a few colours), "line" (line art).
- **Colours.** "colours" is a whole number from {{colours_min}} to {{colours_max}}, and white is the bare material and counts as one. Two colours is black on white. More colours means more layers to cut. Prefer few.
- **Inks.** "palette" is an optional list of inks as "#RRGGBB", at most one fewer than "colours" because white is not an ink. Name the colours in the idea as well, in words. Leave "palette" out and the picture chooses its own.
- **Size.** "width_mm" and "height_mm" are the sheet in millimetres, each from {{size_mm_min}} to {{size_mm_max}}. Ask what it will be cut from or fit on before guessing.
- Ask about: the subject, the kind, how many colours, which colours, the size, and what it is for (a sign, an inlay, a stencil). Do not ask about lighting, depth of field or camera.
- Keep the idea inside {{prompt_chars}} characters. There is no negative prompt for Vector.
