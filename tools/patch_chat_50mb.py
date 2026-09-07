"""Patch chat_server.py: subir el límite de subida 25MB → 50MB (Henry 29-jun). Atómico."""
import sys, py_compile

P = "/home/dadito/IA/proyecto-seal/messages/chat_server.py"
s = open(P, encoding="utf-8").read()


def repl_once(text, old, new, label):
    c = text.count(old)
    if c != 1:
        print(f"ABORT [{label}]: ancla {c} veces (esperaba 1)"); sys.exit(1)
    print(f"ok [{label}]")
    return text.replace(old, new)


s = repl_once(s, "MAX_UPLOAD_SIZE = 25 * 1024 * 1024", "MAX_UPLOAD_SIZE = 50 * 1024 * 1024", "backend-const")
s = repl_once(s, "archivo muy grande (max 25MB)", "archivo muy grande (max 50MB)", "backend-error")
s = repl_once(s, "f.size > 25*1024*1024", "f.size > 50*1024*1024", "frontend-toobig")
s = repl_once(s, "f.size <= 25*1024*1024", "f.size <= 50*1024*1024", "frontend-filter")
s = repl_once(s, "Muy grande (max 25MB c/u)", "Muy grande (max 50MB c/u)", "frontend-msg")

open(P, "w", encoding="utf-8").write(s)
py_compile.compile(P, doraise=True)
print("PATCH OK + py_compile OK")
