# Working in this repository

Instructions for Claude Code (and anyone else writing code here). The owner reviews every change by reading it, so the code has to
explain itself to someone who did not write it.

## Comment the code you write

When you write or change code, add comments of good quality, so that a reviewer can tell what each section is doing without having to
work it out. This applies to every kind of file: Python, TypeScript and TSX, CSS, shell scripts, and the tests.

**What to comment**

- **Every file, function, class and component** says what it is for, in a sentence or two. If a function has a docstring or a doc comment, keep
  it true.
- **Every major block inside a function** (a step in a longer routine, a loop with a purpose, a branch that handles a special case) gets a
  comment saying what the block does.
- **The reason, wherever a choice is not obvious.** Why this order and not another, why this check exists, what goes wrong if it is removed,
  why a value is what it is. The reviewer can see *what* the code does; what they cannot see is *why*.
- **Anything surprising**: a workaround, a timing assumption, a limit imposed by something outside this repository, a rule that comes from the
  spec. Say where the rule comes from, for example "DESIGN.md §30.2", as the existing code does.
- **Tests**: say what each group of tests protects and why that matters; explain each helper and fixture; explain any sleep, timestamp or
  fake, and what the final assertions prove. A test with a clear name still deserves a line saying what would break if it failed.

**How to write them**

- Plain sentences, in the plain style the rest of the project uses. Say it once, where it applies.
- Write for a reader who knows the language but not this code or the thinking behind it.
- Do not narrate the obvious (`i += 1  # add one to i`). A comment that repeats the line it sits on makes the useful comments harder to find.
- Keep comments true. When you change code, change the comments that describe it, and delete the ones that are no longer so.
