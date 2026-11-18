import { chromium } from 'playwright-core';
import { readFile, access } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join, basename } from 'node:path';
import { spawn } from 'node:child_process';
import { once } from 'node:events';

const root = dirname(fileURLToPath(import.meta.url));
const [filmPath, output, widthArg, fpsArg = '24'] = process.argv.slice(2);
const film = JSON.parse(await readFile(filmPath, 'utf8'));
if (!/^[\w-]+\.js$/.test(film.module)) throw Error('Nombre de módulo inválido');
const portrait = film.aspect === '9:16';
const width = Number(widthArg ?? (portrait ? 864 : 1280));
const height = Math.round(width * (portrait ? 16 / 9 : 9 / 16) / 2) * 2, fps = Number(fpsArg);
if (![width, fps].every(n => Number.isSafeInteger(n) && n > 0) || width % 2 ||
    !(film.duration > 0 && Number.isSafeInteger(film.duration * fps))) throw Error('Formato de video inválido');

const files = new Map([
    ['/scene.js', join(dirname(filmPath), basename(film.module))],
    ...['three.module.js','three.core.js'].map(name => ['/' + name, join(root, 'node_modules/three/build', name)])
]);
const launch = { args: ['--enable-unsafe-swiftshader'] };
if (process.env.THREE_CHROME) launch.executablePath = process.env.THREE_CHROME;
else {
    try { await access(chromium.executablePath()); }
    catch { launch.channel = 'chrome'; }
}
if (!launch.channel && !launch.executablePath) launch.executablePath = chromium.executablePath();
const browser = await chromium.launch(launch);
let encoder;
try {
    const page = await browser.newPage({viewport: {width, height}});
    await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'http://film.local') return route.abort();
        if (url.pathname === '/') return route.fulfill({contentType:'text/html', body:
            '<script type="importmap">{"imports":{"three":"/three.module.js"}}</script><canvas></canvas>'});
        const path = files.get(url.pathname) ?? (/^\/[\w-]+\.js$/.test(url.pathname) ? join(dirname(filmPath),url.pathname.slice(1)) : null);
        if (!path) return route.abort();
        const body = await readFile(path).catch(() => null);
        return body ? route.fulfill({contentType:'text/javascript',body}) : route.abort();
    });
    await page.goto('http://film.local');
    await page.evaluate(async ({width,height,film}) => {
        const {createFilm} = await import('/scene.js');
        const canvas = document.querySelector('canvas');
        canvas.width = width; canvas.height = height;
        const draw = await createFilm(canvas,film);
        window.capture = t => { draw(t); return canvas.toDataURL('image/jpeg',0.96).split(',')[1]; };
    }, {width,height,film});
    encoder = spawn('ffmpeg',['-hide_banner','-loglevel','error','-y','-f','image2pipe','-framerate',String(fps),
        '-c:v','mjpeg','-i','pipe:0','-c:v','libx264','-preset','fast','-crf','17','-pix_fmt','yuv420p','-movflags','+faststart',output],
        {stdio:['pipe','ignore','inherit']});
    const finished = once(encoder,'close');
    encoder.stdin.on('error', () => {});
    for (let i=0; i<film.duration*fps; i++) {
        const jpeg = await page.evaluate(t => window.capture(t), i/fps);
        await new Promise((resolve,reject) => encoder.stdin.write(Buffer.from(jpeg,'base64'), e => e ? reject(e) : resolve()));
        if (i % (fps*5) === 0) console.log('Three.js: ' + i/fps + ' / ' + film.duration + ' s');
    }
    encoder.stdin.end();
    if ((await finished)[0] !== 0) throw Error('FFmpeg no completó el video');
} finally {
    encoder?.kill();
    await browser.close();
}
