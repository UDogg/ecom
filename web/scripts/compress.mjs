// Precompresses every text file of a build with Brotli (quality 11) and gzip (level 9), so the
// BFF serves .br/.gz from memory and never compresses per request.
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { brotliCompressSync, constants, gzipSync } from "node:zlib";

const root = process.argv[2] ?? "dist";
const text = /\.(html|js|mjs|css|svg|json|webmanifest|txt|xml)$/;
let saved = 0;
const walk = (dir) => {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) { walk(path); continue; }
    if (!text.test(name)) continue;
    const raw = readFileSync(path);
    const br = brotliCompressSync(raw, { params: { [constants.BROTLI_PARAM_QUALITY]: 11, [constants.BROTLI_PARAM_SIZE_HINT]: raw.length } });
    const gz = gzipSync(raw, { level: 9 });
    if (br.length < raw.length) writeFileSync(path + ".br", br);
    if (gz.length < raw.length) writeFileSync(path + ".gz", gz);
    saved += raw.length - Math.min(br.length, raw.length);
  }
};
walk(root);
console.log(`precompressed ${root}: ${(saved / 1024).toFixed(1)} KiB saved with brotli`);
