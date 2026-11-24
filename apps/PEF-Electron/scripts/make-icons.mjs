// Generates build/icon.png (512 px, app + installer; electron-builder derives the .ico) and build/tray.png
// (32 px) from code: no binary assets committed, no extra dependency. Draws the PEF mark (teal rounded square,
// white "P" and dot) with simple anti-aliased shapes.
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { deflateSync } from 'node:zlib';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
function crc32(buf) {
  let c = 0xffffffff;
  for (const b of buf) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
}
function png(size, pixel) {
  const raw = Buffer.alloc((size * 4 + 1) * size);
  for (let y = 0; y < size; y++) {
    raw[y * (size * 4 + 1)] = 0;
    for (let x = 0; x < size; x++) {
      const [r, g, b, a] = pixel(x + 0.5, y + 0.5, size);
      const o = y * (size * 4 + 1) + 1 + x * 4;
      raw[o] = r; raw[o + 1] = g; raw[o + 2] = b; raw[o + 3] = a;
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

const clamp = (v) => Math.max(0, Math.min(1, v));
// Signed distances in a 48-unit design space (same geometry as the renderer's <PefLogo/>).
function roundedRect(px, py, x, y, w, h, r) {
  const qx = Math.abs(px - (x + w / 2)) - (w / 2 - r);
  const qy = Math.abs(py - (y + h / 2)) - (h / 2 - r);
  return Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) + Math.min(Math.max(qx, qy), 0) - r;
}
function segment(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const t = clamp(((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}
function pMark(px, py) {
  // stem 14,34 → 14,14 ; bowl 14,14 → 23,14 arc r=6 → 23,26 → 14,26  (stroke 4)
  let d = segment(px, py, 14, 34, 14, 14);
  d = Math.min(d, segment(px, py, 14, 14, 23, 14), segment(px, py, 23, 26, 14, 26));
  if (px >= 23) d = Math.min(d, Math.abs(Math.hypot(px - 23, py - 20) - 6));
  return d - 2;
}

function pixel(x, y, size) {
  const s = 48 / size;
  const px = x * s, py = y * s;
  const aa = s; // one pixel in design units
  const body = clamp(0.5 - roundedRect(px, py, 2, 2, 44, 44, 12) / aa);
  const mark = Math.max(clamp(0.5 - pMark(px, py) / aa), clamp(0.5 - (Math.hypot(px - 34, py - 31) - 4) / aa));
  const teal = [43, 179, 160], white = [255, 255, 255];
  const rgb = teal.map((c, i) => Math.round(c * (1 - mark) + white[i] * mark));
  return [...rgb, Math.round(255 * body)];
}

mkdirSync(join(root, 'build'), { recursive: true });
writeFileSync(join(root, 'build', 'icon.png'), png(512, pixel));
writeFileSync(join(root, 'build', 'tray.png'), png(32, pixel));
console.log('icons written to build/');
