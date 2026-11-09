import type { Capabilities } from "../types";
import { useSpeechSource } from "../useSpeechSource";
import { panelId, tabId } from "./Tabs";
import { SpeechReview } from "./SpeechReview";
import { SpeechSourceBox } from "./SpeechSource";

// The Speech tab (DESIGN.md §38.2): text in, read aloud in a voice. This file is the tab's frame: it says whether speech can work here at all, and otherwise shows the
// source of the text and the review made of it. The voice, the button that makes the reading and the readings themselves are added to it as they are built.

export interface SpeechPanelProps {
  hidden: boolean;
  caps: Capabilities;
}

export function SpeechPanel({ hidden, caps }: SpeechPanelProps) {
  const source = useSpeechSource(caps.limits.speech);
  const speech = caps.speech;
  const checking = !speech.available && speech.engines.some((engine) => engine.state === "checking");

  return (
    <div className="tab-panel speech-panel" role="tabpanel" id={panelId("speech")} aria-labelledby={tabId("speech")} hidden={hidden}>
      {!speech.available ? (
        <section className="speech-unavailable" aria-label="Speech is not available">
          <h2>{checking ? "Checking the speech engines…" : "Speech isn't available on this server"}</h2>
          {checking ? (
            <p role="status">Each engine is being checked. This takes a moment after the server starts.</p>
          ) : (
            <>
              <p role="status">{speech.reason ?? "No speech engine can run on this server as it is set up."}</p>
              {speech.hint && <p className="hint">{speech.hint}</p>}
            </>
          )}
          <p className="hint">Images, music and vector work as usual.</p>
        </section>
      ) : (
        <>
          <SpeechSourceBox source={source} limits={caps.limits.speech} />
          <SpeechReview source={source} limits={caps.limits.speech} />
        </>
      )}
    </div>
  );
}
