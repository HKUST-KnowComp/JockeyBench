#requires -Version 5.1
# Standalone tests; no WSL startup, data, models, or benchmark imports.
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'jockeybench.ps1')

function Assert-Equal {
    param($Actual, $Expected, [string] $Label)
    if ($Actual -cne $Expected) {
        throw ('Assertion failed: ' + $Label + '; actual=' + $Actual + '; expected=' + $Expected)
    }
}

$unicode = [string][char]0x4E2D + [char]0x6587
$cases = @('', 'plain', 'two words', 'quote"inside', 'backslash\', 'slashes\\"quoted', '; echo unsafe', '$(do not execute)', $unicode, "line1`nline2")
$project = '/home/' + 'synthetic/Jockey Bench'
$payload = New-JockeyBenchPayload -Project $project -Operation run -Arguments $cases
$decoded = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($payload)) | ConvertFrom-Json
Assert-Equal $decoded.project $project 'project path'
Assert-Equal $decoded.arguments.Count $cases.Count 'argument count'
for ($i = 0; $i -lt $cases.Count; $i++) {
    Assert-Equal $decoded.arguments[$i] $cases[$i] ('payload argument ' + $i)
}
$empty = New-JockeyBenchPayload -Project $project -Operation run -Arguments @()
$decodedEmpty = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($empty)) | ConvertFrom-Json
Assert-Equal $decodedEmpty.arguments.Count 0 'empty argument array'
Assert-Equal (ConvertTo-JockeyBenchWindowsArgument '') '""' 'empty Windows argument'
Assert-Equal (ConvertTo-JockeyBenchWindowsArgument 'a"b') '"a\"b"' 'embedded quote'
Assert-Equal (ConvertTo-JockeyBenchWindowsArgument 'a\') '"a\\"' 'trailing backslash'

if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
    # Compile a tiny argv receiver using the .NET Framework compiler already
    # present on Windows. This tests the actual Windows process parser under
    # both Windows PowerShell 5.1 and PowerShell 7, with no shell in between.
    $directory = Join-Path ([IO.Path]::GetTempPath()) ('jockeybench-argv-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $directory | Out-Null
    try {
        $source = Join-Path $directory 'argv.cs'
        $executable = Join-Path $directory 'argv.exe'
        [IO.File]::WriteAllText($source, @'
using System;
using System.Text;
public class ArgDump {
    public static void Main(string[] args) {
        foreach (string arg in args) Console.WriteLine(Convert.ToBase64String(Encoding.UTF8.GetBytes(arg)));
    }
}
'@)
        $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
        if (-not (Test-Path -LiteralPath $compiler)) {
            $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
        }
        if (-not (Test-Path -LiteralPath $compiler)) {
            throw 'The standalone Windows argv test requires the installed .NET Framework compiler.'
        }
        & $compiler /nologo ('/out:' + $executable) $source
        if ($LASTEXITCODE -ne 0) { throw 'Could not compile the synthetic argv receiver.' }
        $start = New-Object System.Diagnostics.ProcessStartInfo
        $start.FileName = $executable
        $start.Arguments = @($cases | ForEach-Object { ConvertTo-JockeyBenchWindowsArgument $_ }) -join ' '
        $start.UseShellExecute = $false
        $start.RedirectStandardOutput = $true
        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $start
        try {
            [void] $process.Start()
            $actual = $process.StandardOutput.ReadToEnd()
            $process.WaitForExit()
            Assert-Equal $process.ExitCode 0 'argv receiver exit'
        }
        finally { $process.Dispose() }
        $expected = (@($cases | ForEach-Object { [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($_)) }) -join [Environment]::NewLine) + [Environment]::NewLine
        Assert-Equal $actual $expected 'real Windows argv round trip'
    }
    finally { Remove-Item -LiteralPath $directory -Recurse -Force }
}
Write-Output 'PowerShell launcher payload and argv checks passed.'
