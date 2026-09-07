# Probar SOUL + Ollama
# El alma le da su IDENTIDAD al modelo, y el modelo responde COMO ella.
#
# Requisitos:
#   1) Ollama corriendo con un modelo:   ollama pull qwen2.5:1.5b
#   2) En tu venv de soul:               pip install "soul-framework[llm]"
# Correr:                                python probar_soul_ollama.py
#
# Nota: el CLI de SOUL no tiene comando de "chat" — administra el alma (identidad/memoria).
# La conversacion la hace un modelo (Ollama) al que le pasas el contexto del alma. Eso hace este script.

import asyncio
from soul_framework import Soul
from soul_framework.llm.ollama import OllamaProvider

MODELO = "qwen2.5:1.5b"   # cambialo por el modelo que tengas en Ollama (ollama list)

async def main():
    # 1) Crea/abre un alma con identidad (personality VA como dict)
    async with Soul.create(
        "mialma",
        personality={"personality": "curiosa y directa, responde breve"},
    ) as soul:
        contexto = await soul.boot()   # el "quien soy" del alma

    # 2) Pasa esa identidad a Ollama y que responda COMO el alma
    llm = OllamaProvider(model=MODELO)
    prompt = f"{contexto}\n\nUsuario: Hola, en una frase, quien sos?\nvos:"
    print(f"Pensando (via Ollama: {MODELO})...\n")
    respuesta = await llm.generate(prompt, max_tokens=120)
    print(respuesta.strip())

if __name__ == "__main__":
    asyncio.run(main())
