# Apex Quant Snooker

Versión de Apex Quant para **snooker** (circuito profesional: torneos de ranking, invitacionales y sus previas).
Misma familia que fútbol, basket, tenis y hockey. Se ve dentro de **Apex Quant Deportes** (pestaña 🎱).

- Web / app: https://apexquantbot.github.io/apex-quant-snooker/
- Datos: [snooker.org](https://www.snooker.org) (API gratuita para uso no comercial, citando la fuente).

## Cómo funciona

| Pieza | Qué hace |
|---|---|
| `bot_quant.py` | El bot. Se ejecuta cada 15 minutos con GitHub Actions. |
| `datos.json` | Lo que muestra la web (torneos en juego, partidos, cuotas justas, pronósticos). |
| `snooker.json` | Historial de resultados, jugadores y rondas (lo genera el bot). |
| `historico_picks.json` / `historico_mensual.json` | Libro de todos los pronósticos liquidados. |
| `calibracion.json` | Lo que dijo el modelo en cada partido y si salió (para saber si las cuotas justas son de fiar). |

**Sin cuotas de casas (como hockey):** el bot no consulta cuotas. Calcula la **cuota justa** (1 ÷ probabilidad) de cada
mercado y la **cuota mínima** (justa + 5 %). Tú la comparas con Winamax en la casilla de cada partido.

**Modelo:**
1. **Elo por frames** de cada jugador con esta temporada y la anterior: cada frame ganado o perdido mueve el Elo.
2. Con el Elo sale la probabilidad de ganar cada frame y se calcula de forma exacta la carrera a N frames
   (al mejor de 2N−1) → ganador, hándicap de frames (±1.5, ±2.5, ±3.5), total de frames y resultado exacto.
3. Se exigen 30 frames jugados por jugador. No entran shoot-outs, 6 reds, torneos por equipos, femeninos, seniors ni Q Tour.

**Pronósticos:** cada partido se fija como mucho **18 h antes** (el rival se conoce cuando acaba la ronda anterior).
Se elige el mercado con probabilidad entre 60 % y 85 % y cuota mínima ≥ 1,30 que más paga. Los más seguros, hasta
**6 por día**, son «⭐ Destacados»; el resto queda como Control. Walkover o partido no completado → anulado (salvo ganador).

## Permiso de snooker.org (cabecera X-Requested-By)

snooker.org pide que quien descarga datos de forma automática les escriba para acordar una cabecera.
1. Escribe a **webmaster@snooker.org** (texto de ejemplo abajo).
2. Cuando te contesten con el valor, guárdalo en el repositorio: **Settings → Secrets and variables → Actions →
   New repository secret** → nombre `SNOOKER_ORG_HEADER`, valor el que te den.

Mientras no esté, el bot intenta entrar sin cabecera; si snooker.org no le deja, la web lo avisa.

> Subject: API access request – Apex Quant Snooker (non-commercial)
>
> Hello, I'm building a free, non-commercial personal web app (https://apexquantbot.github.io/apex-quant-snooker/)
> that shows snooker.org results and upcoming matches with a statistical model. It runs every 15 minutes on
> GitHub Actions, using about 4–8 requests per run (at most 10 per minute), and credits snooker.org as the source.
> Could you please tell me what value I should use in the X-Requested-By header? Thank you very much!
> Francisco Serrano

## Secretos del repositorio

| Nombre | Obligatorio | Valor |
|---|---|---|
| `SNOOKER_ORG_HEADER` | Sí (cuando lo den) | El valor de cabecera que te dé snooker.org |
| `TELEGRAM_BOT_TOKEN` | No (de momento) | Token del bot de Telegram |
| `TELEGRAM_CHAT_ID` | No (de momento) | `@apex_quant_snooker` (cuando se cree el canal) |

## Lanzamiento cada 15 minutos (cron-job.org)

- URL: `https://api.github.com/repos/Apexquantbot/apex-quant-snooker/actions/workflows/bot.yml/dispatches`
- Método POST · cabeceras `Authorization: Bearer <token>` y `Accept: application/vnd.github+json` · cuerpo `{"ref":"main"}`

🔞 +18 · Juega con responsabilidad · Información estadística, no es asesoramiento.
