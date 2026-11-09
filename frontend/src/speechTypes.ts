// The Speech tab's shapes (DESIGN.md §38.9), as the server sends them. Written out here so that the page and the tests say the same keys, as builderTypes.ts does
// for the Prompt builder. Nothing here names an engine: the page learns which engines exist, and what each can do, from `capabilities.speech`.

/** What an engine's start-up check made of it (DESIGN.md §38.3). `checking` lasts until the check has run; `needs_memory` means it would run but the free memory
 *  is short at this moment (the server says by how much, in `reason`). */
export type EngineState = "checking" | "ready" | "not_installed" | "error" | "needs_memory";

/** An engine's licence, as the engine itself states it. `notice` goes beside the engine's name and into the audio files it makes. */
export interface SpeechLicence {
  name: string;
  noncommercial: boolean;
  notice: string;
}

/** One engine of `capabilities.speech.engines`: what it can do (from the adapter) and how it is right now (from the start-up check). */
export interface SpeechEngine {
  id: string;
  title: string;
  licence: SpeechLicence;
  languages: string[]; // empty = the engine takes no language: the voice decides
  can_style: boolean; // takes a style note on its preset voices
  can_clone: boolean; // can speak in a voice made from a recording
  can_design: boolean; // can make a voice from a description
  max_chars: number;
  min_chars: number;
  sample_rate: number;
  interruptible: boolean;
  memory_gb: number;
  model_id: string;
  variants: string[];
  preset_variant: string;
  clone_variant: string | null;
  design_variant: string | null;
  notes: string;
  state: EngineState;
  reason: string | null; // why it cannot run (state not_installed, error or needs_memory), in words
  hint: string | null; // what to do about it
}

/** `capabilities.speech` */
export interface SpeechCaps {
  available: boolean; // at least one engine is usable
  reason: string | null;
  hint: string | null;
  engines: SpeechEngine[];
}

/** `capabilities.limits.speech`: the limits the text pipeline and the uploads obey, so the page says them and never repeats a number. */
export interface SpeechLimits {
  max_chars: number; // the most one reading can hold
  part_max_chars: number; // a part is cut at a chapter, or here
  upload_mb: number;
  fetch_mb: number;
  pause_ms: number;
  voice_seconds: { min: number; max: number }; // the length a recording of a voice may be
  recording_mb: number;
  chars_per_second: number; // how much text a narrator speaks in a second, for turning a length into minutes
}

// ---------------------------------------------------------------- preparing a source (§38.5)
export type SourceKind = "text" | "url" | "file";

/** Why a paragraph will not be read unless the switch says so (code and tables); null for text that will be read. */
export type SkipReason = "code" | "table" | string;

export interface PreparedParagraph {
  kind: "heading" | "paragraph";
  text: string;
  skipped: SkipReason | null;
}

export interface PreparedPart {
  title: string;
  chars: number; // of the paragraphs that will be read
  minutes: number;
  include: boolean; // an EPUB's front matter starts out not included
  note: string | null;
  paragraphs: PreparedParagraph[];
}

/** Where a reading's text came from, as the review states it. */
export type SourceInfo = { kind: "text" } | { kind: "url"; url: string; host: string } | { kind: "file"; name: string };

/** `POST /api/speech/prepare` and `…/prepare/file`: the review of a source. Nothing is stored. */
export interface Prepared {
  title: string;
  source: SourceInfo;
  parts: PreparedPart[];
  warnings: string[];
  cleaning: string[]; // what the clean-up changed, one line each
  chars: number;
  minutes: number;
  options: { clean: boolean; read_extras: boolean };
  chapters_found: number;
}

export interface PrepareTextBody {
  source: "text" | "url";
  text?: string;
  url?: string;
  clean?: boolean;
  read_extras?: boolean;
}

// ---------------------------------------------------------------- making a reading (§38.6, §38.9)
export interface ReadingParagraphBody {
  kind: "heading" | "paragraph";
  text: string;
}

export interface ReadingPartBody {
  title: string;
  paragraphs: ReadingParagraphBody[];
}

/** `POST /api/readings`: the parts the person settled on, already prepared, and whose voice to read them in. Exactly one of `voice` (a preset) or `saved_voice`. */
export interface ReadingBody {
  title: string;
  source: Record<string, unknown>;
  engine: string;
  voice: string;
  saved_voice?: string | null;
  style?: string | null;
  language?: string | null;
  parts: ReadingPartBody[];
}

/** A part's state in the `reading` object of a run: not started, some of its pieces made, or all of them. */
export type PartState = "waiting" | "started" | "done";

export interface ReadingPart {
  idx: number;
  title: string;
  chars: number;
  pieces: number;
  made: number;
  seconds: number; // of the audio made so far
  state: PartState;
}

/** The voice a reading was made in, as its options record it. A preset also records what its source stated of it (gender, accent), so a card can say it
 *  after the engine's list has changed; a saved voice records its id and name only. */
export interface ReadingVoice {
  kind: "preset" | "saved";
  id: string;
  label: string;
  gender?: "female" | "male" | null;
  accent?: string | null;
}

/** Where the listener stopped: shared by whoever opens the reading (there is no login, DESIGN.md §11). The server only stores it. The page decides what the three
 *  numbers mean: `part` and `piece` are indexes, and `offset_ms` is the time from the START OF THE PART (not of the piece), so that a card can say "from 2:14:07 in
 *  part 5" without fetching the part, and a player can seek the part's one file straight to it. `piece` is the piece that time falls in, for the read-along. */
export interface ReadingPosition {
  part: number;
  piece: number;
  offset_ms: number;
}

/** What `PUT /api/readings/{id}/position` takes: the same three numbers the reading reports back as `position`. */
export type PositionBody = ReadingPosition;

/** The `reading` key of a speech run (§38.9): where it stands, part by part, and how long it will take. */
export interface ReadingInfo {
  engine: string;
  engine_title: string | null;
  variant: string;
  voice: ReadingVoice;
  style: string | null;
  language: string | null;
  source: SourceInfo | null;
  licence: SpeechLicence | null;
  title: string | null;
  parts: ReadingPart[];
  parts_total: number;
  parts_done: number;
  pieces_total: number;
  pieces_done: number;
  seconds_made: number;
  bytes: number;
  sample_rate: number;
  waiting: boolean; // queued between two parts with something made: "waiting for its turn" and not "not started"
  seconds_total: number; // made so far plus an estimate of the rest
  seconds_to_go: number | null; // null when nothing is left, or nothing is known of the engine's speed yet
  position: ReadingPosition | null;
}

/** One piece of a part: a paragraph (or a piece of a long one) as it is spoken and as the read-along highlights it. */
export interface PartPiece {
  idx: number;
  kind: "heading" | "paragraph";
  text: string;
  continues: boolean; // carries on a paragraph begun in the piece before it
  status: "pending" | "done" | "failed" | string;
  frames: number | null;
  start_frame: number | null; // where it starts in the part's audio; null until it is made
  error: string | null;
  url: string | null; // its own WAV, once made
}

/** `GET /api/readings/{id}/parts/{n}` */
export interface PartDetail {
  idx: number;
  title: string;
  chars: number;
  complete: boolean;
  sample_rate: number | null;
  total_frames: number;
  url: string | null; // the whole part as one WAV, once every piece is made
  download_url: string | null;
  pieces: PartPiece[];
}

// ---------------------------------------------------------------- voices (§38.4)
/** A preset voice. Gender, language and accent are what the source states, and null where it states nothing: the page then says "accent not stated". */
export interface PresetVoice {
  id: string;
  label: string;
  gender: "female" | "male" | null;
  language: string | null;
  accent: string | null;
  description: string;
  styleable: boolean;
  sample: SampleState;
}

export interface SampleState {
  ready: boolean;
  url: string | null;
}

/** A voice the person made: designed from a description, or cloned from a recording of their own. */
export interface SavedVoice {
  id: string;
  name: string;
  kind: "designed" | "cloned";
  engine: string;
  description: string | null;
  seconds: number | null;
  created_at: string;
  consent_at: string | null;
  sample: SampleState;
}

/** `GET /api/speech/engines/{id}/voices` */
export interface EngineVoices {
  engine: string;
  presets: PresetVoice[];
  saved: SavedVoice[];
}

/** `GET /api/speech/passage`: the words to read aloud to add a voice, the sentence a sample says, and the statement to tick. */
export interface Passage {
  text: string;
  sample_sentence: string;
  consent: string;
  voice_seconds: { min: number; max: number };
  name_max: number;
  description_max: number;
}

export interface SampleBody {
  engine: string;
  voice?: string;
  saved_voice?: string | null;
  recording?: string | null;
}

/** A short job behind a sample or a voice design (§38.4): queued like a run, followed through `GET /api/speech/jobs/{id}` and the `speech.job` event. */
export interface SpeechJob {
  id: string;
  task: "sample" | "design";
  status: "queued" | "running" | "done" | "failed" | "canceled" | string;
  target: string;
  label: string | null;
  error: { message: string; hint: string | null } | null;
  files: { key: string; url: string; index?: number }[];
  description?: string; // a design: the words it was made from
}

/** The answer to "Play sample": `ready` with the clip's address, or a job to wait for. */
export interface SampleAnswer {
  ready: boolean;
  url: string | null;
  job: SpeechJob | null;
}

/** `POST /api/speech/recordings`: a recording that passed the checks and is kept until it is saved as a voice. */
export interface StagedRecording {
  id: string;
  url: string; // the studio's own mono WAV of it, silence trimmed from both ends: what "Hear it back" plays
  seconds: number;
  peak: number; // the loudest sample as a share of full scale, 0 to 1
  clipped: number; // the share of samples at full scale, 0 to 1
  trimmed_seconds: number; // how much silence was taken from the ends (both together)
}

export type SaveVoiceBody =
  | { kind: "designed"; name: string; candidate: { job: string; index: number } }
  | { kind: "cloned"; name: string; recording: string; engine: string; consent: boolean };

/** What the `reading.piece` event carries: a piece of a reading has been made. */
export interface ReadingPieceEvent {
  run_id: string;
  part: number;
  idx: number;
  frames: number;
}
