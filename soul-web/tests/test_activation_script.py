import shutil
import subprocess
from pathlib import Path

SCRIPT = (
    Path(__file__).parents[1] / "installer" / "Activate-Soul-Web.ps1"
).read_text(encoding="utf-8")


def test_activation_is_bound_to_release_identity_and_database() -> None:
    for literal in (
        '$ExpectedService = "soul-memory-web"',
        '$ExpectedVersion = "0.1.4"',
        '$ExpectedBuild = "soul-memory-web-0.1.4"',
        "$Probe.service -eq $ExpectedService",
        "$Probe.version -eq $ExpectedVersion",
        "$Probe.build_id -eq $ExpectedBuild",
        "[int64]$Probe.pid -gt 0",
        "[IO.Path]::GetFullPath([string]$Probe.database) -ieq $Database",
    ):
        assert literal in SCRIPT


def test_activation_rejects_old_or_wrong_listener_process() -> None:
    for literal in (
        "$HealthPid -in $PreActivationPids",
        "$ListenerPids.Count -ne 1",
        "$ListenerPids[0] -ne $HealthPid",
        "$LiveProcess.ExecutablePath -ine $CandidateInterpreter",
        "Test-LauncherCommandLine",
        "$CandidatePython $SoulWeb $ExpectedArguments",
    ):
        assert literal in SCRIPT


def test_preexisting_listener_must_be_owned_before_any_stop() -> None:
    ownership = SCRIPT.index("foreach ($PreActivationPid in $PreActivationPids)")
    reject_foreign = SCRIPT.index(
        'throw "puerto $Port ocupado por servicio ajeno PID $PreActivationPid',
        ownership,
    )
    backup = SCRIPT.index('$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"', reject_foreign)
    activation = SCRIPT.index("try {", backup)
    stop_task = SCRIPT.index("Stop-ScheduledTask -TaskName $TaskName", activation)
    stop_listener = SCRIPT.index("Stop-ProcessIds $PreActivationPids", stop_task)
    assert ownership < reject_foreign < backup < activation < stop_task < stop_listener
    for literal in (
        "$Old | Where-Object { $_.ProcessId -eq $PreActivationPid }",
        "$ExistingAction = @(if ($ExistingTask) { $ExistingTask.Actions })",
        "$ExistingAction.Count -eq 1",
        "$PreActivationProcess.ExecutablePath) -ieq $PreviousInterpreter",
        "$PreviousPython $PreviousExecute $PreviousArguments",
        "activación abortada sin detenerlo",
    ):
        assert literal in SCRIPT


def test_windows_console_script_identity_uses_venv_launcher_and_real_interpreter() -> None:
    for literal in (
        '$CandidatePython = Join-Path $Candidate "Scripts\\python.exe"',
        "$CandidateInterpreter = Get-InterpreterIdentity $CandidatePython",
        "print(os.path.realpath(getattr(sys,'_base_executable',sys.executable)))",
        "[regex]::Escape($VenvPython)",
        "[regex]::Escape($ConsoleScript)",
        "[regex]::Escape($Arguments)",
        "([string]$Observed) -match $Pattern",
    ):
        assert literal in SCRIPT


def test_activation_requires_running_task_with_exact_action() -> None:
    for literal in (
        '$Task.State -ne "Running"',
        '$Task.Principal.RunLevel -ne "Limited"',
        "$Task.Principal.UserId -ne $env:USERNAME",
        "$TaskAction.Count -ne 1",
        "$TaskAction[0].Execute -ine $SoulWeb",
        "$TaskAction[0].Arguments -cne $ExpectedArguments",
        "[IO.Path]::GetFullPath([string]$TaskAction[0].WorkingDirectory) -ine $ReleaseRoot",
    ):
        assert literal in SCRIPT


def test_rollback_stops_candidate_before_database_restore_and_retains_evidence() -> None:
    catch = SCRIPT.index("} catch {")
    stop = SCRIPT.index("Stop-ProcessIds @($CandidatePids", catch)
    restore = SCRIPT.index("Restore-DatabaseIfChanged", stop)
    old_task = SCRIPT.index("Register-ScheduledTask -TaskName $TaskName -Xml $ExistingTaskXml", restore)
    assert catch < stop < restore < old_task
    assert '$ExistingTaskWasRunning = $ExistingTask -and $ExistingTask.State -eq "Running"' in SCRIPT
    assert "if ($ExistingTaskWasRunning) { Start-ScheduledTask -TaskName $TaskName }" in SCRIPT
    assert "Move-Item -LiteralPath $Database -Destination $FailedDatabase" in SCRIPT
    assert "Copy-Item -LiteralPath $Backup -Destination $Database" in SCRIPT
    assert "Get-FileHash -LiteralPath $Database -Algorithm SHA256" in SCRIPT
    assert "Remove-Item" not in SCRIPT


def test_powershell_parser_accepts_script_when_available() -> None:
    # CI Linux normally lacks PowerShell. The release gate on Windows runs this
    # parser again before activation; this test documents the exact command.
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh is None:
        return
    command = (
        "$errors=$null; [void][System.Management.Automation.Language.Parser]::"
        "ParseFile($args[0],[ref]$null,[ref]$errors); "
        "if($errors.Count){$errors|%%{$_.Message}; exit 1}"
    )
    script = Path(__file__).parents[1] / "installer" / "Activate-Soul-Web.ps1"
    subprocess.run(
        [pwsh, "-NoProfile", "-Command", command, str(script)],
        check=True,
    )
