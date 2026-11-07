import { useId, useState } from "react";
import type { VectorItem } from "../types";
import { stackCaption } from "../vector";
import { Swatch } from "./Swatch";

// The stack view of a version made Stacked (depth) (DESIGN.md §35.2): the sheets drawn one above another in depth, back to front, each in its own
// colour and a little offset from the one behind it with a soft shadow, with its number and how many sheets high its colour stands, and a control that
// peels the stack from the front so that the piece can be watched building up.
//
// The sheets are the layers' own SVGs (`layer_urls`, one request each), shown in <img>s as the combined SVG is: a file that came from a model never runs as
// part of the page (§31.6 "Safety"). The depth is by position and shadow only; this is not a 3-D model (§35.5 item 16).

/** How far each sheet in front of the one behind it is moved, in pixels, up and to the right. Smaller on a phone through the stage's own CSS. */
const STEP_PX = 12;

export function StackView({ item }: { item: VectorItem }) {
  const id = useId();
  const count = item.layer_urls.length;
  // how many sheets from the back are shown: all of them to begin with. The caller keys this component by the version, so a new version starts with all.
  const [shown, setShown] = useState(count);
  const [wide, tall] = item.check.size_mm;
  const layers = item.check.layers;
  return (
    <section className="stack-view" aria-label="Stack view" data-stack-view>
      <h3 className="vv-heading">Stack view</h3>
      <p className="field-hint">The sheets as they stack, back to front. The back sheet is the whole design; each sheet in front of it holds less.</p>
      {/* the stage is as wide as the picture and has room for the offset of every sheet (--sheets) at its top and right */}
      <div className="stack-stage" style={{ ["--sheets" as string]: count, ["--step" as string]: `${STEP_PX}px` }}>
        <div className="stack-frame" style={{ aspectRatio: `${wide} / ${tall}` }} role="group" aria-label={`${shown} of ${count} sheets shown`}>
          {item.layer_urls.slice(0, shown).map((url, i) => {
            const layer = layers[i];
            return (
              <div key={url} className="stack-sheet" data-sheet={i + 1}
                style={{ transform: `translate(calc(${i} * var(--step)), calc(${i} * var(--step) * -1))`, zIndex: i + 1 }}>
                <img src={url} alt={layer ? stackCaption(layer, item.check, count) : `Layer ${i + 1}`} loading="lazy" />
              </div>
            );
          })}
        </div>
      </div>
      <div className="stack-control">
        <label htmlFor={`${id}-shown`}>Sheets shown</label>
        <input id={`${id}-shown`} type="range" min={1} max={count} step={1} value={shown} data-stack-shown aria-valuetext={`${shown} of ${count}`}
          onChange={(e) => setShown(Number(e.target.value))} />
        <output htmlFor={`${id}-shown`} data-stack-shown-text>{shown} of {count}</output>
      </div>
      <ol className="stack-captions" aria-label="Sheets, back to front">
        {layers.map((layer, i) => (
          <li key={layer.id} data-sheet-caption={layer.index} className={i < shown ? undefined : "hidden-sheet"}>
            <Swatch colour={layer.colour} /> <span>{stackCaption(layer, item.check, count)}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
