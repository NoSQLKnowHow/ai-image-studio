import type { VectorItem, VectorRun } from "../types";
import { coloursText, isStacked, layerHeight, lengthText, smallestFeatureText } from "../vector";
import { AlertIcon } from "./icons";
import { Swatch } from "./Swatch";

// What the clean-up found for one version of a Vector run: the colours found against the colours asked, the table of layers, the smallest feature and the
// notes (DESIGN.md §31.12.8, §35.2). It is shown in the viewer for every version, and on the card of a run with exactly one version, where there is no
// grid of several to hold it apart from; a run with several keeps its card short and shows these only in the viewer.

/** The table of a version's layers: swatch and name, how many paths, and the cut length (a rough idea of the time). A version made Stacked (depth) has a
 *  fourth column, Height: how many sheets high the colour stands (§35.2). When the picture's own colour differs from the layer's (always, in shape mode,
 *  where a layer's colour is a label from a fixed list: §31.10 item 7) it is shown under the name. */
export function LayerTable({ item }: { item: VectorItem }) {
  if (item.check.layers.length === 0) return null;
  const stacked = isStacked(item.check);
  return (
    <div className="vector-layers-wrap" role="region" aria-label="Layers" tabIndex={0}>
      <table className="vector-layers">
        <thead>
          <tr>
            <th scope="col">Layer</th>
            <th scope="col" className="num">Paths</th>
            <th scope="col" className="num">Cut length</th>
            {stacked && <th scope="col" className="num" data-column="height" title="How many sheets high this colour stands: the back sheet is 1">Height</th>}
          </tr>
        </thead>
        <tbody>
          {item.check.layers.map((layer) => (
            <tr key={layer.id} data-layer-id={layer.id}>
              <th scope="row">
                <span className="layer-name"><Swatch colour={layer.colour} /> {layer.name}</span>
                <span className="layer-colour">
                  {layer.colour}
                  {layer.picture_colour !== layer.colour && <> · in the picture <Swatch colour={layer.picture_colour} small /> {layer.picture_colour}</>}
                </span>
              </th>
              <td className="num">{layer.paths}</td>
              <td className="num">{lengthText(layer.length_mm)}</td>
              {stacked && <td className="num" data-height>{layerHeight(layer, item.check)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The facts about a version, in the order the card has always shown them. */
export function VersionFacts({ run, item }: { run: VectorRun; item: VectorItem }) {
  const check = item.check;
  return (
    <>
      <p className="vector-colours" data-vector-colours>{coloursText(check)}</p>
      <LayerTable item={item} />
      <p className="vector-feature" data-vector-feature>{smallestFeatureText(check.smallest_feature_mm, run.options.min_feature_mm)}</p>
      {check.notes.length > 0 && (
        <ul className="vector-notes" aria-label="Notes">
          {check.notes.map((note, i) => (
            <li key={`${note.code}-${i}`} data-note-code={note.code}><AlertIcon /> <span>{note.message}</span></li>
          ))}
        </ul>
      )}
    </>
  );
}
