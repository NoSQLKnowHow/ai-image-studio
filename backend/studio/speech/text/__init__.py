"""Turning a source (pasted text, a web page, a file) into the parts and pieces of a reading (DESIGN.md §38.5).

Everything in this package is pure functions on text, so it is tested without a GPU, a server or a network:
  model.py                  the records that move through the pipeline
  clean.py                  making text speakable (markup, links, footnote marks, hard-wrapped lines)
  sentences.py              the sentence splitter that knows abbreviations
  pieces.py, parts.py       dividing paragraphs into pieces and a text into parts
  extract_*.py              one reader for each kind of source
  fetch.py                  fetching a web page safely
  isolated.py               running a reader in a child process with limits
  prepare.py                the whole of it, from a source to the review the page shows
"""
