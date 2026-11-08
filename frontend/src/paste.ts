import type { ToastAction, ToastKind } from "./hooks";
import type { AddOutcome } from "./useTray";

// Pictures by paste and drop (DESIGN.md §36): where a pasted picture goes, what a text field keeps for itself, and what Generate does with pictures it is
// given. All of it is a function of plain values (the files, what the clipboard also holds, where the cursor was, which tab is showing), so the rules can
// be tested without a browser; `usePicturePaste.ts` is the one listener that reads the page and calls them.
//
// The words are here too, so that the page, the tests and the e2e say the same sentences.

export const PICTURES_ON_IMAGES_TAB = "Pictures are used on the Images tab.";
export const GENERATE_PICTURES_HELP = "Paste or drop pictures to work from them: the card switches to Edit.";
export const STILL_UPSCALING = "Still upscaling the last picture.";
export const EDITING_UNAVAILABLE = "Editing isn't available.";
/** The server's own words for a file that is not a picture it reads (backend/studio/inputs.py), so a file refused here and one refused there read the same. */
export const NOT_A_PICTURE = "That isn't a PNG, JPEG or WebP image.";
/** Upscale a picture…'s tooltip (§27.9, §36.2): the chooser, a drop and a paste are three ways to the same thing. */
export const UPSCALE_TITLE =
  "Choose a picture from this computer and get a 4K copy of it back as a download, or drop or paste a picture here. Nothing is added to your history. " +
  "It makes the picture bigger, not sharper: no detail is added.";

/** The id of the prompt box: a picture pasted there is taken even when the clipboard has text as well (§36.2), as it always was. */
export const PROMPT_ID = "prompt";
/** The id of the Prompt builder's message box: as the prompt box on Images, a picture pasted there is taken even when the clipboard has text as well. */
export const BUILDER_INPUT_ID = "builder-input";
/** What marks the Upscale control as the place a paste goes when it has the focus. */
export const UPSCALE_MARK = "data-upscale-drop";

/** A file the clipboard or a drop gave that is a picture by its type. A clipboard names the type of what it holds, so this is exact for a paste. */
export const isImageType = (file: File): boolean => file.type.startsWith("image/");

/** A file that may be a picture: its type says so, or says nothing (some systems give none for a .webp). The server decides for the second kind. */
export const mayBePicture = (file: File): boolean => file.type === "" || file.type.startsWith("image/");

/** The pictures on a clipboard, in the order it holds them. Most browsers list a pasted picture in `files`; some only among the `items`, so those are
 *  looked at when `files` has none (never both, or one picture would count twice). */
export function pastedPictures(data: Pick<DataTransfer, "files" | "items"> | null | undefined): File[] {
  if (!data) return [];
  const fromFiles = Array.from(data.files ?? []).filter(isImageType);
  if (fromFiles.length) return fromFiles;
  return Array.from(data.items ?? [])
    .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
    .map((item) => item.getAsFile())
    .filter((file): file is File => file !== null);
}

/** Whether the clipboard also holds plain text. A range copied from a spreadsheet or a selection copied from a page carries a picture of itself beside its
 *  text; a screenshot or "Copy image" carries no text. */
export function hasPlainText(data: Pick<DataTransfer, "getData"> | null | undefined): boolean {
  try {
    return (data?.getData("text/plain") ?? "") !== "";
  } catch {
    return false; // a clipboard that cannot say is taken to have none
  }
}

/** Whether pasting here puts text in: a text box, a text area, an editable element. (A button, a checkbox, a range or a file input takes no text.) */
export function isTextField(element: Element | null): boolean {
  if (!element) return false;
  if (element instanceof HTMLTextAreaElement) return true;
  if (element instanceof HTMLInputElement) return !["button", "checkbox", "radio", "range", "file", "submit", "reset", "image", "color"].includes(element.type);
  return element instanceof HTMLElement && element.isContentEditable === true;
}

export type PasteRoute =
  | { kind: "ignore" } // not ours: the browser does what it does
  | { kind: "tray"; files: File[] } // the Images tab's pictures (Edit's tray, or Generate, which switches to Edit)
  | { kind: "upscale"; files: File[] } // Upscale a picture…, which has the focus
  | { kind: "builder"; files: File[] } // the Prompt builder tab: a picture for the chat (DESIGN.md §37.3 rule 13)
  | { kind: "wrong-tab" }; // Music or Vector: no pictures are used there, and the page says so

export interface PasteFacts {
  files: File[]; // the pictures on the clipboard
  plainText: boolean; // and the clipboard also holds text
  target: Element | null; // where the cursor was: the element that has the focus
  tab: "images" | "music" | "vector" | "builder";
  modalOpen: boolean; // a dialog, the Options drawer or a viewer is open
  ready: boolean; // the page knows what the server can do
}

const IGNORE: PasteRoute = { kind: "ignore" };

/** Where a paste goes (§36.2, §36.3). In this order:
 *  1. A paste with no picture is never touched, in any field: the browser pastes the text.
 *  2. While a dialog, the drawer or a viewer is open nothing is added: focus is held there and the picture would land where it could not be seen.
 *  3. On the Music and Vector tabs a picture is not taken; the page says where pictures are used. (In a text field with text beside the picture, the text
 *     is what the person meant, and goes in without a word.)
 *  4. On the Images tab, in the prompt box the picture wins over text; in another text field it is taken only if the clipboard has no plain text.
 *  5. With Upscale a picture… focused, the picture goes there, and only there; anywhere else on the page (the bare page, a button) it goes to the tray. */
export function routePaste(facts: PasteFacts): PasteRoute {
  const { files, plainText, target, tab, modalOpen, ready } = facts;
  if (files.length === 0 || !ready) return IGNORE;
  if (modalOpen) return IGNORE;
  const inField = isTextField(target);
  if (tab === "builder") return inField && plainText && target?.id !== BUILDER_INPUT_ID ? IGNORE : { kind: "builder", files };
  if (tab !== "images") return inField && plainText ? IGNORE : { kind: "wrong-tab" };
  if (inField && target?.id !== PROMPT_ID && plainText) return IGNORE;
  if (target?.closest(`[${UPSCALE_MARK}]`)) return { kind: "upscale", files };
  return { kind: "tray", files };
}

/** The toast after Generate turned into Edit (§36.2): how many pictures, and the numbers the prompt calls them by. `first` is the number of the first picture
 *  this paste added (earlier pictures may be in the tray). */
export function switchedToEditText(count: number, first: number): string {
  const numbers = Array.from({ length: count }, (_, i) => first + i).join(", ");
  return `Switched to Edit: ${count} ${count === 1 ? "picture" : "pictures"} added. Refer to ${count === 1 ? "it" : "them"} as image ${numbers}.`;
}

/** What the page lends the intake: the tray, the mode and the toasts. A plain interface so that the rules below can be tested with stand-ins. */
export interface IntakeEnv {
  canEdit: boolean; // the loaded pipeline can edit
  mode: "generate" | "edit";
  trayCount: () => number; // pictures in the tray now
  addFiles: (files: File[], quiet: boolean) => AddOutcome; // `quiet`: do not post the reasons in the tray, return them
  note: (text: string) => void; // post a reason in the tray
  remove: (key: string) => void; // take one picture out of the tray
  setMode: (mode: "generate" | "edit") => void;
  push: (kind: ToastKind, text: string, action?: ToastAction) => void;
}

/** Pictures given to the prompt card, pasted or dropped (§36.2, §36.3 item 4). In Edit they go to the tray as ever. In Generate, which has no picture input,
 *  they are added in order and the card becomes Edit with the prompt kept (the prompt belongs to the page, not to the mode), with a toast that counts them
 *  and offers Undo: Undo removes exactly the pictures this paste added and goes back to Generate, leaving earlier pictures and Generate's own options.
 *  When no picture is accepted the card stays in Generate and each rejection is named; when the pipeline cannot edit, nothing is added. */
export function takePictures(files: File[], env: IntakeEnv): void {
  if (files.length === 0) return;
  if (!env.canEdit) {
    env.push("info", EDITING_UNAVAILABLE);
    return;
  }
  if (env.mode === "edit") {
    env.addFiles(files, false);
    return;
  }
  const before = env.trayCount();
  const outcome = env.addFiles(files, true);
  if (outcome.added === 0) {
    env.push("error", outcome.problems.join(" ") || "No picture was added.");
    return;
  }
  outcome.problems.forEach((text) => env.note(text)); // the pictures that were refused are named in the tray, which is on screen now
  env.setMode("edit");
  env.push("info", switchedToEditText(outcome.added, before + 1), {
    label: "Undo",
    run: () => {
      outcome.keys.forEach((key) => env.remove(key));
      env.setMode("generate");
    },
  });
}
