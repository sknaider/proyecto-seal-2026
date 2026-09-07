param(
    [string]$Database = "$env:USERPROFILE\soul-core\Scripts\alma_william.db.nuevo",
    [string]$Candidate = "$env:USERPROFILE\soul-web-0.1.4",
    [string]$ReleaseRoot = "$env:USERPROFILE\SOUL-Releases\0.1.4",
    [string]$TaskName = "SOUL Web",
    [int]$Port = 8777
)

$ErrorActionPreference = "Stop"
$ExpectedService = "soul-memory-web"
$ExpectedVersion = "0.1.4"
$ExpectedBuild = "soul-memory-web-0.1.4"
$Database = [IO.Path]::GetFullPath($Database)
$Candidate = [IO.Path]::GetFullPath($Candidate)
$ReleaseRoot = [IO.Path]::GetFullPath($ReleaseRoot)
$SoulWeb = Join-Path $Candidate "Scripts\soul-web.exe"
$CandidatePython = Join-Path $Candidate "Scripts\python.exe"
$BackupTool = Join-Path $Candidate "Scripts\soul-web-backup.exe"
$ExpectedArguments = "--db `"$Database`" --port $Port --extraction-model gemma4:12b-it-qat --no-browser"

if (-not (Test-Path -LiteralPath $Database -PathType Leaf)) { throw "DB canónica no existe: $Database" }
if (-not (Test-Path -LiteralPath $SoulWeb -PathType Leaf)) { throw "candidato no existe: $SoulWeb" }
if (-not (Test-Path -LiteralPath $CandidatePython -PathType Leaf)) { throw "python del candidato no existe" }
if (-not (Test-Path -LiteralPath $BackupTool -PathType Leaf)) { throw "backup tool no existe" }
if ($Port -lt 1024 -or $Port -gt 65535) { throw "puerto inválido" }

function Get-InterpreterIdentity([string]$VenvPython) {
    $Identity = (& $VenvPython -c "import os,sys; print(os.path.realpath(getattr(sys,'_base_executable',sys.executable)))").Trim()
    if ($LASTEXITCODE -ne 0 -or -not $Identity) {
        throw "no se pudo resolver la identidad del intérprete: $VenvPython"
    }
    [IO.Path]::GetFullPath($Identity)
}

function Test-LauncherCommandLine(
    [string]$Observed,
    [string]$VenvPython,
    [string]$ConsoleScript,
    [string]$Arguments
) {
    # Console scripts de Windows lanzan el Python base, pero conservan en
    # CommandLine el python del venv + el soul-web.exe. Ligamos ambos tokens y
    # todos los argumentos, permitiendo únicamente variación de whitespace.
    $Pattern = '^"?' + [regex]::Escape($VenvPython) + '"?\s+' +
        '"?' + [regex]::Escape($ConsoleScript) + '"?\s+' +
        [regex]::Escape($Arguments) + '$'
    # Windows paths are case-insensitive; -match avoids a false rejection when
    # CIM/task normalize drive/path casing differently.
    ([string]$Observed) -match $Pattern
}

$CandidateInterpreter = Get-InterpreterIdentity $CandidatePython

function Get-ListenerProcessIds {
    @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -in @("127.0.0.1", "::1") } |
        Select-Object -ExpandProperty OwningProcess -Unique)
}

function Stop-ProcessIds([object[]]$ProcessIds) {
    foreach ($ProcessId in @($ProcessIds)) {
        if ($ProcessId -and (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
            Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
    for ($Attempt = 0; $Attempt -lt 20; $Attempt++) {
        $Alive = @($ProcessIds | Where-Object {
            $_ -and (Get-Process -Id $_ -ErrorAction SilentlyContinue)
        })
        if ($Alive.Count -eq 0) { return }
        Start-Sleep -Milliseconds 250
    }
    throw "no se pudieron detener todos los procesos: $($ProcessIds -join ',')"
}

function Restore-DatabaseIfChanged {
    if (-not (Test-Path -LiteralPath $Database -PathType Leaf)) { return }
    $Observed = Join-Path $ReleaseRoot "alma_william.rollback-observed-$Timestamp.db"
    $ObservedRaw = & $BackupTool $Database $Observed
    if ($LASTEXITCODE -ne 0) { throw "no se pudo auditar la DB durante rollback" }
    $ObservedReceipt = $ObservedRaw | ConvertFrom-Json
    if (-not $ObservedReceipt.ok -or $ObservedReceipt.quick_check -ne "ok") {
        throw "recibo de DB observada inválido durante rollback"
    }
    if ($ObservedReceipt.sha256 -eq $Receipt.sha256) { return }

    # Nada se borra: el estado fallido y sus sidecars se conservan para forensia.
    $FailedDatabase = Join-Path $ReleaseRoot "alma_william.failed-candidate-$Timestamp.db"
    Move-Item -LiteralPath $Database -Destination $FailedDatabase
    foreach ($Suffix in @("-wal", "-shm")) {
        $Sidecar = "$Database$Suffix"
        if (Test-Path -LiteralPath $Sidecar) {
            Move-Item -LiteralPath $Sidecar -Destination "$FailedDatabase$Suffix"
        }
    }
    # Todos los candidatos ya están detenidos. Copiar el backup SQLite consistente
    # conserva exactamente los bytes probados; el estado fallido quedó retenido.
    Copy-Item -LiteralPath $Backup -Destination $Database
    $RestoredSha256 = (Get-FileHash -LiteralPath $Database -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($RestoredSha256 -ne ([string]$Receipt.sha256).ToLowerInvariant()) {
        throw "la DB restaurada no coincide con el backup previo"
    }
}

$Action = New-ScheduledTaskAction `
    -Execute $SoulWeb `
    -Argument $ExpectedArguments `
    -WorkingDirectory $ReleaseRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 365)

$Old = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "python.exe" -and
    $_.ExecutablePath -ieq "$env:USERPROFILE\soul-core\Scripts\python.exe" -and
    $_.CommandLine -match "(^|\s)soul_web\.py(\s|$)"
})
$ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$ExistingTaskXml = if ($ExistingTask) { Export-ScheduledTask -TaskName $TaskName } else { $null }
$ExistingTaskWasRunning = $ExistingTask -and $ExistingTask.State -eq "Running"
$PreActivationPids = @(Get-ListenerProcessIds)
$CandidatePids = @()

# Nunca reclamar el puerto matando a ciegas. Cada listener previo debe poder
# ligarse byte por byte a la acción de la task anterior o al legacy conocido.
$ExistingAction = @(if ($ExistingTask) { $ExistingTask.Actions })
foreach ($PreActivationPid in $PreActivationPids) {
    $PreActivationProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$PreActivationPid"
    $OwnedByLegacy = @($Old | Where-Object { $_.ProcessId -eq $PreActivationPid }).Count -eq 1
    $OwnedByExistingTask = $false
    if ($PreActivationProcess -and $ExistingAction.Count -eq 1) {
        $PreviousExecute = [IO.Path]::GetFullPath([string]$ExistingAction[0].Execute)
        $PreviousArguments = ([string]$ExistingAction[0].Arguments).Trim()
        $PreviousPython = Join-Path (Split-Path -Parent $PreviousExecute) "python.exe"
        if (Test-Path -LiteralPath $PreviousPython -PathType Leaf) {
            $PreviousInterpreter = Get-InterpreterIdentity $PreviousPython
            $OwnedByExistingTask = `
                [IO.Path]::GetFullPath([string]$PreActivationProcess.ExecutablePath) -ieq $PreviousInterpreter -and `
                (Test-LauncherCommandLine `
                    ([string]$PreActivationProcess.CommandLine).Trim() `
                    $PreviousPython $PreviousExecute $PreviousArguments)
        }
    }
    if (-not ($OwnedByLegacy -or $OwnedByExistingTask)) {
        throw "puerto $Port ocupado por servicio ajeno PID $PreActivationPid (legacy=$OwnedByLegacy, task=$OwnedByExistingTask, actions=$($ExistingAction.Count)); activación abortada sin detenerlo"
    }
}

$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Backup = Join-Path $ReleaseRoot "alma_william.pre-soul-web-$Timestamp.db"
$ReceiptRaw = & $BackupTool $Database $Backup
if ($LASTEXITCODE -ne 0) { throw "falló el backup consistente" }
$Receipt = $ReceiptRaw | ConvertFrom-Json
if (-not $Receipt.ok -or $Receipt.quick_check -ne "ok" -or $Receipt.null_embeddings -ne 0) {
    throw "recibo de backup inválido"
}
$BaselineCount = [int]$Receipt.memory_count

try {
    if ($ExistingTask) { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }
    Stop-ProcessIds $PreActivationPids
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
        -Principal $Principal -Settings $Settings -Force | Out-Null
    foreach ($Process in $Old) {
        if ($Process.ProcessId -notin $PreActivationPids) {
            Stop-Process -Id $Process.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
    Start-ScheduledTask -TaskName $TaskName

    $Health = $null
    for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
        Start-Sleep -Seconds 1
        try {
            $Probe = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
            if ($Probe.ok -and $Probe.service -eq $ExpectedService -and
                $Probe.version -eq $ExpectedVersion -and $Probe.build_id -eq $ExpectedBuild -and
                [int64]$Probe.pid -gt 0 -and
                [IO.Path]::GetFullPath([string]$Probe.database) -ieq $Database) {
                $Health = $Probe
                break
            }
        } catch { }
    }
    if (-not $Health) { throw "health del candidato no probó identidad/version/build/pid/db" }

    $HealthPid = [int]$Health.pid
    if ($HealthPid -in $PreActivationPids) { throw "health respondió desde un PID anterior" }
    $CandidatePids = @($HealthPid)
    $ListenerPids = @(Get-ListenerProcessIds)
    if ($ListenerPids.Count -ne 1 -or $ListenerPids[0] -ne $HealthPid) {
        throw "listener no pertenece exclusivamente al PID del health"
    }
    $LiveProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$HealthPid"
    if (-not $LiveProcess -or $LiveProcess.ExecutablePath -ine $CandidateInterpreter -or
        -not (Test-LauncherCommandLine `
            ([string]$LiveProcess.CommandLine).Trim() `
            $CandidatePython $SoulWeb $ExpectedArguments)) {
        throw "listener no ejecuta el candidato/comando esperado"
    }

    $Status = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/status" -TimeoutSec 30
    if (-not $Status.ok -or [int]$Status.core_memory_count -ne $BaselineCount) {
        throw "conteo vivo divergente: esperado $BaselineCount, observado $($Status.core_memory_count)"
    }
    $Task = Get-ScheduledTask -TaskName $TaskName
    $TaskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
    $TaskAction = @($Task.Actions)
    if ($Task.State -ne "Running" -or $TaskInfo.LastTaskResult -notin @(0, 267009) -or
        $Task.Principal.RunLevel -ne "Limited" -or $Task.Principal.UserId -ne $env:USERNAME -or
        $TaskAction.Count -ne 1 -or $TaskAction[0].Execute -ine $SoulWeb -or
        $TaskAction[0].Arguments -cne $ExpectedArguments -or
        [IO.Path]::GetFullPath([string]$TaskAction[0].WorkingDirectory) -ine $ReleaseRoot) {
        throw "Scheduled Task no quedó Running/current-user/Limited con acción exacta"
    }
    [pscustomobject]@{
        ok = $true
        task = $TaskName
        backup = $Backup
        backup_sha256 = $Receipt.sha256
        core_memory_count = $Status.core_memory_count
        endpoint = "http://127.0.0.1:$Port"
        service = $Health.service
        version = $Health.version
        build_id = $Health.build_id
        pid = $HealthPid
        database = $Health.database
        executable = $LiveProcess.ExecutablePath
        runlevel = $Task.Principal.RunLevel
        user = $Task.Principal.UserId
    } | ConvertTo-Json -Compress
} catch {
    $ActivationError = $_
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $CandidatePids += @(Get-ListenerProcessIds)
    $CandidatePids += @(Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -ieq $SoulWeb
    } | Select-Object -ExpandProperty ProcessId)
    Stop-ProcessIds @($CandidatePids | Select-Object -Unique)
    Restore-DatabaseIfChanged
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    if ($ExistingTaskXml) {
        Register-ScheduledTask -TaskName $TaskName -Xml $ExistingTaskXml -Force | Out-Null
        if ($ExistingTaskWasRunning) { Start-ScheduledTask -TaskName $TaskName }
    } elseif ($Old.Count -gt 0) {
        Start-Process `
            -FilePath "$env:USERPROFILE\soul-core\Scripts\python.exe" `
            -ArgumentList "soul_web.py" `
            -WorkingDirectory "$env:USERPROFILE\soul-core\Scripts" `
            -WindowStyle Hidden
    }
    throw $ActivationError
}
