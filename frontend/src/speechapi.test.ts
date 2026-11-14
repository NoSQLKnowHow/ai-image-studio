import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, toApiError } from "./api";
import { partAudioUrl, partDownloadUrl, pieceAudioUrl, speechApi, zipUrl } from "./speechApi";
import { SPEECH_CONTRACT } from "./testdata";

// The Speech tab's calls (DESIGN.md §38.9): which address, which method, what is sent, and how a refusal reads. `fetch` is replaced, so nothing leaves the test.

type Call = { url: string; init: RequestInit & { headers: Record<string, string> } };

/** Replace `fetch` with one that answers `body` with `status`, and return what it was asked. */
function answer(body: unknown, status = 200): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: Call["init"]) => {
    calls.push({ url, init });
    return status === 204 ? new Response(null, { status }) : new Response(typeof body === "string" ? body : JSON.stringify(body), { status });
  }));
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("the Speech tab's calls", () => {
  it("preparing pasted text posts the body as JSON, with the header every mutation carries", async () => {
    const calls = answer(SPEECH_CONTRACT.prepared);
    const result = await speechApi.prepare({ source: "text", text: "Hello there", clean: true, read_extras: false });
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/speech/prepare");
    expect(calls[0].init.method).toBe("POST");
    expect(JSON.parse(calls[0].init.body as string)).toEqual({ source: "text", text: "Hello there", clean: true, read_extras: false });
    expect(calls[0].init.headers).toMatchObject({ "X-Studio-Client": "1", "Content-Type": "application/json", Accept: "application/json" });
    expect(result.title).toBe(SPEECH_CONTRACT.prepared.title);
  });

  it("preparing a web address says so, with the address", async () => {
    const calls = answer(SPEECH_CONTRACT.prepared);
    await speechApi.prepare({ source: "url", url: "https://example.org/a" });
    expect(JSON.parse(calls[0].init.body as string)).toEqual({ source: "url", url: "https://example.org/a" });
  });

  it("preparing a file sends the file itself as the body, with its name and the two switches in the query, and no content type of its own", async () => {
    const calls = answer(SPEECH_CONTRACT.prepared);
    const file = new File(["the book"], "my book & more.epub", { type: "application/epub+zip" });
    await speechApi.prepareFile(file, { clean: false, read_extras: true });
    expect(calls[0].url).toBe("/api/speech/prepare/file?name=my+book+%26+more.epub&clean=false&read_extras=true");
    expect(calls[0].init.method).toBe("POST");
    expect(calls[0].init.body).toBe(file);
    expect(calls[0].init.headers["X-Studio-Client"]).toBe("1");
    expect(Object.keys(calls[0].init.headers).map((k) => k.toLowerCase())).not.toContain("content-type"); // the server judges by the name and the bytes
  });

  it("stopping an upload is not reported as a failure: the abort comes through as it is", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new DOMException("aborted", "AbortError"); }));
    await expect(speechApi.prepareFile(new File(["x"], "a.txt"), { clean: true, read_extras: false })).rejects.toMatchObject({ name: "AbortError" });
  });

  it("a server that cannot be reached says so, as ApiError with status 0", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("network"); }));
    const error = await speechApi.passage().catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 0, message: "Can't reach the studio server." });
    const upload = await speechApi.uploadRecording(new Blob(["x"])).catch((e) => e);
    expect(upload).toMatchObject({ status: 0 });
  });

  it("making a reading, getting it, continuing it", async () => {
    let calls = answer(SPEECH_CONTRACT.reading_run, 201);
    const body = { title: "T", source: { kind: "text" }, engine: "fake", voice: "anna", saved_voice: null, style: null, language: null, speakers: [], parts: [] };
    await speechApi.makeReading(body);
    expect([calls[0].url, calls[0].init.method, JSON.parse(calls[0].init.body as string)]).toEqual(["/api/readings", "POST", body]);

    calls = answer(SPEECH_CONTRACT.reading_run);
    await speechApi.reading("abc");
    expect([calls[0].url, calls[0].init.method]).toEqual(["/api/readings/abc", "GET"]);
    expect(calls[0].init.headers["X-Studio-Client"]).toBeUndefined(); // only a change carries it

    calls = answer(SPEECH_CONTRACT.reading_run);
    await speechApi.continueReading("abc");
    expect([calls[0].url, calls[0].init.method, calls[0].init.body]).toEqual(["/api/readings/abc/continue", "POST", undefined]);
  });

  it("getting a part, correcting a piece, saving a position", async () => {
    let calls = answer(SPEECH_CONTRACT.part_detail);
    await speechApi.part("abc", 3);
    expect(calls[0].url).toBe("/api/readings/abc/parts/3");

    calls = answer({ idx: 2, text: "new" });
    await speechApi.editPiece("abc", 3, 2, "new");
    expect([calls[0].url, calls[0].init.method, JSON.parse(calls[0].init.body as string)]).toEqual(["/api/readings/abc/parts/3/pieces/2", "PATCH", { text: "new" }]);

    calls = answer(null, 204);
    const saved = await speechApi.savePosition("abc", { part: 1, piece: 2, offset_ms: 3000 });
    expect([calls[0].url, calls[0].init.method, JSON.parse(calls[0].init.body as string)]).toEqual(["/api/readings/abc/position", "PUT", { part: 1, piece: 2, offset_ms: 3000 }]);
    expect(saved).toBeUndefined(); // 204: nothing to read
  });

  it("an engine's voices are asked for by the engine's id, made safe for a path", async () => {
    const calls = answer(SPEECH_CONTRACT.engine_voices);
    await speechApi.voices("qwen3-tts");
    await speechApi.voices("odd/id?");
    expect(calls.map((c) => c.url)).toEqual(["/api/speech/engines/qwen3-tts/voices", "/api/speech/engines/odd%2Fid%3F/voices"]);
  });

  it("the passage, a sample, a job", async () => {
    let calls = answer(SPEECH_CONTRACT.passage);
    await speechApi.passage();
    expect(calls[0].url).toBe("/api/speech/passage");

    calls = answer(SPEECH_CONTRACT.sample_answer_queued, 202);
    const result = await speechApi.sample({ engine: "fake", voice: "anna" });
    expect([calls[0].url, calls[0].init.method, JSON.parse(calls[0].init.body as string)]).toEqual(["/api/speech/samples", "POST", { engine: "fake", voice: "anna" }]);
    expect(result.ready).toBe(false);

    calls = answer(SPEECH_CONTRACT.sample_job);
    await speechApi.job("j1");
    expect(calls[0].url).toBe("/api/speech/jobs/j1");
  });

  it("designing a voice sends the description, and the engine only when one is named", async () => {
    let calls = answer(SPEECH_CONTRACT.design_job, 202);
    await speechApi.design("a warm voice");
    expect(JSON.parse(calls[0].init.body as string)).toEqual({ description: "a warm voice" });
    expect(calls[0].url).toBe("/api/speech/voices/design");

    calls = answer(SPEECH_CONTRACT.design_job, 202);
    await speechApi.design("a warm voice", "fake");
    expect(JSON.parse(calls[0].init.body as string)).toEqual({ description: "a warm voice", engine: "fake" });
  });

  it("a recording is sent as the body, as a file is", async () => {
    const calls = answer(SPEECH_CONTRACT.staged_recording, 201);
    const blob = new Blob(["audio"], { type: "audio/webm" });
    const staged = await speechApi.uploadRecording(blob);
    expect([calls[0].url, calls[0].init.method, calls[0].init.body]).toEqual(["/api/speech/recordings", "POST", blob]);
    expect(Object.keys(calls[0].init.headers).map((k) => k.toLowerCase())).not.toContain("content-type");
    expect(staged.id).toBe(SPEECH_CONTRACT.staged_recording.id);
  });

  it("saving, renaming and deleting a voice", async () => {
    let calls = answer(SPEECH_CONTRACT.saved_voice_designed, 201);
    await speechApi.saveVoice({ kind: "designed", name: "Warm", candidate: { job: "j1", index: 1 } });
    expect([calls[0].url, calls[0].init.method, JSON.parse(calls[0].init.body as string)]).toEqual(
      ["/api/speech/voices", "POST", { kind: "designed", name: "Warm", candidate: { job: "j1", index: 1 } }]);

    calls = answer(SPEECH_CONTRACT.saved_voice_cloned, 201);
    await speechApi.saveVoice({ kind: "cloned", name: "Mine", recording: "r1", engine: "fake", consent: true });
    expect(JSON.parse(calls[0].init.body as string)).toEqual({ kind: "cloned", name: "Mine", recording: "r1", engine: "fake", consent: true });

    calls = answer(SPEECH_CONTRACT.saved_voice_cloned);
    await speechApi.renameVoice("v1", "Better");
    expect([calls[0].url, calls[0].init.method, JSON.parse(calls[0].init.body as string)]).toEqual(["/api/speech/voices/v1", "PATCH", { name: "Better" }]);

    calls = answer(null, 204);
    expect(await speechApi.deleteVoice("v1")).toBeUndefined();
    expect([calls[0].url, calls[0].init.method]).toEqual(["/api/speech/voices/v1", "DELETE"]);
  });

  it("every body it builds has fields the server's model has (no more)", async () => {
    const known = SPEECH_CONTRACT.requests;
    const calls = answer(SPEECH_CONTRACT.prepared);
    await speechApi.prepare({ source: "text", text: "x", clean: true, read_extras: true });
    expect(Object.keys(JSON.parse(calls[0].init.body as string)).every((k) => known.prepare.includes(k))).toBe(true);

    const sample = answer(SPEECH_CONTRACT.sample_answer_ready);
    await speechApi.sample({ engine: "fake", voice: "anna", saved_voice: null, recording: null });
    expect(Object.keys(JSON.parse(sample[0].init.body as string)).every((k) => known.sample.includes(k))).toBe(true);

    const design = answer(SPEECH_CONTRACT.design_job, 202);
    await speechApi.design("x", "fake");
    expect(Object.keys(JSON.parse(design[0].init.body as string)).sort()).toEqual(known.design);

    const piece = answer({ idx: 0, text: "x" });
    await speechApi.editPiece("a", 0, 0, "x");
    expect(Object.keys(JSON.parse(piece[0].init.body as string))).toEqual(known.piece);

    const rename = answer(SPEECH_CONTRACT.saved_voice_cloned);
    await speechApi.renameVoice("v", "x");
    expect(Object.keys(JSON.parse(rename[0].init.body as string))).toEqual(known.rename_voice);

    const save = answer(SPEECH_CONTRACT.saved_voice_cloned, 201);
    await speechApi.saveVoice({ kind: "cloned", name: "x", recording: "r", engine: "fake", consent: true });
    expect(Object.keys(JSON.parse(save[0].init.body as string)).every((k) => known.save_voice.includes(k))).toBe(true);
    const saveDesigned = answer(SPEECH_CONTRACT.saved_voice_designed, 201);
    await speechApi.saveVoice({ kind: "designed", name: "x", candidate: { job: "j", index: 0 } });
    const sent = JSON.parse(saveDesigned[0].init.body as string);
    expect(Object.keys(sent).every((k) => known.save_voice.includes(k))).toBe(true);
    expect(Object.keys(sent.candidate).sort()).toEqual(known.save_voice_candidate);
  });

  it("loading an engine names it, and the variant only when one is given; unloading names the speech model", async () => {
    let calls = answer({});
    await speechApi.loadEngine("qwen3-tts");
    expect([calls[0].url, JSON.parse(calls[0].init.body as string)]).toEqual(["/api/model/load", { model: "speech", engine: "qwen3-tts" }]);

    calls = answer({});
    await speechApi.loadEngine("qwen3-tts", "clone");
    expect(JSON.parse(calls[0].init.body as string)).toEqual({ model: "speech", engine: "qwen3-tts", variant: "clone" });

    calls = answer({});
    await speechApi.unloadEngine();
    expect([calls[0].url, JSON.parse(calls[0].init.body as string)]).toEqual(["/api/model/unload", { model: "speech" }]);
  });
});

describe("the addresses the page puts in <audio> and <a download>", () => {
  it("are the server's routes", () => {
    expect(partAudioUrl("r1", 2)).toBe("/api/speech/r1/parts/2.wav");
    expect(partDownloadUrl("r1", 2)).toBe("/api/speech/r1/parts/2.wav?download=1");
    expect(pieceAudioUrl("r1", 2, 7)).toBe("/api/speech/r1/parts/2/pieces/7.wav");
    expect(zipUrl("r1")).toBe("/api/speech/r1/download.zip");
  });

  it("are the same as the ones the server puts in a part's detail", () => {
    const detail = SPEECH_CONTRACT.part_detail;
    const id = detail.url.split("/")[3];
    expect(partAudioUrl(id, detail.idx)).toBe(detail.url);
    expect(partDownloadUrl(id, detail.idx)).toBe(detail.download_url);
    expect(pieceAudioUrl(id, detail.idx, 0)).toBe(detail.pieces[0].url);
  });
});

describe("how a refusal reads", () => {
  it("is the server's own words, its hint, its code and the field it is about", () => {
    const error = toApiError(422, { detail: "Tick the statement first.", code: "consent_required", hint: "It must be your own voice.", field: "consent" });
    expect(error).toMatchObject({ message: "Tick the statement first.", status: 422, code: "consent_required", hint: "It must be your own voice." });
    expect(error.fieldErrors).toEqual([{ field: "consent", message: "Tick the statement first." }]);
  });

  it("has no field error when the server names no field, as for every other tab", () => {
    expect(toApiError(409, { detail: "Not now.", code: "busy" }).fieldErrors).toEqual([]);
    expect(toApiError(409, { detail: "Not now.", field: "" }).fieldErrors).toEqual([]);
    expect(toApiError(409, { detail: "Not now.", field: 5 }).fieldErrors).toEqual([]);
  });

  it("still reads the framework's own list of field errors as before", () => {
    const error = toApiError(422, { detail: [{ loc: ["body", "name"], msg: "String too long" }] });
    expect(error.fieldErrors).toEqual([{ field: "name", message: "String too long" }]);
    expect(error.message).toBe("String too long");
  });

  it("comes through a call as an ApiError carrying the field", async () => {
    answer({ detail: "A voice needs a name.", code: "name_empty", field: "name" }, 422);
    const error = await speechApi.renameVoice("v1", "").catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.fieldErrors).toEqual([{ field: "name", message: "A voice needs a name." }]);
  });

  it("is 'The server answered N' when the body is not JSON", async () => {
    answer("<html>oops</html>", 500);
    const error = await speechApi.passage().catch((e) => e);
    expect(error).toMatchObject({ status: 500, message: "The server answered 500." });
    answer("not json", 502);
    expect(await speechApi.prepareFile(new File(["x"], "a.txt"), { clean: true, read_extras: false }).catch((e) => e)).toMatchObject({ status: 502, message: "The server answered 502." });
  });

  it("a refusal of an upload carries the field too", async () => {
    answer({ detail: "That recording is too short.", code: "too_short", field: "recording", hint: "Read the whole passage." }, 422);
    const error = await speechApi.uploadRecording(new Blob(["x"])).catch((e) => e);
    expect(error).toMatchObject({ status: 422, code: "too_short", hint: "Read the whole passage." });
    expect(error.fieldErrors).toEqual([{ field: "recording", message: "That recording is too short." }]);
  });
});
