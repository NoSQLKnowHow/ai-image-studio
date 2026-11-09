import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createEmitter } from "./emitter";
import { waitForJob } from "./speechJobs";
import type { SpeechJob } from "./speechTypes";
import { SPEECH_CONTRACT } from "./testdata";

// Waiting for a sample or a voice design to finish (DESIGN.md §38.4): told by the `speech.job` event, or found by asking every few seconds in case an event was missed.

const job = (status: SpeechJob["status"], id = "j1"): SpeechJob => ({ ...(SPEECH_CONTRACT.sample_job as unknown as SpeechJob), id, status });
const settle = async () => {
  for (let i = 0; i < 6; i++) await Promise.resolve();
};

function setup(first: SpeechJob = job("queued"), extra: { wanted?: () => boolean } = {}) {
  const jobs = createEmitter<SpeechJob>();
  const api = { job: vi.fn(async (_id: string) => job("queued")) };
  const wait = waitForJob(first, { jobs, api, pollMs: 2000, ...extra });
  const result: { value: SpeechJob | null | undefined } = { value: undefined };
  void wait.done.then((v) => void (result.value = v));
  return { jobs, api, wait, result };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("waiting for a job", () => {
  it("is over at once for a job that has already finished, however it finished", async () => {
    for (const status of ["done", "failed", "canceled"]) {
      const t = setup(job(status));
      await settle();
      expect(t.result.value?.status).toBe(status);
      expect(t.api.job).not.toHaveBeenCalled();
    }
  });

  it("is over when the event says the job is done, and listens to nothing after", async () => {
    const t = setup();
    t.jobs.emit(job("done"));
    await settle();
    expect(t.result.value?.status).toBe("done");
    await vi.advanceTimersByTimeAsync(10_000);
    expect(t.api.job).not.toHaveBeenCalled(); // and it stopped asking
  });

  it("is over when the event says it failed or was canceled", async () => {
    const t = setup();
    t.jobs.emit(job("failed"));
    await settle();
    expect(t.result.value?.status).toBe("failed");
  });

  it("takes no notice of a job that is another's, or that has only moved on", async () => {
    const t = setup();
    t.jobs.emit(job("done", "another"));
    t.jobs.emit(job("running"));
    await settle();
    expect(t.result.value).toBeUndefined();
  });

  it("asks every few seconds in case the event was missed, and is over when the answer says so", async () => {
    const t = setup();
    await vi.advanceTimersByTimeAsync(1_999);
    expect(t.api.job).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(t.api.job).toHaveBeenCalledWith("j1");
    expect(t.result.value).toBeUndefined(); // still queued
    t.api.job.mockResolvedValue(job("done"));
    await vi.advanceTimersByTimeAsync(2_000);
    expect(t.result.value?.status).toBe("done");
    await vi.advanceTimersByTimeAsync(10_000);
    expect(t.api.job).toHaveBeenCalledTimes(2); // no more asking once it is over
  });

  it("asks again at the next time when a poll fails", async () => {
    const t = setup();
    t.api.job.mockRejectedValueOnce(new Error("offline"));
    await vi.advanceTimersByTimeAsync(2_000);
    expect(t.result.value).toBeUndefined();
    t.api.job.mockResolvedValue(job("failed"));
    await vi.advanceTimersByTimeAsync(2_000);
    expect(t.result.value?.status).toBe("failed");
  });

  it("can be given up: the answer is nothing, and it neither listens nor asks any more", async () => {
    const t = setup();
    t.wait.cancel();
    await settle();
    expect(t.result.value).toBeNull();
    t.jobs.emit(job("done"));
    await vi.advanceTimersByTimeAsync(10_000);
    expect(t.api.job).not.toHaveBeenCalled();
    t.wait.cancel(); // twice is no harm
  });

  it("keeps its answer when it is given up after it was over", async () => {
    const t = setup();
    t.jobs.emit(job("done"));
    await settle();
    t.wait.cancel();
    await settle();
    expect(t.result.value?.status).toBe("done");
  });

  it("is given up at the next poll when what it was for is no longer wanted", async () => {
    let wanted = true;
    const t = setup(job("queued"), { wanted: () => wanted });
    await vi.advanceTimersByTimeAsync(2_000);
    expect(t.api.job).toHaveBeenCalledTimes(1);
    wanted = false;
    await vi.advanceTimersByTimeAsync(2_000);
    expect(t.result.value).toBeNull();
    expect(t.api.job).toHaveBeenCalledTimes(1); // not asked again for something nobody waits for
  });
});
