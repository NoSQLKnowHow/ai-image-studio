import { useCallback, useMemo, useRef, useState } from "react";
import { ApiError } from "./api";
import { initialCast, withChoice, type Cast, type SpeakerChoice } from "./speakers";
import {
  draftFromPrepared, editParagraph as editParagraphIn, fileProblem, mergeWithNext as mergeIn, normalizeUrl, renamePart as renameIn, reviewChanged, setInclude as includeIn,
  switchesChanged, urlProblem, type DraftPart, type Switches,
} from "./speech";
import { speechApi } from "./speechApi";
import type { Prepared, SpeechLimits } from "./speechTypes";

// What the Speech tab holds between choosing a source and pressing Make reading (DESIGN.md §38.2): where the text comes from, the review the server made of it, and
// the changes the person makes to that review. The calls are `speechApi`'s; the rules are speech.ts's; this is only the state and the order things happen in.

export type Way = "text" | "url" | "file";
export type Phase = "idle" | "preparing" | "ready";
export interface Problem {
  message: string;
  hint: string | null;
}

/** What the review had when the person pressed Prepare while it held changes of theirs: asked once, answered with Replace or Keep. */
export type Pending = (() => void) | null;

export interface SpeechSource {
  way: Way;
  setWay: (way: Way) => void;
  text: string;
  setText: (text: string) => void;
  url: string;
  setUrl: (url: string) => void;
  file: File | null;
  clean: boolean;
  setClean: (clean: boolean) => void;
  readExtras: boolean;
  setReadExtras: (readExtras: boolean) => void;
  speakers: boolean; // find the speakers: lines that begin with a name and a colon (§44.5)
  setSpeakers: (speakers: boolean) => void;
  cast: Cast; // what the person chose for each name the review found
  setChoice: (name: string, change: Partial<SpeakerChoice>) => void;
  phase: Phase;
  problem: Problem | null;
  prepared: Prepared | null;
  parts: DraftPart[];
  title: string;
  setTitle: (title: string) => void;
  changed: boolean; // the review holds changes of the person's own
  switchesStale: boolean; // the switches are not the ones the review was made with
  replacing: boolean; // the person pressed Prepare over a review they had changed: waiting for Replace or Keep
  prepareText: () => void;
  prepareUrl: () => void;
  chooseFile: (file: File) => void;
  prepareAgain: () => void;
  confirmReplace: () => void;
  keepReview: () => void;
  stop: () => void;
  include: (index: number, include: boolean) => void;
  rename: (index: number, title: string) => void;
  merge: (index: number) => void;
  edit: (part: number, paragraph: number, text: string) => void;
}

const toProblem = (error: unknown): Problem => {
  if (error instanceof ApiError) return { message: error.message, hint: error.hint };
  return { message: "Something went wrong preparing the text.", hint: null };
};

const isAbort = (error: unknown): boolean => error instanceof DOMException && error.name === "AbortError";

export function useSpeechSource(limits: Pick<SpeechLimits, "upload_mb"> | undefined): SpeechSource {
  const [way, setWayState] = useState<Way>("text");
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [clean, setClean] = useState(true);
  const [readExtras, setReadExtras] = useState(false);
  const [speakers, setSpeakers] = useState(true);
  const [cast, setCast] = useState<Cast>({});
  const [phase, setPhase] = useState<Phase>("idle");
  const [problem, setProblem] = useState<Problem | null>(null);
  const [prepared, setPrepared] = useState<Prepared | null>(null);
  const [parts, setParts] = useState<DraftPart[]>([]);
  const [title, setTitle] = useState("");
  const [made, setMade] = useState<Switches | null>(null); // the switches the review on the page was asked for with
  const [pending, setPending] = useState<Pending>(null);
  const newest = useRef(0); // the number of the latest request: an answer to an earlier one is not wanted any more
  const controller = useRef<AbortController | null>(null);

  // the person's own changes: to the parts (left out, renamed, joined, edited) or to the title of the whole reading
  // (a name turned off, or given a voice of its own, is the person's work too: it is asked about before it is thrown away, §44.5)
  const castChanged = useMemo(() => Object.values(cast).some((c) => !c.on || c.voice !== null), [cast]);
  const changed = useMemo(() => !!prepared && (reviewChanged(parts, prepared) || title !== prepared.title || castChanged), [parts, prepared, title, castChanged]);
  const switchesStale = !!prepared && !!made && switchesChanged(made, { clean, readExtras, speakers });

  /** Ask the server, and take the answer if it is still the one wanted. A failure leaves the review as it was: work already done on it is not thrown away
   *  because a second source could not be read. */
  const run = useCallback(async (call: (signal: AbortSignal) => Promise<Prepared>) => {
    const asked = { clean, readExtras, speakers }; // what this question is asked with, kept to compare the switches with later
    const mine = ++newest.current;
    controller.current?.abort();
    const mineController = new AbortController();
    controller.current = mineController;
    setPhase("preparing");
    setProblem(null);
    setPending(null);
    try {
      const result = await call(mineController.signal);
      if (mine !== newest.current) return;
      setPrepared(result);
      setMade(asked);
      setParts(draftFromPrepared(result));
      setCast(initialCast(result.speakers ?? [])); // a new review has new names: every one starts on, with the main voice (§44.8 choice 13)
      setTitle(result.title);
      setPhase("ready");
    } catch (error) {
      if (mine !== newest.current) return;
      setPhase(prepared ? "ready" : "idle");
      if (!isAbort(error)) setProblem(toProblem(error));
    }
  }, [prepared, clean, readExtras, speakers]);

  /** Start something that replaces the review: at once, or after the person has said so when the review holds changes of theirs. */
  const replacing = useCallback((start: () => void) => {
    if (prepared && changed) setPending(() => start);
    else start();
  }, [prepared, changed]);

  const fail = (message: string) => {
    setPending(null);
    setProblem({ message, hint: null });
  };

  const prepareText = useCallback(() => {
    if (!text.trim()) return fail("Paste the text to read.");
    replacing(() => void run(() => speechApi.prepare({ source: "text", text, clean, read_extras: readExtras, speakers })));
  }, [text, clean, readExtras, speakers, replacing, run]);

  const prepareUrl = useCallback(() => {
    const wrong = urlProblem(url);
    if (wrong) return fail(wrong);
    const address = normalizeUrl(url);
    setUrl(address);
    replacing(() => void run(() => speechApi.prepare({ source: "url", url: address, clean, read_extras: readExtras, speakers })));
  }, [url, clean, readExtras, speakers, replacing, run]);

  const chooseFile = useCallback((chosen: File) => {
    const wrong = fileProblem(chosen, { upload_mb: limits?.upload_mb ?? 50 });
    if (wrong) return fail(wrong);
    setFile(chosen);
    replacing(() => void run((signal) => speechApi.prepareFile(chosen, { clean, read_extras: readExtras, speakers }, signal)));
  }, [limits, clean, readExtras, speakers, replacing, run]);

  const prepareAgain = useCallback(() => {
    if (way === "file" && file) chooseFile(file);
    else if (way === "url") prepareUrl();
    else prepareText();
  }, [way, file, chooseFile, prepareUrl, prepareText]);

  const confirmReplace = useCallback(() => pending?.(), [pending]);
  const keepReview = useCallback(() => setPending(null), []);
  const stop = useCallback(() => controller.current?.abort(), []);

  return {
    way, setWay: setWayState, text, setText, url, setUrl, file, clean, setClean, readExtras, setReadExtras, speakers, setSpeakers, cast,
    setChoice: (name, change) => setCast((current) => withChoice(current, name, change)), phase, problem, prepared, parts, title, setTitle,
    changed, switchesStale, replacing: pending !== null, prepareText, prepareUrl, chooseFile, prepareAgain, confirmReplace, keepReview, stop,
    include: (index, include) => setParts((current) => includeIn(current, index, include)),
    rename: (index, name) => setParts((current) => renameIn(current, index, name)),
    merge: (index) => setParts((current) => mergeIn(current, index)),
    edit: (part, paragraph, words) => setParts((current) => editParagraphIn(current, part, paragraph, words)),
  };
}
