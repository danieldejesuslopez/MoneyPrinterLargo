# Animación con Three.js

Con el entorno Python de MoneyPrinter activo, instala el motor de render desde la raíz del proyecto. Requiere Node.js, FFmpeg y FFprobe en PATH.

```sh
npm ci --prefix src/three_video
```

El render usa Chromium de Playwright si está instalado, o Chrome de escritorio. También puedes indicar la ruta al ejecutable de Chromium mediante la variable de entorno `THREE_CHROME`.

```sh
python src/main.py --three
```

La opción 7 del menú genera el mismo corto. El archivo final se guarda en `output/three_jardin/el-jardin-de-laton.mp4`. La voz del ejemplo usa Edge TTS con conexión a Internet y la música se sintetiza localmente.

Para crear otro corto, copia el manifiesto y el módulo de `src/three_video/films/`. El manifiesto indica la duración en segundos, la voz y las frases con su segundo de entrada. La música de caja se activa con `"score": "waltz"`. El módulo JavaScript exporta `createFilm(canvas)`, que devuelve una función para dibujar el instante solicitado en segundos. Puede importar Three.js como `three`. Cada fotograma se calcula a partir de ese tiempo a 24 fps.

```sh
python src/main.py --three --film ruta/corto.json --output output/mi-corto.mp4
```

Para obtener exclusivamente la animación muda, ejecuta el render directamente. Los dos últimos valores son el ancho y los fotogramas por segundo.

```sh
node src/three_video/render.mjs src/three_video/films/jardin.json output/visual.mp4 1280 24
```
