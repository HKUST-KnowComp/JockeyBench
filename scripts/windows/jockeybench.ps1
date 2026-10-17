#requires -Version 5.1
<#
.SYNOPSIS
Run the Linux JockeyBench environment through WSL 2 from Windows PowerShell.
.DESCRIPTION
Keep this script beside dispatch.py. The Linux checkout must live on the WSL
ext4 filesystem under /home. WSL and the distribution must already be installed.
Credentials remain configured in Linux; Windows environment variables are not
copied by this launcher. Pass benchmark arguments as an explicit string array.
.EXAMPLE
.\scripts\windows\jockeybench.ps1 -ProjectPath $LinuxProject -Action run -BenchmarkArgs @('research', '--help')
#>
[CmdletBinding()]
param(
    [ValidatePattern('\A[A-Za-z0-9_.-]+\z')]
    [string] $Distribution = 'Ubuntu-22.04',

    [string] $ProjectPath,

    [ValidateSet('setup', 'check', 'run')]
    [string] $Action = 'run',

    [AllowEmptyCollection()]
    [AllowEmptyString()]
    [string[]] $BenchmarkArgs = @()
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function ConvertTo-JockeyBenchWindowsArgument {
    param([AllowEmptyString()][Parameter(Mandatory = $true)][string] $Value)
    # ProcessStartInfo.ArgumentList is unavailable in Windows PowerShell 5.1.
    # Quote each argument using Windows argv rules, including empty strings,
    # embedded quotes, and backslashes immediately before a quote or the end.
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function New-JockeyBenchPayload {
    param(
        [string] $Project,
        [string] $Operation,
        [AllowEmptyCollection()][AllowEmptyString()][string[]] $Arguments = @()
    )
    if ([string]::IsNullOrWhiteSpace($Project) -or -not $Project.StartsWith('/home/')) {
        throw 'ProjectPath must be an absolute Linux project path inside /home.'
    }
    if ($Project.IndexOf([char]0) -ge 0) {
        throw 'ProjectPath cannot contain a NUL character.'
    }
    foreach ($argument in $Arguments) {
        if ($null -eq $argument -or $argument.IndexOf([char]0) -ge 0) {
            throw 'BenchmarkArgs must contain strings without NUL characters.'
        }
    }
    $payload = [ordered]@{
        protocol = 'jockeybench-wsl-launcher-v1'
        project = $Project
        action = $Operation
        arguments = [object[]]@($Arguments)
    }
    $json = ConvertTo-Json -InputObject $payload -Compress -Depth 6
    return [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
}

function Get-JockeyBenchWslPath {
    param(
        [scriptblock] $CommandLookup = { Get-Command 'wsl.exe' -CommandType Application -ErrorAction SilentlyContinue }
    )
    # Windows may expose both the system launcher and the Store-installed WSL.
    # Preserve command/PATH precedence, but pass exactly one executable path.
    $command = & $CommandLookup | Select-Object -First 1
    if ($null -eq $command) {
        throw 'wsl.exe was not found. Install WSL 2 and Ubuntu 22.04 first; this launcher does not install Windows components.'
    }
    return [string] $command.Source
}

function New-JockeyBenchWslCommandLine {
    param(
        [string] $Distro,
        [string] $Python,
        [string] $Payload
    )
    if ([string]::IsNullOrEmpty($Distro) -or -not [regex]::IsMatch($Distro, '\A[A-Za-z0-9_.-]+\z')) {
        throw 'Distribution must contain only ASCII letters, digits, underscores, dots, or hyphens.'
    }
    # WSL parses its own control flags before invoking CommandLineToArgvW.
    # Quoted "--distribution" is treated as a Linux shell command, not a flag.
    # Keep this validated ASCII prefix raw; quote data only after --exec.
    # Microsoft WslClient.cpp (WslMain) and helpers.cpp (ParseArgument):
    # https://github.com/microsoft/WSL/blob/ce886386d3093e339593e2729a0721972546fa69/src/windows/common/WslClient.cpp#L1580
    $prefix = '--distribution ' + $Distro + ' --exec /usr/bin/python3 -c '
    return $prefix + (ConvertTo-JockeyBenchWindowsArgument $Python) + ' ' + (ConvertTo-JockeyBenchWindowsArgument $Payload)
}

function Invoke-JockeyBenchWsl {
    param(
        [ValidatePattern('\A[A-Za-z0-9_.-]+\z')]
        [string] $Distro,
        [string] $Project,
        [string] $Operation,
        [AllowEmptyCollection()][AllowEmptyString()][string[]] $Arguments = @()
    )
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'This launcher runs on Windows. On Linux or macOS, use .venv/bin/jockeybench directly.'
    }
    $payload = New-JockeyBenchPayload -Project $Project -Operation $Operation -Arguments $Arguments
    $wslPath = Get-JockeyBenchWslPath
    $bootstrapPath = Join-Path $PSScriptRoot 'dispatch.py'
    if (-not (Test-Path -LiteralPath $bootstrapPath -PathType Leaf)) {
        throw 'dispatch.py is missing. Keep it beside jockeybench.ps1 from the same checkout.'
    }
    $bootstrap = [Convert]::ToBase64String([IO.File]::ReadAllBytes($bootstrapPath))
    $python = "import base64;exec(compile(base64.b64decode('$bootstrap'),'<jockeybench-wsl-launcher>','exec'))"
    $commandLine = New-JockeyBenchWslCommandLine -Distro $Distro -Python $python -Payload $payload
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $wslPath
    $start.Arguments = $commandLine
    $start.UseShellExecute = $false
    # Do not propagate Windows secrets through a caller's WSLENV list. This
    # changes only this child process; the user's Windows environment is intact.
    $start.EnvironmentVariables['WSLENV'] = ''
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) {
            throw 'Could not start WSL. Check that the selected distribution is installed and uses WSL 2.'
        }
        $process.WaitForExit()
        return $process.ExitCode
    }
    finally {
        $process.Dispose()
    }
}

# Dot-sourcing loads helpers for the standalone bridge tests without starting WSL.
if ($MyInvocation.InvocationName -ne '.') {
    try {
        $code = Invoke-JockeyBenchWsl -Distro $Distribution -Project $ProjectPath -Operation $Action -Arguments $BenchmarkArgs
        exit $code
    }
    catch {
        [Console]::Error.WriteLine('JockeyBench Windows launcher: ' + $_.Exception.Message)
        exit 125
    }
}
