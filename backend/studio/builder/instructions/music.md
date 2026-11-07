<!-- FIRST DRAFT for the owner's review. What the Music tab fills (DESIGN.md section 26 and 37.5); not yet tested against the real model. -->

# This prompt is for Music (MiniMax-Music3)

The studio builds the music description from six fields: "genre", "mood", "bpm" (tempo, a number), "key" (for example "D minor"), "instruments" and "voice" (the singer, if there is one). You fill those fields in "settings.fields", each a short phrase, and you may leave out any you cannot judge. The draft's "prompt" is a one or two sentence summary of the piece for the person to read.

- **Instrumental or not.** "instrumental" is true for music without singing and false for a song. When it is a song, ask what it is about and write "lyrics".
- **Lyrics.** Put each section tag on its own line, with nothing else on that line: [Intro] [Verse] [Pre-Chorus] [Chorus] [Post-Chorus] [Bridge] [Solo] [Instrumental] [Outro]. The lyrics may not be empty for a song. Keep them under 6000 characters.
- **Length.** "seconds" is a whole number from {{seconds_min}} to {{seconds_max}}; the model may stop sooner.
- Ask about: the genre or style, the mood and how it changes over the piece, the tempo, the instruments, whether there is a voice and what it is like, how long it should be, and what it is for. Do not ask about lighting, framing or colours.
- Keep the summary inside {{prompt_chars}} characters. There is no negative prompt for Music.
