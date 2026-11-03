# Create a local, machine-specific Windows shortcut beside the source.
# /D ignores Command Prompt AutoRun hooks. /K keeps the shell open even after
# the command file exits, so a startup error cannot vanish with its console.
# This creates only a file: no registry, security or automatic-startup changes.
$ErrorActionPreference = 'Stop'
$inventoryCommand = Join-Path $PSScriptRoot 'Start Inventory.cmd'
$inventoryShortcutPath = Join-Path $PSScriptRoot 'Open Inventory.lnk'
$inventoryShell = New-Object -ComObject WScript.Shell
$inventoryShortcut = $inventoryShell.CreateShortcut($inventoryShortcutPath)
$inventoryShortcut.TargetPath = Join-Path $env:SystemRoot 'System32\cmd.exe'
$inventoryShortcut.Arguments = '/D /K call "' + $inventoryCommand + '"'
$inventoryShortcut.WorkingDirectory = $PSScriptRoot
$inventoryShortcut.Description = 'Open Local Inventory and keep startup details visible'
$inventoryShortcut.IconLocation = $inventoryShortcut.TargetPath + ',0'
$inventoryShortcut.Save()
Write-Output "Persistent launcher created: $inventoryShortcutPath"
