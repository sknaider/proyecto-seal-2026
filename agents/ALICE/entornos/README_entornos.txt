# Recetas de los entornos de los que DEPENDEN unidades systemd activas.
# Capturadas 7-sep-2026 tras el borrado. Estado: RECETA CAPTURADA;
# reconstruccion real PENDIENTE DE VALIDAR (un pip freeze prueba que
# versiones hay hoy, no que reinstalarlas produzca un entorno que ande).
#
# seal-spark        103 unidades lo referencian, 35 activas. 126 paquetes.
# ada-v2-mcp-broker 139 paquetes. pip check REPORTA un defecto real:
#                   pynacl declara cffi y cffi NO esta instalado. Hoy no
#                   rompe porque el wheel de pynacl trae _cffi_backend
#                   compilado adentro; cualquier 'import cffi' fallaria.
# mcp-web-soul      32 paquetes. Su venv NO tiene pip (creado sin el), asi
#                   que la receta salio de importlib.metadata, no de freeze.
