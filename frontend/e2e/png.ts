// A tiny PNG encoder for the browser tests: the server decodes every upload for real, so the files must be valid.
import { deflateSync } from "node:zlib";

const TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});

function crc32(bytes: Buffer): number {
  let c = 0xffffffff;
  for (const b of bytes) c = TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type: string, data: Buffer): Buffer {
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const out = Buffer.alloc(8 + data.length + 4);
  out.writeUInt32BE(data.length, 0);
  body.copy(out, 4);
  out.writeUInt32BE(crc32(body), 8 + data.length);
  return out;
}

/** A solid-colour PNG. With `alpha` below 255 it is RGBA, so the page has a transparent image to show. */
export function png(width: number, height: number, [r, g, b]: [number, number, number], alpha = 255): Buffer {
  const rgba = alpha !== 255;
  const channels = rgba ? 4 : 3;
  const row = Buffer.alloc(1 + width * channels);
  for (let x = 0; x < width; x++) {
    const at = 1 + x * channels;
    row[at] = r;
    row[at + 1] = g;
    row[at + 2] = b;
    if (rgba) row[at + 3] = alpha;
  }
  const raw = Buffer.concat(Array.from({ length: height }, () => row));
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8; // bit depth
  header[9] = rgba ? 6 : 2; // colour type
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

export const RED: [number, number, number] = [220, 50, 47];
export const GREEN: [number, number, number] = [40, 160, 90];
export const BLUE: [number, number, number] = [50, 90, 200];
export const GOLD: [number, number, number] = [230, 170, 30];
