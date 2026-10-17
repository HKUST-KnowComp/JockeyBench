# Windows through WSL2

Both Program and Active Research run on Windows through **WSL2 with Ubuntu 22.04**.
The benchmark executes inside Linux using bubblewrap and libseccomp; native Windows Python and
WSL1 are unsupported. The Windows launcher works with Windows PowerShell 5.1 and PowerShell 7.

## Install WSL and Ubuntu

Use Windows 11 or Windows 10 version 2004/build 19041 or later, with virtualization enabled.
Run this once in an administrator PowerShell window, and restart Windows if prompted:

```powershell
wsl --install -d Ubuntu-22.04
```

Open Ubuntu and finish creating your ordinary Linux user. Confirm the distribution uses version 2:

```powershell
wsl --list --verbose
# Only if Ubuntu-22.04 currently shows version 1:
wsl --set-version Ubuntu-22.04 2
```

These prerequisites and installation steps follow [Microsoft's WSL installation guide](https://learn.microsoft.com/en-us/windows/wsl/install)
and [command reference](https://learn.microsoft.com/en-us/windows/wsl/basic-commands).
If Windows itself runs inside a virtual machine, its host must expose nested virtualization.

## Install JockeyBench inside Linux

In the Ubuntu terminal, clone into the Linux home filesystem. Do not put the checkout, Python
environment or runtime storage on a Windows-mounted path such as `/mnt/c`.
This also follows [Microsoft's filesystem guidance](https://learn.microsoft.com/en-us/windows/wsl/filesystems).

```bash
sudo apt-get update
sudo apt-get install -y git
cd "$HOME"
git clone https://github.com/HKUST-KnowComp/JockeyBench.git
cd JockeyBench
bash scripts/windows/setup.sh --install-system
```

The optional `--install-system` flag installs Ubuntu dependencies with `sudo`: `python3`,
`python3-venv`, `git`, `ca-certificates`, `bubblewrap`, `libseccomp2`, `libarchive-tools`, `lsof`, and
`build-essential`. If these are already installed, omit the flag. Run the script as your ordinary
Linux user, not root.

The installer uses `uv==0.13.1` to install Python 3.13.2, creates a fresh `.venv`, installs the locked
dependencies and both settings, and finishes by running synthetic native sandbox fixtures.
It refuses an existing environment or a checkout designated as an experiment host. To update,
install a fresh checkout with a new environment. The installer never creates `.experiment-host`.

After successful setup, either setting is available directly in Ubuntu:

```bash
.venv/bin/jockeybench program --help
.venv/bin/jockeybench research --help
# Repeat the installation's sandbox checks without changing the environment:
bash scripts/windows/setup.sh --check
```

## Start from PowerShell

Keep a Windows copy of `scripts/windows/jockeybench.ps1` and its adjacent `dispatch.py` from the
same revision as the Linux checkout. For example, clone the repository on Windows as well, then
run the following from that Windows repository root. Replace the placeholder with the Linux path
printed by `pwd` in the Ubuntu checkout:

```powershell
$LinuxProject = '/home/<linux-user>/JockeyBench'
& .\scripts\windows\jockeybench.ps1 -Distribution Ubuntu-22.04 `
    -ProjectPath $LinuxProject -Action run -BenchmarkArgs @('program', '--help')
& .\scripts\windows\jockeybench.ps1 -Distribution Ubuntu-22.04 `
    -ProjectPath $LinuxProject -Action run -BenchmarkArgs @('research', '--help')
```

Use `-Action setup` for a fresh checkout whose Ubuntu system dependencies are already installed;
use `-Action check` to repeat the existing environment's synthetic sandbox fixtures. To include the
optional Ubuntu dependency installation from PowerShell, use
`-Action setup -BenchmarkArgs @('--install-system')` and respond to Ubuntu's sudo prompt.
Arguments must be an explicit string array, including paths with spaces. The launcher preserves
argument boundaries and forwards the Linux process's exit code.

The launcher uses Ubuntu's default user. Keep that user nonroot, and keep the project on the Linux
filesystem under its home directory. Provider files, database paths, credentials and output paths
are Linux inputs; the launcher does not copy Windows environment variables or transfer files.
An environment variable exported in one Ubuntu terminal is not inherited by a later PowerShell
launch. For provider-backed runs, export the credentials and run `.venv/bin/jockeybench` in that
same Ubuntu terminal. The launcher also clears `WSLENV` for its child process.
Real-data and model runs still require a separately authorized and provisioned experiment host.
Follow the normal [Program](REPRODUCING.md) or [Research](RESEARCH.md) configuration inside that host.

## What CI verifies

The Windows workflow targets **Windows Server 2025**. It checks the launcher with both PowerShell
versions, imports a SHA-256-pinned Ubuntu 22.04.5 image into WSL2, verifies that a WSL2 kernel actually
starts, and bootstraps an ordinary user's Linux checkout through the launcher. It then runs both
CLI entrypoints, the full synthetic test suite, native sandbox fixtures and source-provenance checks.
The image comes from [Canonical's Ubuntu releases](https://releases.ubuntu.com/jammy/), with its hash
also published in [Microsoft's distribution list](https://github.com/microsoft/WSL/blob/master/distributions/DistributionInfo.json).

Windows 10/11 are deployment targets; this CI is not certification of every desktop, CPU or virtual
machine. GitHub's [Windows 2025 image](https://github.com/actions/runner-images/blob/main/images/windows/Windows2025-Readme.md)
includes WSL2, but GitHub does not guarantee nested virtualization across its hosted runner service.
See [GitHub's runner limitations](https://docs.github.com/en/actions/concepts/runners/github-hosted-runners).
If WSL2 or sandbox isolation cannot start, the job fails; it does not skip or fall back to unsandboxed
execution. No CI job uses real JRDB data, provider credentials or an experiment-host marker.

Moving a trajectory between macOS, native Linux and WSL2 requires a fresh run and warmup. This
release does not establish identical predictions, CPU accounting or scores across environments,
and does not reproduce the paper's experiments on Windows.
