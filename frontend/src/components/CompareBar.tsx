import { BASKET_MAX } from "../variations";
import { CompareIcon } from "./icons";

/** The compare basket's bar (DESIGN.md section 43.5): shown while the basket holds anything, fixed at the foot of the page. Compare opens the compare view once there are two pictures; Clear
 *  empties the basket. The basket lives in the page and is gone on a reload. */
export function CompareBar({ count, onCompare, onClear }: { count: number; onCompare: () => void; onClear: () => void }) {
  if (count === 0) return null;
  return (
    <div className="compare-basket" role="region" aria-label="Compare basket" data-compare-basket>
      <CompareIcon />
      <span className="compare-basket-text" role="status">Compare · {count} chosen{count < BASKET_MAX ? ". Choose one more picture in the viewer." : ""}</span>
      <button type="button" className="button small primary" data-action="compare-open" disabled={count < BASKET_MAX} onClick={onCompare}>Compare</button>
      <button type="button" className="button small ghost" data-action="compare-clear" onClick={onClear}>Clear</button>
    </div>
  );
}
