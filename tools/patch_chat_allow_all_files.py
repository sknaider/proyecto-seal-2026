"""Patch chat_server.py: aceptar CUALQUIER tipo de archivo de forma SEGURA (Henry 28-jun, NEXUS #19).
Tipo genérico 'file' → ext original saneada → se sirve como attachment + octet-stream (NUNCA inline/
ejecutable; preserva la defensa anti stored-XSS). Atómico: aborta si algún ancla no aparece exactamente 1 vez."""
import re, sys, py_compile

P = "/home/dadito/IA/proyecto-seal/messages/chat_server.py"
s = open(P, encoding="utf-8").read()
orig = s


def repl_once(text, old, new, label):
    c = text.count(old)
    if c != 1:
        print(f"ABORT [{label}]: ancla encontrada {c} veces (esperaba 1)"); sys.exit(1)
    print(f"ok [{label}]")
    return text.replace(old, new)


# 1) reject-else → ftype='file' (el string de error está SOUL-redactado en la vista → regex DOTALL)
pat = re.compile(r'    else:\n        return JSONResponse\(\{"ok": False, "error": "tipos permitidos:.*?status_code=400\)\n',
                 re.DOTALL)
m = pat.findall(s)
if len(m) != 1:
    print(f"ABORT [reject-else]: regex matched {len(m)} (esperaba 1)"); sys.exit(1)
s = pat.sub('    else:\n        ftype = "file"  # cualquier otro tipo (Henry 28-jun): aceptar; se sirve como descarga segura\n', s)
print("ok [reject-else->file]")

# 2) rama de extensión para 'file' (ext original saneada)
s = repl_once(
    s,
    '        ext = _ext_l if _ext_l in _DOC_EXTS else ".bin"\n',
    '        ext = _ext_l if _ext_l in _DOC_EXTS else ".bin"\n'
    '    elif ftype == "file":\n'
    '        import re as _re_ext\n'
    '        _safe = _re_ext.sub(r"[^a-z0-9]", "", (_ext_l or "").lstrip(".").lower())[:12]\n'
    '        ext = ("." + _safe) if _safe else ".bin"  # ext saneada; se sirve como attachment\n',
    "file-ext-branch")

# 3) serving: file_ también como descarga, con octet-stream (nunca inline)
s = repl_once(
    s,
    '    _dl_prefixes = ("document_", "archive_")\n'
    '    if fpath.name.startswith(_dl_prefixes):\n'
    '        # filename= es necesario para que Starlette emita el header Content-Disposition.\n'
    '        return FileResponse(fpath, content_disposition_type="attachment", filename=fpath.name)\n',
    '    _dl_prefixes = ("document_", "archive_", "file_")\n'
    '    if fpath.name.startswith(_dl_prefixes):\n'
    '        # filename= es necesario para que Starlette emita el header Content-Disposition.\n'
    '        _mt = "application/octet-stream" if fpath.name.startswith("file_") else None\n'
    '        return FileResponse(fpath, content_disposition_type="attachment", filename=fpath.name, media_type=_mt)\n',
    "serve-file-attachment")

# 4) frontend: el picker acepta TODO
s = repl_once(
    s,
    'accept="image/*,audio/*,application/pdf,.zip,.rar,.7z,.tar,.gz,.tgz,.bz2,.xz,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.md,.rtf,.odt,.ods,.odp"',
    'accept="*/*"',
    "frontend-accept-all")

# 5) frontend render: 'file' también como link de descarga
s = repl_once(
    s,
    "} else if ((data.type === 'pdf' || data.type === 'archive' || data.type === 'document') && data.file_url) {",
    "} else if ((data.type === 'pdf' || data.type === 'archive' || data.type === 'document' || data.type === 'file') && data.file_url) {",
    "frontend-render-file")

if s == orig:
    print("ABORT: sin cambios"); sys.exit(1)

open(P, "w", encoding="utf-8").write(s)
py_compile.compile(P, doraise=True)
print("PATCH OK + py_compile OK")
