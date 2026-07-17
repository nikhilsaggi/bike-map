// Minimal static server for docs/ used by the Playwright webServer hook.
// rides.geojson.gz is normally intercepted per-test with a synthetic
// fixture; this just serves index.html (and the real .gz as a fallback).
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const DOCS = resolve(fileURLToPath(new URL('../../docs', import.meta.url)));
const PORT = 8917;
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.gz': 'application/gzip',
  '.png': 'image/png',
};

createServer(async (req, res) => {
  const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
  const rel = pathname === '/' ? 'index.html' : pathname.slice(1);
  const file = resolve(DOCS, rel);
  if (file !== DOCS && !file.startsWith(DOCS + sep)) {
    res.writeHead(403);
    res.end('forbidden');
    return;
  }
  try {
    const body = await readFile(file);
    res.writeHead(200, { 'content-type': MIME[extname(file)] ?? 'application/octet-stream' });
    res.end(body);
  } catch {
    res.writeHead(404);
    res.end('not found');
  }
}).listen(PORT, '127.0.0.1');
