# Proyecto SEAL — Sistema de Mensajes entre Instancias Claude

## Cómo funciona
Dos instancias de Claude (VSCode + Terminal) se comunican via archivos JSON.

## Archivos
- `terminal_log.jsonl` — Terminal Claude escribe sus acciones y reportes
- `vscode_commands.jsonl` — VSCode Claude escribe comandos para Terminal
- `shared_state.json` — Estado compartido (último step, errores, decisiones)

## Protocolo
1. Cada instancia revisa los mensajes del otro periódicamente
2. Formato: `{"timestamp": "...", "from": "terminal|vscode", "type": "report|command|alert", "message": "..."}`
3. Append-only (JSONL) para evitar conflictos de escritura
