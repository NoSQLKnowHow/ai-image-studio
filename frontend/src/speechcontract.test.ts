import { describe, expect, it } from "vitest";
import { buildReadingBody, draftFromPrepared, positionFor } from "./speech";
import type {
  EngineVoices, Passage, PartDetail, PartPiece, PreparedParagraph, PreparedPart, Prepared, PresetVoice, ReadingBody, ReadingInfo, ReadingPart, ReadingPartBody,
  ReadingParagraphBody, ReadingPosition, ReadingVoice, SampleAnswer, SampleState, SavedVoice, SpeechCaps, SpeechEngine, SpeechJob, SpeechLicence, SpeechLimits,
  StagedRecording,
} from "./speechTypes";
import type { SpeechOptionsSnapshot, SpeechRun } from "./types";
import { CAPS, SPEECH_CONTRACT } from "./testdata";

// The page and the server must say the same keys (DESIGN.md §38.9). The server's tests check that what it sends has the keys of backend/tests/speech_contract.json;
// these tests check that the page's types have exactly those keys, so a key added on either side fails one of the two (as vector.test.ts does for Vector).
//
// Each table below is typed `Record<keyof T, true>`: TypeScript itself refuses a table that misses a key of the type or has one the type has not, so the list cannot
// be wrong about the type; and the test compares the list with the contract's keys, so it cannot be wrong about the server.

type Keys<T> = Record<keyof T, true>;
const same = (table: object, sample: object) => expect(Object.keys(table).sort()).toEqual(Object.keys(sample).sort());
const C = SPEECH_CONTRACT;

const RUN: Keys<SpeechRun> = {
  id: true, status: true, prompt: true, effective_prompt: true, model_id: true, created_at: true, started_at: true, finished_at: true, error: true, pinned: true,
  expires_at: true, deleted_at: true, purge_at: true, project_id: true, prompt_ref: true, queue_position: true, progress: true, canceling: true, mode: true,
  options: true, lyrics: true, inputs: true, images: true, binned_images: true, tracks: true, binned_tracks: true, vectors: true, binned_vectors: true, hidden: true,
  reading: true,
};
const OPTIONS: Keys<SpeechOptionsSnapshot> = {
  task: true, engine: true, engine_title: true, variant: true, voice: true, style: true, language: true, source: true, title: true, pause_ms: true, licence: true,
  sample_rate: true, chars: true,
};
const VOICE: Keys<ReadingVoice> = { kind: true, id: true, label: true, gender: true, accent: true };
const LICENCE: Keys<SpeechLicence> = { name: true, noncommercial: true, notice: true };
const READING: Keys<ReadingInfo> = {
  engine: true, engine_title: true, variant: true, voice: true, style: true, language: true, source: true, licence: true, title: true, parts: true, parts_total: true,
  parts_done: true, pieces_total: true, pieces_done: true, seconds_made: true, bytes: true, sample_rate: true, waiting: true, seconds_total: true, seconds_to_go: true,
  position: true,
};
const READING_PART: Keys<ReadingPart> = { idx: true, title: true, chars: true, pieces: true, made: true, seconds: true, state: true };
const POSITION: Keys<ReadingPosition> = { part: true, piece: true, offset_ms: true };
const ENGINE: Keys<SpeechEngine> = {
  id: true, title: true, licence: true, languages: true, can_style: true, can_clone: true, can_design: true, max_chars: true, min_chars: true, sample_rate: true,
  interruptible: true, memory_gb: true, model_id: true, variants: true, preset_variant: true, clone_variant: true, design_variant: true, notes: true, state: true,
  reason: true, hint: true,
};
const CAPS_SPEECH: Keys<SpeechCaps> = { available: true, reason: true, hint: true, engines: true };
const LIMITS: Keys<SpeechLimits> = {
  max_chars: true, part_max_chars: true, upload_mb: true, fetch_mb: true, pause_ms: true, voice_seconds: true, recording_mb: true, chars_per_second: true,
};
const PREPARED: Keys<Prepared> = {
  title: true, source: true, parts: true, warnings: true, cleaning: true, chars: true, minutes: true, options: true, chapters_found: true,
};
const PREPARED_PART: Keys<PreparedPart> = { title: true, chars: true, minutes: true, include: true, note: true, paragraphs: true };
const PREPARED_PARAGRAPH: Keys<PreparedParagraph> = { kind: true, text: true, skipped: true };
const PART: Keys<PartDetail> = { idx: true, title: true, chars: true, complete: true, sample_rate: true, total_frames: true, url: true, download_url: true, pieces: true };
const PIECE: Keys<PartPiece> = { idx: true, kind: true, text: true, continues: true, status: true, frames: true, start_frame: true, error: true, url: true };
const ENGINE_VOICES: Keys<EngineVoices> = { engine: true, presets: true, saved: true };
const PRESET: Keys<PresetVoice> = { id: true, label: true, gender: true, language: true, accent: true, description: true, styleable: true, sample: true };
const SAMPLE: Keys<SampleState> = { ready: true, url: true };
const SAVED: Keys<SavedVoice> = { id: true, name: true, kind: true, engine: true, description: true, seconds: true, created_at: true, consent_at: true, sample: true };
const PASSAGE: Keys<Passage> = { text: true, sample_sentence: true, consent: true, voice_seconds: true, name_max: true, description_max: true };
const SAMPLE_ANSWER: Keys<SampleAnswer> = { ready: true, url: true, job: true };
const JOB: Keys<SpeechJob> = { id: true, task: true, status: true, target: true, label: true, error: true, files: true, description: true };
const RECORDING: Keys<StagedRecording> = { id: true, url: true, seconds: true, peak: true, clipped: true, trimmed_seconds: true };

describe("what the server sends is what the page's types say", () => {
  it("a reading, its options, the voice and licence it records", () => {
    same(RUN, C.reading_run);
    same(OPTIONS, C.reading_run.options);
    same(VOICE, C.reading_run.options.voice);
    same(LICENCE, C.reading_run.options.licence);
  });

  it("a reading's progress: the reading object, a part of it, and where the listener stopped", () => {
    same(READING, C.reading_run.reading);
    same(READING_PART, C.reading_run.reading.parts[0]);
    same(POSITION, C.reading_run.reading.position);
    same(VOICE, C.reading_run.reading.voice); // the reading object says the voice as the options do
    same(LICENCE, C.reading_run.reading.licence);
  });

  it("an engine, the engines' limits and the whole capability", () => {
    same(CAPS_SPEECH, C.capabilities_speech);
    same(ENGINE, C.capabilities_speech.engines[0]);
    same(LICENCE, C.capabilities_speech.engines[0].licence);
    same(LIMITS, C.limits_speech);
  });

  it("the review of a text, its parts and their paragraphs", () => {
    same(PREPARED, C.prepared);
    same(PREPARED_PART, C.prepared.parts[0]);
    same(PREPARED_PARAGRAPH, C.prepared.parts[0].paragraphs[0]);
  });

  it("a part with its pieces", () => {
    same(PART, C.part_detail);
    same(PIECE, C.part_detail.pieces[0]);
  });

  it("an engine's voices: presets and the person's own, each with its sample", () => {
    same(ENGINE_VOICES, C.engine_voices);
    same(PRESET, C.engine_voices.presets[0]);
    same(SAMPLE, C.engine_voices.presets[0].sample);
    same(SAVED, C.engine_voices.saved[0]);
    same(SAVED, C.saved_voice_designed); // what saving answers is what the list has
    same(SAVED, C.saved_voice_cloned);
  });

  it("the passage, a recording that passed the checks, and the answer to Play sample", () => {
    same(PASSAGE, C.passage);
    same(RECORDING, C.staged_recording);
    same(SAMPLE_ANSWER, C.sample_answer_queued);
    same(SAMPLE_ANSWER, C.sample_answer_ready);
  });

  it("a short job: a sample has no description; a design has one, and its files have an index", () => {
    const { description: _description, ...sampleKeys } = JOB;
    same(sampleKeys, C.sample_job);
    same(JOB, C.design_job);
    expect(Object.keys(C.design_job.files[0]).sort()).toEqual(["index", "key", "url"]);
    expect(Object.keys(C.sample_job.files[0]).sort()).toEqual(["key", "url"]);
    same(sampleKeys, C.sample_answer_queued.job as object);
  });

  it("the test data the other tests are built from is the contract's, in the page's types", () => {
    expect(CAPS.speech).toEqual(C.capabilities_speech as unknown as SpeechCaps);
    expect(CAPS.limits.speech).toEqual(C.limits_speech);
  });
});

describe("what the page sends is what the server accepts", () => {
  const body: ReadingBody = buildReadingBody("A book", { kind: "text" }, draftFromPrepared({ ...(C.prepared as unknown as Prepared) }), CAPS.speech.engines[0], C.engine_voices.presets[0] as unknown as PresetVoice, "", "");
  const REQUEST: Keys<ReadingBody> = { title: true, source: true, engine: true, voice: true, saved_voice: true, style: true, language: true, parts: true };
  const PART_BODY: Keys<ReadingPartBody> = { title: true, paragraphs: true };
  const PARAGRAPH_BODY: Keys<ReadingParagraphBody> = { kind: true, text: true };

  it("a request for a reading has exactly the fields of the server's model, and so does each part and each paragraph", () => {
    expect(Object.keys(body).sort()).toEqual(C.requests.reading);
    expect(Object.keys(REQUEST).sort()).toEqual(C.requests.reading);
    expect(Object.keys(body.parts[0]).sort()).toEqual(C.requests.reading_part);
    expect(Object.keys(PART_BODY).sort()).toEqual(C.requests.reading_part);
    expect(Object.keys(body.parts[0].paragraphs[0]).sort()).toEqual(C.requests.reading_paragraph);
    expect(Object.keys(PARAGRAPH_BODY).sort()).toEqual(C.requests.reading_paragraph);
  });

  it("a position has the fields of the server's model", () => {
    expect(Object.keys(positionFor(0, [], 3, 24000)).sort()).toEqual(C.requests.position);
  });

  it("the other bodies are listed by the server, so the API client's are checked against them in speechapi.test.ts", () => {
    expect(Object.keys(C.requests).sort()).toEqual(
      ["design", "piece", "position", "prepare", "reading", "reading_paragraph", "reading_part", "rename_voice", "sample", "save_voice", "save_voice_candidate"]);
  });
});
