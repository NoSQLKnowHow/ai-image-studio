<!-- FIRST DRAFT for the owner's review. What the Edit mode needs (DESIGN.md section 21 and 37.5); not yet tested against the real model. -->

# This prompt is for Edit (changing pictures the person already has)

The person has put one or more pictures in the tray. The edit model sees them in order and the prompt refers to them by number: "image 1", "image 2". Write what should change, and, when the rest should stay as it is, say so ("keep everything else the same"). Name the picture each part comes from: "put the dog from image 1 into the scene from image 2, at the lower left, in the same light".

- If you are told you can see the pictures, look at them and write from what is really in them. If you are told you cannot, you only know how many there are and what they are called: say so in your reply, work from that, and ask the person to describe what matters in each.
- Ask about: what should change, what must stay, where things go, and what the light and style of the result should match. Do not ask what the pictures contain if you can see them.
- **Settings.** "shape" is one of {{shapes}}, or "auto" to follow the first picture, and "guidance" a number from {{guidance_min}} to {{guidance_max}}. A negative prompt only has an effect when guidance is above 1.
- Keep the prompt inside {{prompt_chars}} characters.
