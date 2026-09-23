// Render the original SVG with the same Chromium engine as the desktop window.
import {chromium} from '@playwright/test';
import {readFile, mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const directory = path.dirname(fileURLToPath(import.meta.url));
const assets = path.join(directory, 'assets');
await mkdir(assets, {recursive: true});
const svg = await readFile(path.join(assets, 'paperloop.svg'), 'utf8');
const browser = await chromium.launch({channel: 'msedge', headless: true});
try {
  const page = await browser.newPage({viewport: {width: 1024, height: 1024}, deviceScaleFactor: 1});
  await page.setContent(`<style>html,body{margin:0;background:transparent}svg{display:block;width:1024px;height:1024px}</style>${svg}`);
  await page.screenshot({path: path.join(assets, 'paperloop.png'), omitBackground: true});
} finally {
  await browser.close();
}
