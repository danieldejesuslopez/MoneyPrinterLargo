# Clips

Aprende qué funciona en formato corto y luego lo recorta de un video largo.
Le das un link (o un archivo), y el módulo puede elegir los mejores
momentos o dividir el video entero en partes consecutivas. Los renderiza
en 1080x1920 con subtítulos y los deja como
**borradores** para que los revises. Nunca publica nada por su cuenta.

---

## Uso rápido

```bash
python scripts/clips.py trends                     # qué gana, y con cuántos datos lo sabe
python scripts/clips.py preview <url> --rights "mi propio canal"   # elegir sin renderizar
python scripts/clips.py run <url> --rights "mi propio canal" --language es
python scripts/clips.py full <url> --rights "mi propio canal" --clip-seconds 60
python scripts/clips.py list                       # clips esperando revisión
python scripts/clips.py approve <clip_id>
python scripts/clips.py reject <clip_id>
python scripts/clips.py upload <clip_id> --channel-id <channel_id>  # subir con Firefox
python scripts/clips.py decisions                  # qué se eligió y qué perdió

# tramos elegidos a mano (por ti, o por alguien que vio el video)
python scripts/clips.py cut <url> --rights "..." --at 1:32-1:58 --at 8:05-8:50 \
    --layout facecam

# opcional: forzar un panel superior indicando la cámara a mano
python scripts/clips.py cut <url> --rights "..." --at 1:32-1:58 \
    --layout facecam --facecam 898,11,360,200

# video sin voz: el título se quema como texto de gancho (automático
# cuando no hay subtítulos; --title-overlay always|never para forzarlo)
python scripts/clips.py cut <url> --rights "..." --no-subtitles \
    --at 0:42-1:03 --title "BASES LLENAS Y GRAND SLAM PERFECTO"
```

Todos los comandos aceptan `--json`. Los clips quedan en `.mp/clips/`.

El panel facial automático aparece **solo cuando el encuadre del clip
corta la cara o la deja fuera**. Si ya se ve completa, no se duplica.
`blur` conserva el cuadro entero, por lo que no necesita un panel extra;
con `crop`, el panel aparece únicamente durante los tramos que lo
necesitan. Los zooms del video se conservan. Una caja `--facecam` manual
en el layout `facecam` fuerza el panel superior y tiene prioridad sobre
la detección automática.

`--rights` es obligatorio para una URL. Un clip es una obra derivada: la
nota viaja con cada clip cortado de esa fuente, para que el catálogo diga
siempre por qué existe. Tú respondes por los derechos de cada URL.

---

## Video completo y continuación

En **Clips**, elige el modo **Video completo**, indica la fuente y la
duración aproximada de cada parte. El valor inicial es **60 segundos**;
puedes elegir entre 5 y 180 segundos. Las partes se numeran y cubren
desde el principio hasta el final, conservando también los silencios y
el último tramo aunque sea corto. Si hay palabras con tiempos, los cortes
pueden ajustarse hasta dos segundos para respetar una palabra u oración
cercana sin dejar huecos entre partes.

El campo **Lote** controla cuánto crear en esa ejecución:

- **0** crea todas las partes que faltan, sin el límite de cantidad del
  modo automático.
- Un número mayor crea hasta esa cantidad de partes pendientes; por
  ejemplo, **3** permite trabajar por tandas de tres.

Con **Reanudar sin repetir** activo, el módulo guarda el plan y conserva
los mismos límites e índices al volver a continuar esa serie. Comprueba
la cobertura real de los clips disponibles: una parte posterior no hace
que se salte un hueco anterior. Si borras un archivo o rechazas una parte,
vuelve a considerarse pendiente. Los cambios de fuente o duración se
validan antes de reutilizar un plan.

Puedes usar **Continuar video** en una fuente o **Continuar este video**
desde un clip para preparar esa fuente y reanudar su serie. El progreso
muestra las partes terminadas y las que faltan. Para volver a generar las
partes incluso si ya existen, desactiva la reanudación; los archivos
anteriores se conservan.

Ejemplos en la terminal:

```bash
# Divide toda la fuente; repetir el comando continúa lo pendiente.
python scripts/clips.py full <url> --rights "mi propio canal" --clip-seconds 60

# Un archivo local sin voz, en lotes de tres partes.
python scripts/clips.py full "video.mp4" --no-subtitles --clip-seconds 60 --batch-size 3

# Regenera la serie sin borrar los clips que ya estaban creados.
python scripts/clips.py full "video.mp4" --no-subtitles --no-resume

# Resultado estructurado; --json va antes del comando.
python scripts/clips.py --json full "video.mp4" --no-subtitles
```

Por ejemplo, un video sin voz de 125 segundos y partes de 60 segundos
produce **0–60**, **60–120** y **120–125**. Si se interrumpe una ejecución,
las partes terminadas quedan guardadas y el siguiente intento comienza
por la primera que falte. Si una parte falla, el lote se detiene ahí para
poder continuar después. Un directo cubre el tramo disponible en la
captura de esa fuente, sin seguir la emisión indefinidamente.

Este modo utiliza el mismo encuadre, subtítulos y regla de caras que los
demás clips: conserva los zooms y no duplica un rostro que ya se ve
completo. Todas las partes quedan como borradores; subirlas a YouTube
sigue siendo una acción manual con el perfil de Firefox elegido.

---

## Subir clips a YouTube

En **Clips**, revisa el video y pulsa **Subir a YouTube**. Elige uno de
los canales que ya tienes configurados, revisa el título y la descripción,
y comienza la subida. El progreso se muestra en la misma ventana de
trabajos que usan los Shorts y los videos largos. Crear o recortar clips
sigue dejando borradores: la subida empieza únicamente con esa acción.

La subida reutiliza el flujo de YouTube Studio con **Firefox**. Usa el
perfil del canal seleccionado; si ese canal no tiene uno propio, utiliza
`firefox_profile` de `config.json`. El perfil debe existir y tener la
sesión de YouTube iniciada. No necesitas configurar claves de la API de
YouTube. La ventana de subida muestra el perfil efectivo antes de empezar.

También puedes subir un clip desde la terminal:

```bash
python scripts/clips.py upload <clip_id> --channel-id <channel_id>
python scripts/clips.py upload <clip_id> --channel-id <channel_id> \
    --title "TÍTULO DEL CLIP" --description "Descripción del clip #Shorts"
```

Sin esos cambios de texto, se usa el título del clip y una descripción
preparada con su contenido y fuente. Se sube el archivo exacto de ese clip,
sin volver a generar el video ni modificar sus zooms. El MP4 y sus
subtítulos se conservan después de la subida.

El catálogo registra el canal, el resultado y el enlace de YouTube. Solo
marca el clip como **publicado** cuando la subida queda confirmada. Si
YouTube Studio aún requiere comprobación, aparece **pendiente de
confirmación**: revisa la ventana de Firefox o YouTube Studio antes de
intentar otra subida. Mientras un intento sigue pendiente, no se inicia
otra subida de clips que pueda interrumpir esa ventana de Firefox.

Después de revisar Studio, resuelve el intento desde el catálogo:

- Si el video ya está publicado, confirma el resultado pegando su URL
  de YouTube. El clip queda **publicado** con ese enlace.
- Si el video no se publicó y la transferencia ya se detuvo, permite
  reintentar. Esa acción libera el estado del intento; no inicia una
  subida nueva. El botón **Subir a YouTube** permite iniciarla después.

Si un proceso se interrumpe o la aplicación se reinicia, el catálogo
detecta que ya no hay una subida activa y cambia el intento abandonado a
pendiente de confirmación. Así puedes revisarlo y resolverlo sin editar
archivos ni dejar bloqueados los demás clips. Los errores confirmados
también permiten reintentar conservando el archivo. En la terminal, un
resultado pendiente termina con código de salida `0`, pero conserva el
estado `pending_confirmation`; una subida fallida termina con código `1`.

---

## Cómo aprende de las tendencias

No raspa un feed de "tendencias". Un feed dice qué es popular hoy, pero no
por qué, y no se puede listar de forma fiable. Lee en cambio el historial
que ya tienes:

- `.mp/brain/reference_channels.json`: los canales de referencia que
  importó Brain (`python scripts/brain.py reference --add @canal`).
- `.mp/youtube.json`: tus propios canales.

Solo cuenta los shorts. El éxito de cada video son sus vistas divididas
entre la **mediana de su propio canal**, así que un canal de 100k y uno de
1k pesan igual. Compara el 10% superior contra la mitad inferior y reporta
cada rasgo del título como **lift**: cuánto más común es entre los
ganadores que entre el resto. Un rasgo que tienen igual ganadores y
perdedores no explica nada, y el perfil lo dice en vez de venderlo como
hallazgo.

Con los datos actuales (412 shorts de 3 canales):

- los ganadores duran 57–141 s (mediana 60 s);
- sus títulos tienen unas 6 palabras;
- **saturar de hashtags va en contra**: 53% de los ganadores frente al
  77% del resto.

Ese resumen entra en el prompt del modelo que escribe los títulos, y la
duración ganadora fija el largo de los clips (recortada a `max_seconds`).

Para que aprenda más, importa más canales de referencia de tu nicho y
vuelve a correr `trends --refresh`.

---

## El pipeline

1. **Ingesta** (`ingest.py`). yt-dlp descarga el video a su propio
   directorio, `.mp/clips/sources/<id>/`, y escribe un manifiesto con la
   URL, el autor y tu nota de derechos. Un archivo local se usa tal cual.
   También baja los **subtítulos del video** (`captions.<idioma>.vtt`):
   los escritos por una persona si existen; si no, el reconocimiento de
   voz de YouTube, pero **solo en el idioma original**, porque los demás
   son traducciones automáticas y quemarlos en un clip sería poner en
   boca de alguien algo que no dijo.
2. **Palabras con tiempo** (`transcribe.py`). En este orden: el archivo
   que pases con `--captions`, los subtítulos descargados con el video y,
   si no hay ninguno, faster-whisper con tu `whisper_model`. Los
   subtítulos aciertan donde Whisper `base` falla (gritos, nombres
   propios), y los de YouTube traen tiempos reales por palabra. Con
   `--whisper` se ignoran y se transcribe igualmente. La transcripción de
   Whisper se guarda en caché.
3. **Candidatos** (`segments.py`). Corridas de **oraciones completas**:
   un clip nunca empieza ni termina a mitad de frase. Las oraciones se
   cortan por puntuación o por silencio, así que funciona aunque Whisper
   no puntúe.
4. **Puntuación** (`scoring.py`). Las heurísticas puras miden si encaja en
   el largo ganador, si empieza con gancho o a mitad de argumento ("pero",
   "entonces"…), si termina la frase y si de verdad hay alguien hablando.
   El modelo, con los 20 mejores, juzga gancho, remate y si se entiende
   sin el resto del video, y escribe el título. Si ningún modelo responde,
   sigue con heurísticas y lo avisa.
5. **Selección**. Los mejores N que no repitan el mismo momento: dos
   clips no se solapan más de `max_overlap`.
6. **Render** (`render.py`). Comprueba si el encuadre elegido conserva la
   cara completa. Con `blur` mantiene el cuadro entero sin duplicar el
   rostro. Cuando un recorte puede cortar la cara, escanea el tramo con
   un detector local y sigue el rostro para mostrarlo arriba únicamente
   mientras queda cortado o fuera del encuadre principal. El video
   principal mantiene su encuadre y sus zooms; el panel facial se extrae
   de la fuente original. Una caja manual en el layout `facecam` fuerza
   el panel superior. Si no hay cara, se usa el render habitual.
   Después, una sola pasada de ffmpeg: corte, reencuadre
   a 9:16 (el cuadro entero sobre un fondo desenfocado, o recorte
   central, o cámara arriba y juego abajo) y subtítulos ASS quemados en
   Poppins Black. Si el clip no lleva subtítulos (no hay voz, o
   `--no-subtitles`), quema además su **título** como texto de gancho
   durante todo el clip: centrado en la franja desenfocada de arriba con
   `blur`, arriba sobre la imagen con `crop`, y abajo del panel de juego
   con `facecam`. Cuando hay panel facial, el texto se coloca fuera de él
   para no tapar la cara. Prueba la codificación por
   hardware y cae a libx264 si falla.
7. **Registro** (`store.py`). `clips.json` es el catálogo;
   `decisions.jsonl` guarda también los candidatos que **perdieron**, que
   es lo que permitirá medir más adelante si el puntuador elige bien.

---

## Configuración

Bloque `clips` en `config.json`; todo tiene un valor por defecto.

| clave | defecto | qué hace |
|---|---|---|
| `language` | `""` | idioma de los subtítulos a bajar y de Whisper. Vacío = el idioma original del video (o autodetectar, que con `base` falla en narración con música). |
| `captions` | `auto` | `auto`: subtítulos escritos o, si no hay, el reconocimiento de YouTube en el idioma original; `manual`: solo escritos; `off`: siempre Whisper. |
| `min_seconds` / `max_seconds` | 18 / 60 | límites del clip; el perfil de tendencias elige dentro de ellos. |
| `count` | 3 | clips por fuente. |
| `max_overlap` | 0.25 | solape máximo entre dos clips elegidos. |
| `reframe` | `blur` | `blur` (cuadro completo), `crop` (recorte central) o `facecam` (cámara arriba, juego abajo). |
| `auto_facecam` | `true` | añade un panel facial solo mientras el encuadre corta la cara o la deja fuera. Si ya se ve completa, no la duplica; conserva los zooms del video. |
| `font` | `Poppins-Black.ttf` | fuente de `fonts/` para los subtítulos. |
| `subtitle_font_size` | 80 | tamaño de los subtítulos (el mismo que usa el resto del proyecto). |
| `title_overlay` | `auto` | quemar el título en pantalla: `auto` solo cuando el clip no lleva subtítulos; `always`; `never`. `--title-overlay` lo cambia para una corrida. |
| `title_font_size` | 88 | tamaño del título quemado; los títulos largos se parten en líneas equilibradas. |
| `facecam` | `""` | caja opcional de la cámara del streamer, `x,y,w,h` en píxeles de la fuente, para el layout `facecam`. Una caja manual fuerza el panel superior; sin caja, se aplica la regla automática de no duplicar caras completas. |
| `max_source_minutes` | 180 | no descarga nada más largo sin `--max-minutes`. |

---

## Límites conocidos

- **Calidad de transcripción.** Solo importa cuando el video no trae
  subtítulos (archivos locales, o plataformas sin ellos). Con
  `whisper_model: base` los nombres propios y los gritos salen mal
  ("Parker Solar Probe" → "Parker solar probesía", "¡vienen!" → "miren").
  `small` o `medium` mejoran mucho, a cambio de tiempo.
- **Subtítulos escritos sin tiempo por palabra.** Los manuales vienen por
  bloque, así que sus palabras se reparten a partes iguales dentro de
  cada uno: es aproximado, pero de sobra para 2–3 palabras en pantalla.
- **Subtítulos dobles.** Si la fuente ya trae subtítulos quemados, se
  apilan con los nuevos. Usa `--no-subtitles` en ese caso.
- **Detección de caras.** El detector local funciona mejor con rostros
  frontales. Perfiles, caras pequeñas, oclusiones o poca luz pueden hacer
  que pierda el rostro. Con varias personas elige una cara persistente;
  no reconoce identidades. Puedes indicar la caja de la cámara a mano
  con `--layout facecam --facecam x,y,w,h` cuando haga falta.
- **Publicación manual.** Crear clips nunca publica por sí solo. La
  subida requiere elegir un clip y un canal; un resultado pendiente de
  confirmación debe revisarse en Firefox o YouTube Studio.
