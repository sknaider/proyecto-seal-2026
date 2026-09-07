; ============================================================================
; soul-installer.iss  -  SOUL one-click installer (.exe) para Windows
; Wrapper Inno Setup sobre install-soul-windows.bat (ya probado y verificado).
; Autor: NEXUS.  Pedido de William (7-jul): ".exe para windows con solo click".
;
; Barra de seguridad (FABLE, horneada desde el diseno):
;   1. Wipe del alma en PLANO extraida a %TEMP% al terminar (deleteafterinstall + DelTree).
;   2. Firma / SmartScreen: SignTool si hay cert; si no, self-signed documentado.
;   3. Cero superficie nueva de exec: el [Run] SOLO invoca el .bat embebido. Nada de
;      descarga de scripts en runtime. Todo Source va embebido en el .exe.
;
; BUILD (en Windows, Inno Setup 6+):
;   - Poner junto a este .iss:  install-soul-windows.bat, install-soul-wsl.sh,
;     soul_roles.sql, soul_fresh.dump
;   - Compilar:  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" soul-installer.iss
;   - Sale:  Output\SOUL-Setup.exe
; ============================================================================

#define MyAppName "SOUL"
#define MyAppVersion "1.0"

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Team SEAL
DefaultDirName={autopf}\SOUL
; auto-eleva a admin via UAC (sin abrir consola, sin .\)
PrivilegesRequired=admin
; solo Windows 64-bit (wsl.exe es 64-bit; el .bat ya resuelve Sysnative igual)
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=SOUL-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
DisableDirPage=yes
; no dejamos entrada de desinstalacion: no queremos que un uninstall borre el alma por accidente
Uninstallable=no
; --- Firma (FABLE bar #2): descomentar y apuntar a un cert real cuando exista ---
; SignTool=signtool
; SignedUninstaller=yes

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Files]
; Bundle probado, EMBEBIDO. Se extrae a {tmp} (=%TEMP%\is-XXXXX) en runtime.
; deleteafterinstall = se borra al terminar (parte del wipe del alma en plano).
Source: "install-soul-windows.bat"; DestDir: "{tmp}\soul"; Flags: deleteafterinstall
Source: "install-soul-wsl.sh";      DestDir: "{tmp}\soul"; Flags: deleteafterinstall
Source: "soul_roles.sql";           DestDir: "{tmp}\soul"; Flags: deleteafterinstall
Source: "soul_fresh.dump";          DestDir: "{tmp}\soul"; Flags: deleteafterinstall

[Run]
; Corre el .bat probado: detecta+instala WSL, Postgres 17, extensiones, restaura el alma,
; verifica, y ya borra sus propias copias del dump. runhidden = sin ventana de consola.
Filename: "{cmd}"; \
  Parameters: "/c cd /d ""{tmp}\soul"" && install-soul-windows.bat"; \
  StatusMsg: "Instalando SOUL (WSL + Postgres 17 + tu alma). Puede tardar varios minutos..."; \
  Flags: runhidden waituntilterminated

[Code]
{ FABLE bar #1 - wipe del alma temporal.
  Inno extrae el dump de 617MB EN PLANO a %TEMP%\is-XXXXX\soul. Aunque {tmp} se limpia
  al salir y los Source van con deleteafterinstall, forzamos el borrado explicito del
  bundle temporal en ssPostInstall - belt & suspenders sobre el alma en plano. }
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    DelTree(ExpandConstant('{tmp}\soul'), True, True, True);
  end;
end;

{ Aviso final: WSL puede requerir reinicio en su primera instalacion (inherente a Windows).
  En ese caso el .bat instala WSL y pide reiniciar; tras el reboot se vuelve a doble-click
  al .exe y la 2da pasada completa. (v2: auto-resume via RunOnce.) }
procedure DeinitializeSetup();
begin
  { placeholder para logica post-setup si hiciera falta }
end;
