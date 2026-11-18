# GTA 6

Módulo editorial en **Producción → GTA 6** (`/gta6`). Reúne el flujo de las dos
imágenes de referencia sin depender de Opus Clip, Kapwing o un editor externo.
No instala esas herramientas: usa Clips, los proveedores de texto, TTS,
FFmpeg y el uploader Firefox que MoneyPrinter ya tiene.

## Recorrido

1. **Configuración**: nombre, handle, bio, idioma, redes, zona IANA, voz,
   horarios, límites diarios y mesa de lanzamiento. La zona inicial es
   `America/La_Paz`; los controles de fecha usan esa zona, aunque el navegador
   esté en otra. La cuenta atrás necesita fecha y un hecho oficial vigente.
2. **Fuentes y hechos**: lista inicial de diez fuentes oficiales o de
   descubrimiento. Añade los creadores originales, su crédito y la evidencia
   de permiso/base de uso. Los hechos se registran con el enlace exacto y una
   confirmación de revisión. Vencen a las 48 h; no se verifican por el mero
   hecho de haberlos escrito un modelo.
3. **Estudio → Banco de ideas**: generación con `llm_provider.generate_text`,
   hechos vigentes, resultados de publicaciones y lecciones. Sin modelo hay
   plantillas limitadas y se informa cuántas ideas distintas se pudieron crear.
   Nunca se rellenan quince espacios con hechos inventados.
4. **Estudio → Producir clips**: mejores momentos, tramos manuales o video
   completo reanudable. El perfil inicial es 7–30 segundos, 9:16 y gancho en
   pantalla. Reutiliza transcripción, puntuación, selección sin solapamiento,
   subtítulos, encuadres y seguimiento de caras de `src/clips/`. Los límites
   por trabajo no cambian la configuración global de Clips.
5. **Voz en off**: al activarla en Configuración, el comentario se sintetiza
   con `ScriptVoicePreview` y el TTS existente. La versión narrada se vuelve a
   renderizar desde la fuente original, con subtítulos del narrador, y reemplaza
   el audio original. Se conserva el clip base. El comentario debe caber en el
   tramo; si no cabe, el trabajo pide acortarlo. No acelera ni corta palabras
   para ocultar el problema.
6. **Borradores**: revisar video, texto, hechos, crédito y comentario propio;
   marcar la revisión y aprobar. Puedes crear variantes por red. X elimina
   hashtags; Instagram añade tres si faltan. Los textos largos se señalan para
   corregirlos, sin cortar frases ni créditos. Los cambios vuelven a borrador.
7. **Calendario**: reservar individualmente o repartir aprobados entre las
   franjas del día. Una reserva local no publica. Desde la ficha puedes
   enviarla a Buffer o subir un clip a YouTube con un canal Firefox existente.
8. **Aprendizaje**: confirmar el enlace y fecha reales de cada publicación;
   registrar lecturas de 12, 24 y 72 h; guardar wins, losses y reglas; probar una
   variable al día; convertir preguntas en ideas y estudiar otras páginas.

## Noticias narradas y lote del día

En **Hoy → Generar lote del día** o **Estudio → Noticias narradas**:

1. Elige un canal identificado de MoneyPrinter.
2. Elige entre 1 y 5 hechos oficiales vigentes, o deja que se seleccionen los
   que todavía no se usaron. El lote inicial propone 2 videos y 3 textos.
3. El sistema conserva la frase factual, prepara un guion breve, sintetiza voz
   con `ScriptVoicePreview` y TTS, crea tarjetas originales, quema subtítulos y
   renderiza MP4 de 1080×1920. Reutiliza el estilo y los encoders de Clips.
4. Los videos quedan en Clips y como borradores de GTA 6. También prepara
   variantes para las redes seleccionadas y textos para X. La generación no
   necesita ComfyUI, imágenes de terceros ni un clip de gameplay.
5. Si activas **Subir los videos a YouTube al terminar**, ese lote autoriza
   la publicación de los videos tras comprobar hechos, procedencia y render.
   La aprobación se registra como automática y se invalida al editar el texto
   o cambiar el hecho. Las otras redes siguen recibiendo borradores.

Cada trabajo guarda el avance por video. **Continuar sin repetir terminados**
recupera los MP4 existentes y los resultados de subida confirmados. Un envío
incierto pausa el lote: resuélvelo en Clips antes de continuar. No se envía de
nuevo un video cuyo resultado ya está confirmado.

Los canales GTA nuevos se registran con subidas desactivadas hasta identificar
su handle y asociar su perfil. La pantalla puede listar los nombres y rutas de
`profiles.ini`; no lee cookies ni sesiones. Al asociar un perfil se consulta el
ID público del canal con yt-dlp. El uploader comprueba ese ID contra el canal
de la sesión antes de seleccionar el archivo, para no enviar al otro canal.

El kit también contiene avatar de 800×800 y banner de 2560×1440 en PNG, con el
nombre, handle y texto centrales. Generar esos archivos o registrar canales en
MoneyPrinter no cambia la identidad pública de YouTube; se aplican en Studio.

```powershell
venv\Scripts\python.exe scripts\gta6.py news --request-id <id_del_lote>
venv\Scripts\python.exe -m pytest tests\test_gta6_news.py
```

## Seguimiento y producción automáticos

### Solo los creadores elegidos e idiomas por cuenta

`hermes_routes` relaciona el enlace canónico de cada canal con
`youtube_channel_id`, `language` (`en` o `es`) y, opcionalmente,
`source_language`. Cuando hay una lista, Hermes no consulta los directorios
generales y rechaza cualquier otra fuente al capturar, revisar, mostrar o
enviar. La asignación por creador sustituye al reparto numérico entre cuentas.
El panel muestra la lista completa y el destino de cada creador.

`hermes_layout: "crop"` llena la pantalla vertical sin franjas difuminadas y
conserva la cámara mediante el seguimiento facial existente cuando hace falta.

`hermes_min_seconds` y `hermes_max_seconds` delimitan los cortes de directos y
comunidad (entre 7 y 120 segundos). Para clips de uno a dos minutos, usa 60 y
120. Se descartan los clips de comunidad más cortos que el mínimo; no se
alargan artificialmente. La caché de subtítulos y vídeo valida también el tramo
seleccionado para no reutilizar un corte anterior con otra duración.
Las copias para revisar llevan subtítulos reales quemados: Whisper small
transcribe español y traduce al inglés para la cuenta inglesa. El audio
original se conserva. No se reemplaza una transcripción fallida por diálogo
inventado. La producción reutiliza esos subtítulos y valida idioma y destino
antes del envío.

`hermes_community_enabled` añade los clips públicos de las comunidades de
esos mismos creadores en Twitch y Kick, publicados en las últimas 24 horas. Se comprueba
creador, categoría GTA VI, fecha y duración antes de descargar. Se conserva
el enlace del clip y el nombre del clipper; los IDs ya capturados no se
descargan de nuevo. Se revisan hasta 20 candidatos por creador cada 15 minutos,
con hasta dos descargas nuevas por creador en cada ciclo. El límite compartido
es de 18 capturas pendientes, además de los límites de disco.

`hermes_platforms` permite limitar las redes de destino, por ejemplo a
`["youtube"]`. Esto no configura por sí solo sesiones ni uploaders adicionales.

En **GTA 6 → Automático → Hermes · Directos**, **Iniciar Hermes y publicar clips**
enciende un proceso local independiente. **Detener Hermes** interrumpe el
seguimiento. Un envío que ya llegó a una plataforma puede terminar allí.
El backend comprueba el estado guardado al arrancar y recupera el proceso
si deja de responder.

Cada cinco minutos se consultan en Chrome los directorios públicos de Twitch,
Kick y YouTube. Se añaden también los canales de las fuentes activas y se
respetan las exclusiones. Streamlink rota ocho grabadores por defecto
(`MP_HERMES_RECORDERS`, de 1 a 32) en bloques de dos minutos. El turno, la
conexión y la disponibilidad del sitio determinan la cobertura. Hermes revisa
una imagen cada cuatro segundos y propone el tramo, el título y el comentario.
Los momentos seleccionados se recortan como MP4 locales para revisión. En
**Ver actividad de Hermes → Cortes para revisar → Ver corte** puedes reproducir
el tramo. El material en revisión se conserva hasta seis horas. La producción
para redes se inicia cuando la fuente tiene crédito y permiso.
El supervisor deja de iniciar grabaciones con menos de 3 GiB libres o más de
12 GiB de capturas. La lista de actividad muestra los errores de cada servicio.

La skill se distribuye en `integrations/hermes/moneyprinter-live/SKILL.md`.
En otra instalación, cópiala a `skills/moneyprinter-live/SKILL.md` del
directorio de Hermes y selecciona un proveedor con visión. El servicio usa
Chrome instalado, FFmpeg, la CLI de Hermes y el entorno Python del proyecto.

Cada clip produce cuatro entregas. YouTube utiliza el canal y el perfil
verificados de GTA 6. `hermes_youtube_channel_ids` permite repartir los clips
entre varias cuentas de YouTube: cada clip se reserva para una sola cuenta,
equilibrando las asignaciones (10 clips entre dos cuentas: 5 y 5). La reserva
se conserva al reintentar o reiniciar. Si una cuenta no tiene su perfil listo,
sus clips esperan allí; no se desvían a otra cuenta. Sin esa lista se utiliza
el canal principal de GTA 6. TikTok, Instagram y X pueden usar Buffer con canal y
URL pública del MP4, o un módulo instalado con un entry point de Python en
`moneyprinter.uploaders` llamado `tiktok`, `instagram`, `x` o `youtube`.
El objeto exportado debe implementar `submit(key, path, post)` y
`receipt(key)`. La clave estable es el ID de la publicación. Tras un envío
interrumpido se consulta la misma clave. Un recibo
`{"status":"published","url":"https://…"}` confirma la publicación; un
resultado pendiente se vuelve a consultar. Los adaptadores deben conservar
la relación de la clave con el ID remoto incluso si su proceso se reinicia.

En **GTA 6 → Automático**, activa el seguimiento y guarda la configuración.
Mientras el backend esté abierto, el programador revisa la lista por turnos,
encuentra publicaciones recientes de GTA y alimenta al productor de Clips.
Al reiniciar la aplicación conserva el próximo ciclo, el turno de creadores,
los videos encontrados y los trabajos ya realizados.

- **Frecuencia**: 60 minutos inicialmente, configurable desde 15 minutos.
- **Fuentes por ciclo**: 12 inicialmente, con rotación hasta recorrer la lista.
- **Videos por fuente**: los 3 más recientes, con ventana inicial de 7 días.
  El filtro busca GTA V, GTA VI, GTA Online o roleplay identificable; excluye
  filtraciones, emisiones activas y publicaciones sin fecha comprobable.
  Las fechas relativas estimadas por YouTube se identifican como aproximadas.
- **Producción**: hasta 2 videos fuente por día en la zona del workspace,
  con 1 clip de 7–30 segundos por fuente inicialmente. El cupo cuenta intentos,
  incluidos los fallidos, para acotar el trabajo. Un cupo de 0 solo descubre.
- **Resultado**: clips y borradores locales con crédito y el comentario
  configurado. Este proceso no invoca los uploaders ni envía posts a Buffer.

El descubrimiento consulta metadatos públicos con yt-dlp, sin descargar los
videos ni leer cookies. **Revisar permiso** permite encontrar candidatos;
solo se recortan fuentes activas con crédito, permiso/base de uso documentada
y seguimiento habilitado. Registrar una fuente no concede ese permiso.
Los creadores de la lista de exclusión no se consultan ni producen.

Cada fuente tiene un **Canal para seguimiento** opcional: por ejemplo, el
canal oficial de YouTube de un creador cuya URL principal es Twitch. YouTube
y los VOD públicos de Twitch dependen del acceso que dé la plataforma.
Los canales de Kick todavía no tienen un listado compatible en yt-dlp;
aparecen como no compatibles y se puede indicar su canal oficial alternativo.
Los errores de una fuente quedan visibles y no detienen a las demás.

**Escanear ahora** funciona también en pausa. **Ejecutar ciclo** descubre y
produce cuando el automático está activado. Pausar impide iniciar nuevas
producciones; un render que ya comenzó puede terminar. Los fallos o renders
interrumpidos requieren un reintento explícito y se comprueba primero si ya
hay clips, para evitar duplicados. Los candidatos se pueden descartar.

El programador se inicia con el ciclo de vida normal de FastAPI. Arranca el
backend sin `--lifespan off`; no hace falta mantener abierta la pestaña web.
Para detener todas las revisiones, pausa Automático o cierra el backend.

```powershell
venv\Scripts\python.exe scripts\gta6.py autopilot --status
venv\Scripts\python.exe scripts\gta6.py autopilot --scan-only
venv\Scripts\python.exe scripts\gta6.py autopilot --enable
venv\Scripts\python.exe scripts\gta6.py autopilot --disable
```

`--enable` activa la configuración y ejecuta un ciclo. El backend sostiene
los ciclos posteriores; la CLI por sí sola termina al finalizar el ciclo.

## Buffer

Configura `BUFFER_API_KEY` en el entorno del backend, reinícialo y consulta
los canales desde GTA 6 → Configuración. Asocia el ID real de cada red.
No guardes el token en el módulo ni en Git.

Se usa la [API GraphQL actual](https://developers.buffer.com/guides/getting-started.html),
con [horario explícito](https://developers.buffer.com/guides/your-first-post.html)
(`customScheduled`) y el [formato actual de assets de video](https://developers.buffer.com/examples/create-video-post.html).
Se comprueba que el canal corresponda a la plataforma y no esté pausado.

Buffer necesita una **URL HTTPS directa y permanente** al MP4 o imagen,
accesible sin login. El servidor local no aloja medios públicamente. Descarga
el clip desde su ficha, colócalo en tu alojamiento y registra esa URL. Una
ruta local, un enlace de Drive para compartir o una URL temporal no sirven.
Para YouTube también puedes usar la subida Firefox existente sin alojamiento
público ni clave de Buffer.

Estados del envío: `planned → dispatching → scheduled`. `scheduled` significa
aceptado por Buffer, no publicado en la red. Un timeout queda en
`pending_confirmation` y no se reintenta automáticamente. Revisa la cola y
registra el ID remoto o confirma que no se creó. Un proceso interrumpido puede
quedar en `dispatching`; tras dos minutos se permite resolverlo desde la ficha.

Bloquear una fuente bloquea sus posts locales y conserva sus IDs externos.
La interfaz indica que hay que cancelar los envíos remotos y retirar los posts
en cada plataforma: un cambio local no confirma una retirada externa.

## Métricas y operación

- Los valores no disponibles quedan vacíos; cero es una observación real.
- No se aceptan lecturas antes de que transcurra su edad correspondiente.
- La revisión semanal compara 72 h; si no hay lecturas, usa 24 h. No mezcla
  edades. Las 12 h son una señal temprana y no se usan para declarar ganadores.
- Muestra top, bottom y medianas por red, pilar y franja con tamaño de muestra.
  Son descripciones de lo observado, no una prueba de causalidad.
- Una prueba cambia una variable y no se cierra antes de 24 h. Registra las
  publicaciones y la evidencia de la conclusión en sus notas.
- Hoy contiene la rutina diaria, el checklist de siete días y, opcionalmente,
  la mesa de lanzamiento. Las tareas no envían comentarios ni DMs.

## Archivos y CLI

El estado está en `.mp/gta6/workspace.sqlite3`, con transacciones SQLite y sin
secretos de cuentas. Los videos se conservan en el catálogo existente de Clips.
**Exportar** produce `GTA-6.zip`: `playbook.md`, `facts.md`, `idea-bank.md`,
`watchlist.md`, `log.md`, `scoreboard.csv`, `calendar.csv`, `learning.md`,
`takedowns.md`, informe diario, estado JSON y avatar/banner SVG originales.
Los MP4 se descargan individualmente desde cada ficha para no construir ZIP
gigantes en memoria.

```powershell
venv\Scripts\python.exe scripts\gta6.py status
venv\Scripts\python.exe scripts\gta6.py ideas --count 15
venv\Scripts\python.exe scripts\gta6.py report
venv\Scripts\python.exe scripts\gta6.py export
venv\Scripts\python.exe -m pytest tests\test_gta6.py
```

El menú `python src/main.py` tiene la opción **6. GTA 6**. Se conserva la
opción 5 para salir. Los trabajos de clips comparten el registro de jobs,
progreso SSE y panel de trabajos de la webapp.

La configuración de cuentas externas, los permisos de creadores y las
métricas reales los aporta quien opera la página. No se crean cuentas, se
contratan planes o se publican mensajes al instalar este módulo.
