"""
emojis.py — Emojis custom del server, listos para usar en embeds/mensajes.

Uso:
    from emojis import EMOJIS
    f"{EMOJIS['checkmark']} Listo!"

IMPORTANTE: estos emojis son custom (viven en un server de Discord). Para que
se rendericen bien, el bot tiene que estar en ese mismo server (o el server
tiene que tener los emojis marcados como disponibles externamente). Si el bot
no comparte server con el emoji, Discord lo muestra como texto roto.

Si en algún momento cambian de servidor de emojis o se borra alguno, solo hay
que actualizar el ID acá abajo — no hace falta tocar el resto del código.
"""

EMOJIS: dict[str, str] = {
    # Estáticos
    "develop": "<:5493develop:1369313476656500746>",
    "terminal": "<:6289terminal:1369684981575847936>",
    "ban": "<:ban:1515205134940897320>",

    # Animados
    "discorddevelopers": "<a:2366discorddevelopers:1369313465780535327>",
    "Verify": "<a:Verify:1515205121888489583>",
    "checkmark": "<a:checkmark:1515205141257650238>",
    "olhos70": "<a:olhos70:1520880404905721956>",
}


def get_emoji(name: str, fallback: str = "") -> str:
    """Devuelve el emoji custom por nombre, o un fallback (unicode/texto) si
    no existe. Útil si algún día se cae un emoji y no querés que reviente."""
    return EMOJIS.get(name, fallback)