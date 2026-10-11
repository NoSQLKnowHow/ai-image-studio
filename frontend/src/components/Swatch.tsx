import { normaliseColour } from "../vector";

/** A colour as a small square, with the border that lets white (the bare material) be seen on a white card. Only a clean #RRGGBB reaches the style, so a
 *  value the server never sends could not carry anything else into it. `unset` is the empty square of an ink the picture chooses. It is decoration: the colour's
 *  name or hex is always written beside it. */
export function Swatch({ colour, small = false, unset = false }: { colour?: string; small?: boolean; unset?: boolean }) {
  const valid = colour === undefined ? null : normaliseColour(colour);
  return <span className={`swatch${small ? " small" : ""}${unset ? " unset" : ""}`} style={valid ? { background: valid } : undefined} aria-hidden="true" />;
}
